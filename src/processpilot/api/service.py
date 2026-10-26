"""What the API does, without HTTP: datasets, runs, agent runs, decisions.

WHY a service layer under thin routes: every operation here is a sequence of
existing pipeline calls plus bookkeeping in SQLite, and it must behave the
same whether it is called by FastAPI, a test or a script. Routes only
translate requests and the three exception types below into HTTP.

Nothing in this module computes a metric or talks to a model directly: it
calls ``pipeline`` (ingest, validation, mining) and ``AgentRunner`` (agent),
and reads what they wrote.

Every id that comes from a request (dataset id, run id, segment slug, agent
run id) is checked against the database or the run's manifest before it is
used in a path, so a request cannot name a file outside the workspace.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from pydantic import ValidationError

from processpilot.agent.catalogue import load_agent_settings
from processpilot.agent.graph import (
    AgentRunner,
    create_llm,
    load_as_is_tree,
    model_id_from_env,
    segment_activities,
)
from processpilot.agent.llm import ChatTurn, LLMResponse
from processpilot.agent.state import AgentState, Constraints, ReviewDecision
from processpilot.api.db import Database
from processpilot.api.jobs import JobRunner
from processpilot.config import (
    find_project_root,
    load_config_yaml,
    load_preset_yaml,
    resolve_source_path,
)
from processpilot.ingest.mapping import ColumnMapping, suggest_mapping
from processpilot.mining.evidence import EvidenceStore
from processpilot.mining.run import new_run_id
from processpilot.pipeline import (
    DATA_QUALITY_FILE,
    CleaningPolicy,
    clean_log,
    load_canonical,
    mine_log,
    suggested_cleaning_policy,
    validate_log,
)
from processpilot.process_tree import graphviz_available, labels
from processpilot.redesign.operations import describe
from processpilot.sources.file_source import FileSource

DB_FILE = "processpilot.sqlite"
PROFILE_FILE = "profile.json"
QUALITY_FILE = "quality.json"
_EXAMPLE_VALUES = 3
_COPY_CHUNK_BYTES = 1 << 20
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_NODE_MESSAGES = {
    "load_evidence": "evidence loaded",
    "diagnose": "diagnosis done",
    "redesign": "proposals drafted",
    "apply_edits": "to-be models written",
    "validate": "soundness checked and impact estimated",
    "human_review": "decision recorded",
    "finalize": "run finalised",
}


class NotFound(Exception):
    """The requested dataset, run, segment or agent run does not exist (404)."""


class Conflict(Exception):
    """The request is valid but not in the current state (409)."""


class Invalid(Exception):
    """The request's content is unusable (422)."""


@dataclass(frozen=True)
class AppSettings:
    """``configs/app.yaml`` (the ``api`` block)."""

    host: str
    port: int
    job_workers: int
    max_upload_mb: int

    @classmethod
    def load(cls) -> AppSettings:
        """Read the ``api`` block of ``configs/app.yaml``."""
        api = load_config_yaml("app.yaml")["api"]
        return cls(
            host=str(api["host"]),
            port=int(api["port"]),
            job_workers=int(api["job_workers"]),
            max_upload_mb=int(api["max_upload_mb"]),
        )


class _ReadOnlyModel:
    """Stands in for the model when a graph is built only to read its checkpoint."""

    model_id = "(read-only)"

    def invoke(self, turns: list[ChatTurn], schema: type) -> LLMResponse:  # pragma: no cover
        raise RuntimeError("a read-only agent runner cannot call a model")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{new_run_id()}-{uuid.uuid4().hex[:6]}"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


class Workspace:
    """The API's state: run folders on disk, the SQLite index and the job pool."""

    def __init__(
        self,
        runs_dir: Path,
        app_dir: Path,
        settings: AppSettings,
        inline_jobs: bool = False,
    ) -> None:
        """Open the index, recover from an earlier stop, and register runs found on disk."""
        self.runs_dir = Path(runs_dir)
        self.app_dir = Path(app_dir)
        self.settings = settings
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.app_dir / DB_FILE)
        self.jobs = JobRunner(self.db, settings.job_workers, inline=inline_jobs)
        self.db.interrupt_unfinished_jobs()
        self._recover_runs()
        self.sync_runs()

    def close(self) -> None:
        """Let running jobs finish."""
        self.jobs.shutdown()

    # --- health and presets --------------------------------------------------

    def health(self) -> dict[str, Any]:
        """What the UI shows in its status line."""
        try:
            model_id: str | None = model_id_from_env(load_agent_settings())
        except OSError:
            model_id = None
        return {
            "status": "ok",
            "model_id": model_id,
            "graphviz": graphviz_available(),
            "runs_dir": str(self.runs_dir),
            "active_jobs": len(self.db.jobs(active_only=True)),
        }

    def presets(self) -> list[dict[str, Any]]:
        """Dataset presets in ``configs/`` and whether their source file is reachable.

        A preset is a top-level YAML with a ``discovery`` block (after
        ``extends``); agent/heuristics/app configs have none.
        """
        out = []
        for path in sorted((find_project_root() / "configs").glob("*.yaml")):
            try:
                preset = load_preset_yaml(path.stem)
            except (OSError, ValueError):
                continue
            if "discovery" not in preset:
                continue
            available, reason = True, None
            try:
                resolve_source_path(preset)
            except (OSError, ValueError) as exc:
                available, reason = False, str(exc)
            out.append(
                {
                    "name": path.stem,
                    "description": " ".join(str(preset.get("description", "")).split()),
                    "has_mapping": "mapping" in preset,
                    "source_available": available and "mapping" in preset,
                    "source_problem": reason,
                }
            )
        return out

    def _preset(self, name: str) -> dict[str, Any]:
        try:
            preset = load_preset_yaml(name)
        except FileNotFoundError as exc:
            raise Invalid(f"unknown preset {name!r}") from exc
        if "discovery" not in preset:
            raise Invalid(f"{name!r} is not a dataset preset")
        return preset

    # --- datasets ------------------------------------------------------------

    def create_preset_dataset(self, preset_name: str) -> dict[str, Any]:
        """A dataset that reads a preset's configured source with the preset's mapping."""
        preset = self._preset(preset_name)
        if "mapping" not in preset:
            raise Invalid(f"preset {preset_name!r} has no mapping; upload a file instead")
        try:
            path = resolve_source_path(preset)
        except (OSError, ValueError) as exc:
            raise Invalid(str(exc)) from exc
        mapping = ColumnMapping(
            **preset["mapping"],
            timestamp_timezone_policy=preset.get("timestamp_timezone_policy", "utc"),
        )
        dataset_id = _new_id("ds")
        self.db.add_dataset(
            dataset_id,
            "preset",
            str(preset.get("name", preset_name)),
            preset_name,
            str(path),
            mapping.model_dump(),
        )
        return self.dataset(dataset_id)

    def create_upload_dataset(
        self, filename: str, stream: BinaryIO, base_preset: str
    ) -> dict[str, Any]:
        """Store an uploaded log; its mapping is confirmed by the analyst later."""
        self._preset(base_preset)
        dataset_id = _new_id("ds")
        safe = _SAFE_NAME.sub("_", Path(filename).name).strip("._") or "upload"
        directory = self.app_dir / "datasets" / dataset_id
        directory.mkdir(parents=True)
        target = directory / safe
        limit = self.settings.max_upload_mb * 1024 * 1024
        written = 0
        with target.open("wb") as out:
            while chunk := stream.read(_COPY_CHUNK_BYTES):
                written += len(chunk)
                if written > limit:
                    out.close()
                    shutil.rmtree(directory)
                    raise Invalid(f"upload exceeds {self.settings.max_upload_mb} MB")
                out.write(chunk)
        try:
            FileSource(target)
        except ValueError as exc:
            shutil.rmtree(directory)
            raise Invalid(str(exc)) from exc
        self.db.add_dataset(dataset_id, "upload", safe, base_preset, str(target), None)
        return self.dataset(dataset_id)

    def _dataset_record(self, dataset_id: str) -> dict[str, Any]:
        record = self.db.dataset(dataset_id)
        if record is None:
            raise NotFound(f"dataset {dataset_id!r} not found")
        return record

    def _dataset_dir(self, dataset_id: str) -> Path:
        return self.app_dir / "datasets" / dataset_id

    def dataset(self, dataset_id: str) -> dict[str, Any]:
        """The record plus its profile and data-quality report, when computed."""
        record = self._dataset_record(dataset_id)
        directory = self._dataset_dir(dataset_id)
        return {
            **record,
            "profile": _read_json(directory / PROFILE_FILE),
            "quality": _read_json(directory / QUALITY_FILE),
            "suggested_cleaning": suggested_cleaning_policy(
                self._preset(record["preset"])
            ).model_dump(),
            "runs": [r["run_id"] for r in self.db.runs() if r["dataset_id"] == dataset_id],
        }

    def datasets(self) -> list[dict[str, Any]]:
        """All dataset records, newest first."""
        return self.db.datasets()

    def profile_dataset(self, dataset_id: str) -> str:
        """Job: read the raw file, describe its columns, rank mapping candidates."""
        record = self._dataset_record(dataset_id)

        def run(progress: Callable[[str], None]) -> dict[str, Any]:
            progress("reading the source file (the first read of a large XES takes minutes)")
            raw = FileSource(record["source_path"]).fetch()
            progress("profiling columns and ranking mapping candidates")
            columns = []
            for name in raw.columns:
                col = raw[name]
                examples = col.dropna().astype(str).unique()[:_EXAMPLE_VALUES]
                columns.append(
                    {
                        "name": str(name),
                        "dtype": str(col.dtype),
                        "null_share": float(col.isna().mean()) if len(col) else 0.0,
                        "distinct": int(col.nunique(dropna=True)),
                        "examples": [str(e) for e in examples],
                    }
                )
            suggestions = {
                role: [c.model_dump() for c in candidates]
                for role, candidates in suggest_mapping(raw).items()
            }
            profile = {"row_count": len(raw), "columns": columns, "suggestions": suggestions}
            _write_json(self._dataset_dir(dataset_id) / PROFILE_FILE, profile)
            return {"row_count": len(raw), "column_count": len(columns)}

        return self.jobs.submit("profile", {"dataset_id": dataset_id}, run)

    def set_mapping(self, dataset_id: str, mapping: ColumnMapping) -> dict[str, Any]:
        """Record the analyst's mapping; a stale data-quality report is discarded."""
        self._dataset_record(dataset_id)
        profile = _read_json(self._dataset_dir(dataset_id) / PROFILE_FILE)
        if profile is not None:
            known = {c["name"] for c in profile["columns"]}
            missing = [c for c in mapping.mapped_columns() if c not in known]
            if missing:
                raise Invalid(f"mapped column(s) not in the file: {missing}")
        self.db.set_mapping(dataset_id, mapping.model_dump())
        (self._dataset_dir(dataset_id) / QUALITY_FILE).unlink(missing_ok=True)
        return self.dataset(dataset_id)

    def _mapping(self, record: dict[str, Any]) -> ColumnMapping:
        if record["mapping"] is None:
            raise Conflict("confirm a column mapping first")
        return ColumnMapping(**record["mapping"])

    def validate_dataset(self, dataset_id: str) -> str:
        """Job: map to the canonical schema and build the data-quality report."""
        record = self._dataset_record(dataset_id)
        mapping = self._mapping(record)
        preset = self._preset(record["preset"])

        def run(progress: Callable[[str], None]) -> dict[str, Any]:
            progress("reading and mapping the log")
            canonical = load_canonical(FileSource(record["source_path"]), mapping)
            progress("checking data quality")
            report = validate_log(canonical, preset)
            _write_json(
                self._dataset_dir(dataset_id) / QUALITY_FILE,
                {"mapping": mapping.model_dump(), "report": report.model_dump(mode="json")},
            )
            return {
                "event_count": report.event_count,
                "case_count": report.case_count,
                "issue_count": report.issue_count,
            }

        return self.jobs.submit("validate", {"dataset_id": dataset_id}, run)

    def mine_dataset(self, dataset_id: str, policy: CleaningPolicy) -> tuple[str, str]:
        """Job: clean with the analyst's policy, mine every segment. Returns (job, run id)."""
        record = self._dataset_record(dataset_id)
        mapping = self._mapping(record)
        preset = self._preset(record["preset"])
        run_id = self._unique_run_id()
        self.db.add_run(
            run_id,
            "app",
            "mining",
            dataset_id=dataset_id,
            preset=record["preset"],
            cleaning=policy.model_dump(),
        )

        def run(progress: Callable[[str], None]) -> dict[str, Any]:
            progress("reading and mapping the log")
            source = FileSource(record["source_path"])
            canonical = load_canonical(source, mapping)
            report = validate_log(canonical, preset)
            progress("applying the cleaning policy")
            clean, quality = clean_log(canonical, report, policy, preset)
            store = mine_log(
                clean,
                preset,
                self.runs_dir / run_id,
                {**source.describe(), "dataset_id": dataset_id, "preset": record["preset"]},
                quality,
                progress,
            )
            self.db.update_run(run_id, status="mined", evidence_count=len(store.items))
            return {"run_id": run_id, "evidence_count": len(store.items)}

        job_id = self.jobs.submit(
            "mine",
            {"dataset_id": dataset_id, "run_id": run_id},
            run,
            on_failure=lambda _: self.db.update_run(run_id, status="failed"),
        )
        return job_id, run_id

    def _unique_run_id(self) -> str:
        base = new_run_id()
        candidate, n = base, 2
        while self.db.run(candidate) is not None or (self.runs_dir / candidate).exists():
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    # --- runs ----------------------------------------------------------------

    def _recover_runs(self) -> None:
        """Runs left 'mining' by a stopped server: complete iff the manifest was written.

        ``write_run`` writes ``manifest.json`` last, so its presence means the
        run folder is complete.
        """
        for run in self.db.runs():
            if run["status"] == "mining":
                done = (self.runs_dir / run["run_id"] / "manifest.json").is_file()
                self.db.update_run(run["run_id"], status="mined" if done else "failed")

    def sync_runs(self) -> int:
        """Register complete run folders the index does not know (e.g. notebook runs)."""
        added = 0
        for directory in sorted(p for p in self.runs_dir.iterdir() if p.is_dir()):
            manifest = _read_json(directory / "manifest.json")
            if manifest is None or self.db.run(directory.name) is not None:
                continue
            self.db.add_run(
                directory.name,
                "disk",
                "mined",
                evidence_count=manifest.get("evidence_count"),
                created_at=manifest.get("created_at"),
            )
            added += 1
        return added

    def _run_record(self, run_id: str) -> dict[str, Any]:
        record = self.db.run(run_id)
        if record is None:
            raise NotFound(f"run {run_id!r} not found")
        return record

    def _mined_run(self, run_id: str) -> tuple[dict[str, Any], Path, dict[str, Any]]:
        record = self._run_record(run_id)
        if record["status"] != "mined":
            raise Conflict(f"run {run_id} is {record['status']}, not mined")
        run_dir = self.runs_dir / run_id
        return record, run_dir, _read_json(run_dir / "manifest.json") or {}

    def _segment(self, run_id: str, slug: str) -> tuple[dict[str, Any], Path, str]:
        """(run record, run dir, segment name) for a slug listed in the manifest."""
        record, run_dir, manifest = self._mined_run(run_id)
        names = {s: name for name, s in manifest.get("segments", {}).items()}
        if slug not in names:
            raise NotFound(f"run {run_id} has no segment {slug!r}")
        return record, run_dir, names[slug]

    def runs(self) -> list[dict[str, Any]]:
        """Every indexed run (after registering new ones found on disk)."""
        self.sync_runs()
        return self.db.runs()

    def run(self, run_id: str) -> dict[str, Any]:
        """The record, its segments with their KPI line, and the data-quality report."""
        record = self._run_record(run_id)
        run_dir = self.runs_dir / run_id
        manifest = _read_json(run_dir / "manifest.json")
        segments = []
        if manifest is not None:
            store = EvidenceStore.from_json(run_dir / "evidence.json")
            for name, slug in manifest["segments"].items():
                kpi = store.query(name, "kpi")
                segments.append(
                    {
                        "name": name,
                        "slug": slug,
                        "kpi": kpi[0].model_dump() if kpi else None,
                        "has_model": (run_dir / slug / "as_is.bpmn").is_file(),
                        "agent_runs": len(self.db.agent_runs(run_id, name)),
                    }
                )
        return {
            **record,
            "manifest": manifest,
            "segments": segments,
            "data_quality": _read_json(run_dir / DATA_QUALITY_FILE),
        }

    def set_run_preset(self, run_id: str, preset_name: str) -> dict[str, Any]:
        """Record which preset a run found on disk was mined with (the agent needs it)."""
        self._run_record(run_id)
        self._preset(preset_name)
        self.db.update_run(run_id, preset=preset_name)
        return self.run(run_id)

    def evidence(self, run_id: str, slug: str, type_: str | None = None) -> list[dict[str, Any]]:
        """A segment's evidence items in rank order, optionally of one type."""
        _, run_dir, name = self._segment(run_id, slug)
        store = EvidenceStore.from_json(run_dir / slug / "evidence.json")
        return [i.model_dump() for i in store.query(name, type_)]  # type: ignore[arg-type]

    def segment_model(self, run_id: str, slug: str) -> dict[str, Any]:
        """Model activities, activities outside the model, and the default constraints."""
        record, run_dir, name = self._segment(run_id, slug)
        tree = load_as_is_tree(run_dir, name)
        modelled = sorted(labels(tree)) if tree is not None else []
        observed = segment_activities(run_dir, name)
        defaults = None
        if record["preset"] is not None:
            block = self._preset(record["preset"]).get("redesign") or {}
            defaults = Constraints(
                text=block.get("constraints_text") or [],
                protected_activities=block.get("protected_activities") or [],
            ).model_dump()
        return {
            "segment": name,
            "activities": modelled,
            "unmodelled_activities": sorted(set(observed) - set(modelled)),
            "default_constraints": defaults,
            "preset": record["preset"],
        }

    def as_is_bpmn(self, run_id: str, slug: str) -> str:
        """The segment's as-is BPMN XML."""
        _, run_dir, _ = self._segment(run_id, slug)
        path = run_dir / slug / "as_is.bpmn"
        if not path.is_file():
            raise NotFound(f"run {run_id} has no as-is model for {slug!r}")
        return path.read_text(encoding="utf-8")

    # --- agent runs ----------------------------------------------------------

    def _runner(self, run_dir: Path, preset_name: str | None, model_id: str | None) -> AgentRunner:
        if preset_name is None:
            raise Conflict("choose the preset this run was mined with before running the agent")
        preset = self._preset(preset_name)
        if model_id is None:
            return AgentRunner(run_dir, preset, llm=_ReadOnlyModel())
        return AgentRunner(
            run_dir, preset, llm=create_llm(model_id, load_agent_settings(), run_dir)
        )

    def start_agent(
        self,
        run_id: str,
        slug: str,
        constraints: Constraints | None = None,
        model_id: str | None = None,
    ) -> str:
        """Job: run the agent on one segment until it waits for review."""
        record, run_dir, name = self._segment(run_id, slug)
        if model_id is None:
            try:
                model_id = model_id_from_env(load_agent_settings())
            except OSError as exc:
                raise Invalid(str(exc)) from exc
        with self._runner(run_dir, record["preset"], None) as r:
            agent_run_id = r.new_agent_run_id(name)
        self.db.add_agent_run(run_id, name, agent_run_id, model_id)

        def run(progress: Callable[[str], None]) -> dict[str, Any]:
            progress(f"agent started (model {model_id})")
            with self._runner(run_dir, record["preset"], model_id) as r:
                _, state = r.start(
                    name,
                    constraints,
                    agent_run_id=agent_run_id,
                    on_node=lambda node: progress(_NODE_MESSAGES.get(node, node)),
                )
            self.db.set_agent_status(run_id, name, agent_run_id, state.status)
            return {"agent_run_id": agent_run_id, "status": state.status}

        return self.jobs.submit(
            "agent_start",
            {"run_id": run_id, "segment": name, "agent_run_id": agent_run_id},
            run,
            on_failure=lambda _: self.db.set_agent_status(run_id, name, agent_run_id, "failed"),
        )

    def agent_runs(self, run_id: str, slug: str) -> list[dict[str, Any]]:
        """Indexed agent runs of a segment, newest first."""
        _, _, name = self._segment(run_id, slug)
        return self.db.agent_runs(run_id, name)

    def _agent_record(self, run_id: str, slug: str, agent_run_id: str) -> tuple[Any, ...]:
        record, run_dir, name = self._segment(run_id, slug)
        agent = self.db.agent_run(run_id, name, agent_run_id)
        if agent is None:
            raise NotFound(f"agent run {agent_run_id!r} not found for {slug!r}")
        return record, run_dir, name, agent

    def _busy(self, agent_run_id: str) -> bool:
        return any(
            (j["target"] or {}).get("agent_run_id") == agent_run_id
            for j in self.db.jobs(active_only=True)
        )

    def agent_run(self, run_id: str, slug: str, agent_run_id: str) -> dict[str, Any]:
        """Checkpointed state, the pending review (if any) and the decision log."""
        record, run_dir, name, agent = self._agent_record(run_id, slug, agent_run_id)
        busy = self._busy(agent_run_id)
        state: AgentState | None = None
        pending: dict[str, Any] | None = None
        with self._runner(run_dir, record["preset"], None) as r:
            try:
                state = r.state(name, agent_run_id)
                pending = None if busy else r.pending_review(name, agent_run_id)
            except KeyError:
                state = None  # job queued, graph not started yet
        if state is not None and not busy and state.status != agent["status"]:
            self.db.set_agent_status(run_id, name, agent_run_id, state.status)
            agent = {**agent, "status": state.status}
        proposals = []
        if state is not None and state.proposals is not None:
            edits = {e.proposal_index: e for e in state.edit_results}
            checks = {v.proposal_index: v for v in state.validation_results}
            for i, p in enumerate(state.proposals.proposals):
                proposals.append(
                    {
                        "index": i,
                        "proposal": p.model_dump(),
                        "operations_text": [describe(op) for op in p.edit_operations],
                        "edit": edits[i].model_dump(mode="json") if i in edits else None,
                        "validation": checks[i].model_dump(mode="json") if i in checks else None,
                    }
                )
        return {
            **agent,
            "busy": busy,
            "awaiting_review": pending is not None,
            "review_round": pending["round"] if pending else None,
            "max_revision_loops": pending["max_revision_loops"] if pending else None,
            "state": state.model_dump(mode="json") if state is not None else None,
            "proposals": proposals,
            "decisions": self.db.decisions(run_id, name, agent_run_id),
        }

    def to_be_bpmn(self, run_id: str, slug: str, agent_run_id: str, index: int) -> str:
        """The to-be BPMN XML of one proposal of the latest round."""
        record, run_dir, name, _ = self._agent_record(run_id, slug, agent_run_id)
        with self._runner(run_dir, record["preset"], None) as r:
            state = r.state(name, agent_run_id)
        edit = next((e for e in state.edit_results if e.proposal_index == index), None)
        if edit is None or edit.to_be_bpmn_path is None:
            raise NotFound(f"proposal {index} has no to-be model")
        path = Path(edit.to_be_bpmn_path).resolve()
        if not path.is_relative_to(run_dir.resolve()) or not path.is_file():
            raise NotFound(f"to-be model of proposal {index} is not in run {run_id}")
        return path.read_text(encoding="utf-8")

    def decide(
        self,
        run_id: str,
        slug: str,
        agent_run_id: str,
        decision: ReviewDecision,
        analyst: str | None,
    ) -> str:
        """Log the analyst's decision, then resume the graph in a job.

        The decision is written before the graph resumes: it was made whether
        or not the following redesign round succeeds.
        """
        record, run_dir, name, agent = self._agent_record(run_id, slug, agent_run_id)
        if self._busy(agent_run_id):
            raise Conflict("this agent run is still working; wait for its job to finish")
        with self._runner(run_dir, record["preset"], None) as r:
            pending = r.pending_review(name, agent_run_id)
        if pending is None:
            raise Conflict(f"agent run {agent_run_id} is not awaiting review")
        proposals = (pending.get("proposals") or {}).get("proposals") or []
        comment = (decision.comment or "").strip() or None
        self.db.add_decision(
            run_id,
            name,
            agent_run_id,
            int(pending["round"]),
            decision.decision,
            comment,
            (analyst or "").strip() or None,
            [p["title"] for p in proposals],
        )
        self.db.set_agent_status(run_id, name, agent_run_id, "running")

        def run(progress: Callable[[str], None]) -> dict[str, Any]:
            progress(f"resuming after '{decision.decision}'")
            with self._runner(run_dir, record["preset"], agent["model_id"]) as r:
                state = r.resume(
                    name,
                    agent_run_id,
                    ReviewDecision(decision=decision.decision, comment=comment),
                    on_node=lambda node: progress(_NODE_MESSAGES.get(node, node)),
                )
            self.db.set_agent_status(run_id, name, agent_run_id, state.status)
            return {"agent_run_id": agent_run_id, "status": state.status}

        return self.jobs.submit(
            "agent_resume",
            {"run_id": run_id, "segment": name, "agent_run_id": agent_run_id},
            run,
            on_failure=lambda _: self.db.set_agent_status(run_id, name, agent_run_id, "failed"),
        )

    # --- jobs and decisions ----------------------------------------------------

    def job(self, job_id: str) -> dict[str, Any]:
        """One job."""
        job = self.db.job(job_id)
        if job is None:
            raise NotFound(f"job {job_id!r} not found")
        return job

    def all_jobs(self, active_only: bool = False) -> list[dict[str, Any]]:
        """Jobs, newest first."""
        return self.db.jobs(active_only)

    def all_decisions(self, run_id: str | None = None) -> list[dict[str, Any]]:
        """The decision log (the audit trail), optionally for one run."""
        return self.db.decisions(run_id)


def parse_mapping(payload: dict[str, Any]) -> ColumnMapping:
    """A ``ColumnMapping`` from request JSON; ``Invalid`` with pydantic's message."""
    try:
        return ColumnMapping(**payload)
    except (TypeError, ValidationError) as exc:
        raise Invalid(str(exc)) from exc
