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
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

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
        """Create the file and schema if needed."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row["value"]) != SCHEMA_VERSION:
                raise RuntimeError(
                    f"{self.path} has schema version {row['value']}, this code expects "
                    f"{SCHEMA_VERSION}; it is an index, so it can be deleted and rebuilt"
                )

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
    ) -> None:
        """Register a run (being mined by the app, or found on disk)."""
        self._run(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                origin,
                status,
                dataset_id,
                preset,
                _dumps(cleaning),
                evidence_count,
                created_at or utc_now(),
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
        """Set ``status``, ``preset`` and/or ``evidence_count``."""
        allowed = {"status", "preset", "evidence_count"}
        if not fields or set(fields) - allowed:
            raise ValueError(f"can only update {sorted(allowed)}, got {sorted(fields)}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._run(f"UPDATE runs SET {assignments} WHERE run_id = ?", (*fields.values(), run_id))

    # --- agent runs ----------------------------------------------------------

    def add_agent_run(self, run_id: str, segment: str, agent_run_id: str, model_id: str) -> None:
        """Register an agent run as soon as the graph has created it."""
        now = utc_now()
        self._run(
            "INSERT INTO agent_runs VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (run_id, segment, agent_run_id, model_id, now, now),
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
