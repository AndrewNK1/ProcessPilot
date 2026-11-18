"""The API's SQLite database: an index of runs and jobs, and the decision log.

It indexes datasets, runs, background jobs and agent runs, and holds the log
of analyst decisions.

WHY an index and not a copy (user decision): the run folders under
``outputs/runs`` stay the single source of truth for evidence, models, agent
logs and LangGraph checkpoints, exactly as notebooks and tests produce them.
SQLite holds only what has no other home: which datasets and runs exist and
how they were made, the state of background jobs, and every approve/reject
decision with who made it and when. That last table is the audit trail
behind "a human analyst approves or rejects every proposal".

One short-lived connection per call: jobs run in worker threads, and
``sqlite3`` connections must not be shared across threads. WAL mode lets the
UI read while a job writes.

Schema versions are applied as steps (``_MIGRATIONS``), never by rebuilding:
the decision log is an audit trail and must survive an upgrade. A new file
gets the version-1 tables and then every step, so a fresh and an upgraded
database end up identical. Before upgrading an existing file, a copy is
written next to it (``<name>.bak-v<old>``).

History:

1. datasets, runs, agent_runs, decisions, jobs.
2. (2026-11-18, evaluation Phase 4) ``purpose`` (dev | evaluation) and
   ``campaign_id`` on runs and agent_runs, ``launched_by`` (app | batch) and
   ``condition`` (processpilot | baseline) on agent_runs; every existing row
   becomes ``dev``. New tables ``eval_campaigns`` and ``eval_runs``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

PURPOSES = ("dev", "evaluation")

# The version-1 tables; later columns and tables come from _MIGRATIONS.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS datasets (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('preset', 'upload')),
    name         TEXT NOT NULL,
    preset       TEXT NOT NULL,            -- preset name; base preset for uploads
    source_path  TEXT NOT NULL,
    mapping_json TEXT,                     -- NULL until the analyst confirms one
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    origin         TEXT NOT NULL CHECK (origin IN ('app', 'disk')),
    status         TEXT NOT NULL CHECK (status IN ('mining', 'mined', 'failed')),
    dataset_id     TEXT REFERENCES datasets (id),
    preset         TEXT,                   -- NULL for runs found on disk until chosen
    cleaning_json  TEXT,
    evidence_count INTEGER,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id       TEXT NOT NULL REFERENCES runs (run_id),
    segment      TEXT NOT NULL,
    agent_run_id TEXT NOT NULL,
    model_id     TEXT NOT NULL,
    status       TEXT NOT NULL,            -- mirrors AgentState.status
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (run_id, segment, agent_run_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               TEXT NOT NULL,
    segment              TEXT NOT NULL,
    agent_run_id         TEXT NOT NULL,
    round                INTEGER NOT NULL,
    decision             TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    comment              TEXT,
    analyst              TEXT,
    proposal_titles_json TEXT NOT NULL,
    decided_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL
                CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'interrupted')),
    target_json TEXT NOT NULL,             -- what the job works on (ids)
    message     TEXT,                      -- latest progress line
    error       TEXT,
    result_json TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
"""


_MIGRATIONS: dict[int, str] = {
    2: """
ALTER TABLE runs ADD COLUMN purpose TEXT NOT NULL DEFAULT 'dev'
    CHECK (purpose IN ('dev', 'evaluation'));
ALTER TABLE runs ADD COLUMN campaign_id TEXT;
ALTER TABLE agent_runs ADD COLUMN purpose TEXT NOT NULL DEFAULT 'dev'
    CHECK (purpose IN ('dev', 'evaluation'));
ALTER TABLE agent_runs ADD COLUMN campaign_id TEXT;
ALTER TABLE agent_runs ADD COLUMN launched_by TEXT NOT NULL DEFAULT 'app'
    CHECK (launched_by IN ('app', 'batch'));
ALTER TABLE agent_runs ADD COLUMN condition TEXT NOT NULL DEFAULT 'processpilot'
    CHECK (condition IN ('processpilot', 'baseline'));

CREATE TABLE eval_campaigns (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    manifest_json   TEXT NOT NULL,          -- copy of outputs/eval/<id>/manifest.json
    manifest_sha256 TEXT NOT NULL,
    status          TEXT NOT NULL
                    CHECK (status IN ('draft', 'sealed', 'running', 'finished'))
);

CREATE TABLE eval_runs (
    campaign_id    TEXT NOT NULL REFERENCES eval_campaigns (id),
    segment        TEXT NOT NULL,
    condition      TEXT NOT NULL CHECK (condition IN ('processpilot', 'baseline')),
    repetition     INTEGER NOT NULL CHECK (repetition BETWEEN 1 AND 5),
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'running', 'done', 'failed', 'quota_stopped')),
    outcome        TEXT,                    -- e.g. reached_review, failed_validation
    attempt_count  INTEGER NOT NULL DEFAULT 0,
    agent_run_id   TEXT,
    baseline_path  TEXT,
    calls_used     INTEGER,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    started_at     TEXT,
    finished_at    TEXT,
    PRIMARY KEY (campaign_id, segment, condition, repetition)
);
""",
}


def utc_now() -> str:
    """ISO-8601 UTC timestamp, the only time format stored."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _loads(value: str | None) -> Any:
    return None if value is None else json.loads(value)


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


class Database:
    """Typed access to the API's SQLite file. Rows come back as plain dicts."""

    def __init__(self, path: Path) -> None:
        """Create the file and schema if needed; upgrade an older schema step by step."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is None:  # a new file: the version-1 tables were just created
                conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
                version, existing = 1, False
            else:
                version, existing = int(row["value"]), True
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"{self.path} has schema version {version}, newer than this code's "
                f"{SCHEMA_VERSION}; update the code instead of downgrading the database"
            )
        if version < SCHEMA_VERSION:
            if existing:
                self.backup_path = self._backup(version)
            self._migrate(version)

    backup_path: Path | None = None

    def _backup(self, version: int) -> Path:
        """Copy the database before upgrading it (SQLite's backup API, safe under WAL)."""
        target = self.path.with_name(f"{self.path.name}.bak-v{version}")
        if target.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            target = self.path.with_name(f"{self.path.name}.bak-v{version}-{stamp}")
        src = sqlite3.connect(self.path)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return target

    def _migrate(self, version: int) -> None:
        """Apply every step after ``version``; each step is one transaction."""
        for step in range(version + 1, SCHEMA_VERSION + 1):
            conn = sqlite3.connect(self.path, timeout=30)
            try:
                # executescript commits on its own, so the step brackets itself.
                conn.executescript(
                    "BEGIN;\n"
                    + _MIGRATIONS[step]
                    + f"\nUPDATE meta SET value = '{step}' WHERE key = 'schema_version';"
                    + "\nCOMMIT;"
                )
            except sqlite3.Error:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    def _run(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._connect() as conn:
            conn.execute(sql, params)

    # --- datasets ------------------------------------------------------------

    def add_dataset(
        self,
        dataset_id: str,
        kind: str,
        name: str,
        preset: str,
        source_path: str,
        mapping: dict[str, Any] | None,
    ) -> None:
        """Register a dataset (a preset's source or an uploaded file)."""
        self._run(
            "INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?, ?)",
            (dataset_id, kind, name, preset, source_path, _dumps(mapping), utc_now()),
        )

    def dataset(self, dataset_id: str) -> dict[str, Any] | None:
        """One dataset, with ``mapping`` decoded."""
        row = self._one("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
        if row is not None:
            row["mapping"] = _loads(row.pop("mapping_json"))
        return row

    def datasets(self) -> list[dict[str, Any]]:
        """All datasets, newest first."""
        rows = self._all("SELECT * FROM datasets ORDER BY created_at DESC, id DESC")
        for row in rows:
            row["mapping"] = _loads(row.pop("mapping_json"))
        return rows

    def set_mapping(self, dataset_id: str, mapping: dict[str, Any]) -> None:
        """Record the analyst-confirmed column mapping."""
        self._run(
            "UPDATE datasets SET mapping_json = ? WHERE id = ?", (_dumps(mapping), dataset_id)
        )

    # --- runs ----------------------------------------------------------------

    def add_run(
        self,
        run_id: str,
        origin: str,
        status: str,
        dataset_id: str | None = None,
        preset: str | None = None,
        cleaning: dict[str, Any] | None = None,
        evidence_count: int | None = None,
        created_at: str | None = None,
        purpose: str = "dev",
        campaign_id: str | None = None,
    ) -> None:
        """Register a run (being mined by the app, or found on disk)."""
        self._run(
            "INSERT INTO runs (run_id, origin, status, dataset_id, preset, cleaning_json, "
            "evidence_count, created_at, purpose, campaign_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                origin,
                status,
                dataset_id,
                preset,
                _dumps(cleaning),
                evidence_count,
                created_at or utc_now(),
                purpose,
                campaign_id,
            ),
        )

    def run(self, run_id: str) -> dict[str, Any] | None:
        """One run, with ``cleaning`` decoded."""
        row = self._one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        if row is not None:
            row["cleaning"] = _loads(row.pop("cleaning_json"))
        return row

    def runs(self) -> list[dict[str, Any]]:
        """All runs, newest first (run ids sort by time)."""
        rows = self._all("SELECT * FROM runs ORDER BY run_id DESC")
        for row in rows:
            row["cleaning"] = _loads(row.pop("cleaning_json"))
        return rows

    def update_run(self, run_id: str, **fields: Any) -> None:
        """Set ``status``, ``preset``, ``evidence_count``, ``purpose`` or ``campaign_id``."""
        allowed = {"status", "preset", "evidence_count", "purpose", "campaign_id"}
        if not fields or set(fields) - allowed:
            raise ValueError(f"can only update {sorted(allowed)}, got {sorted(fields)}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._run(f"UPDATE runs SET {assignments} WHERE run_id = ?", (*fields.values(), run_id))

    # --- agent runs ----------------------------------------------------------

    def add_agent_run(
        self,
        run_id: str,
        segment: str,
        agent_run_id: str,
        model_id: str,
        purpose: str = "dev",
        campaign_id: str | None = None,
        launched_by: str = "app",
        condition: str = "processpilot",
    ) -> None:
        """Register an agent run as soon as the graph has created it."""
        now = utc_now()
        self._run(
            "INSERT INTO agent_runs (run_id, segment, agent_run_id, model_id, status, "
            "created_at, updated_at, purpose, campaign_id, launched_by, condition) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                segment,
                agent_run_id,
                model_id,
                now,
                now,
                purpose,
                campaign_id,
                launched_by,
                condition,
            ),
        )

    def set_agent_status(self, run_id: str, segment: str, agent_run_id: str, status: str) -> None:
        """Mirror the checkpointed ``AgentState.status``."""
        self._run(
            "UPDATE agent_runs SET status = ?, updated_at = ? "
            "WHERE run_id = ? AND segment = ? AND agent_run_id = ?",
            (status, utc_now(), run_id, segment, agent_run_id),
        )

    def agent_runs(self, run_id: str, segment: str | None = None) -> list[dict[str, Any]]:
        """Agent runs of a mining run (optionally one segment), newest first."""
        if segment is None:
            return self._all(
                "SELECT * FROM agent_runs WHERE run_id = ? ORDER BY agent_run_id DESC", (run_id,)
            )
        return self._all(
            "SELECT * FROM agent_runs WHERE run_id = ? AND segment = ? ORDER BY agent_run_id DESC",
            (run_id, segment),
        )

    def agent_run(self, run_id: str, segment: str, agent_run_id: str) -> dict[str, Any] | None:
        """One agent run."""
        return self._one(
            "SELECT * FROM agent_runs WHERE run_id = ? AND segment = ? AND agent_run_id = ?",
            (run_id, segment, agent_run_id),
        )

    # --- evaluation campaigns ----------------------------------------------

    def add_campaign(
        self, campaign_id: str, manifest: dict[str, Any], manifest_sha256: str, status: str
    ) -> None:
        """Register a campaign with a copy of its manifest (the file stays the source)."""
        self._run(
            "INSERT INTO eval_campaigns (id, created_at, manifest_json, manifest_sha256, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (campaign_id, utc_now(), _dumps(manifest), manifest_sha256, status),
        )

    def update_campaign(
        self, campaign_id: str, manifest: dict[str, Any], manifest_sha256: str, status: str
    ) -> None:
        """Replace the manifest copy and status (e.g. on sealing)."""
        self._run(
            "UPDATE eval_campaigns SET manifest_json = ?, manifest_sha256 = ?, status = ? "
            "WHERE id = ?",
            (_dumps(manifest), manifest_sha256, status, campaign_id),
        )

    def campaign(self, campaign_id: str) -> dict[str, Any] | None:
        """One campaign, with ``manifest`` decoded."""
        row = self._one("SELECT * FROM eval_campaigns WHERE id = ?", (campaign_id,))
        if row is not None:
            row["manifest"] = _loads(row.pop("manifest_json"))
        return row

    def campaigns(self) -> list[dict[str, Any]]:
        """All campaigns, newest first (manifest not decoded)."""
        return self._all(
            "SELECT id, created_at, manifest_sha256, status FROM eval_campaigns "
            "ORDER BY created_at DESC, id DESC"
        )

    # --- decisions -----------------------------------------------------------

    def add_decision(
        self,
        run_id: str,
        segment: str,
        agent_run_id: str,
        round_: int,
        decision: str,
        comment: str | None,
        analyst: str | None,
        proposal_titles: list[str],
    ) -> int:
        """Append one analyst decision; returns its id. Decisions are never edited."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO decisions (run_id, segment, agent_run_id, round, decision, comment, "
                "analyst, proposal_titles_json, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    segment,
                    agent_run_id,
                    round_,
                    decision,
                    comment,
                    analyst,
                    _dumps(proposal_titles),
                    utc_now(),
                ),
            )
            return int(cur.lastrowid or 0)

    def decisions(
        self, run_id: str | None = None, segment: str | None = None, agent_run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Decisions in the order they were made, optionally filtered."""
        clauses, params = [], []
        for column, value in (
            ("run_id", run_id),
            ("segment", segment),
            ("agent_run_id", agent_run_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._all(f"SELECT * FROM decisions {where} ORDER BY id", tuple(params))
        for row in rows:
            row["proposal_titles"] = _loads(row.pop("proposal_titles_json"))
        return rows

    # --- jobs ----------------------------------------------------------------

    def add_job(self, job_id: str, kind: str, target: dict[str, Any]) -> None:
        """Register a queued job."""
        self._run(
            "INSERT INTO jobs (id, kind, status, target_json, created_at) "
            "VALUES (?, ?, 'queued', ?, ?)",
            (job_id, kind, _dumps(target), utc_now()),
        )

    def update_job(self, job_id: str, **fields: Any) -> None:
        """Set status/message/error/result and the matching timestamps."""
        allowed = {"status", "message", "error", "result"}
        if not fields or set(fields) - allowed:
            raise ValueError(f"can only update {sorted(allowed)}, got {sorted(fields)}")
        if "result" in fields:
            fields["result_json"] = _dumps(fields.pop("result"))
        status = fields.get("status")
        if status == "running":
            fields["started_at"] = utc_now()
        elif status in ("succeeded", "failed", "interrupted"):
            fields["finished_at"] = utc_now()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._run(f"UPDATE jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))

    def job(self, job_id: str) -> dict[str, Any] | None:
        """One job, with ``target`` and ``result`` decoded."""
        row = self._one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return None if row is None else _decode_job(row)

    def jobs(self, active_only: bool = False) -> list[dict[str, Any]]:
        """Jobs, newest first; ``active_only`` keeps queued and running ones."""
        where = "WHERE status IN ('queued', 'running')" if active_only else ""
        return [
            _decode_job(r)
            for r in self._all(f"SELECT * FROM jobs {where} ORDER BY created_at DESC")
        ]

    def interrupt_unfinished_jobs(self) -> int:
        """Mark jobs a previous server process left queued/running; returns how many.

        WHY: worker threads die with the process, so such a job will never
        finish. Its run or agent run is recovered from disk/checkpoints.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'interrupted', finished_at = ?, "
                "error = 'the API server stopped while this job was queued or running' "
                "WHERE status IN ('queued', 'running')",
                (utc_now(),),
            )
            return cur.rowcount


def _decode_job(row: dict[str, Any]) -> dict[str, Any]:
    row["target"] = _loads(row.pop("target_json"))
    row["result"] = _loads(row.pop("result_json"))
    return row
