"""Per-run agent log: ``outputs/runs/<run_id>/<segment>/agent/<agent_run_id>/``.

WHY an append-only JSONL plus snapshot files: a run must be inspectable
without re-running it, and a paused run may be resumed by a different process
(e.g. the web app) hours later. Appending events means the logger holds no
state, so a resumed run simply keeps appending to the same file; totals are
recomputed from the file, never from memory.

Files:
    inputs.json      model, prompt version, settings, constraints, evidence ids,
                     library versions (written once by load_evidence)
    events.jsonl     one line per event: every LLM call with full prompts, raw
                     output, parsed output, validation issues, usage, cost;
                     retries; review decisions; node failures
    diagnosis.json / proposals_r<N>.json   accepted outputs
    summary.json     final status, totals, last proposals (written by finalize)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVENTS_FILE = "events.jsonl"


def utc_now() -> str:
    """ISO timestamp, UTC, second precision."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class RunLogger:
    """Writes one agent run's artefacts. Stateless apart from its directory."""

    def __init__(self, directory: Path) -> None:
        """Create the directory if needed."""
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def write_json(self, name: str, payload: Any) -> Path:
        """Write (or overwrite) one snapshot file."""
        path = self.directory / name
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        return path

    def event(self, kind: str, **payload: Any) -> None:
        """Append one event line."""
        line = json.dumps(
            {"at": utc_now(), "kind": kind, **payload}, ensure_ascii=False, default=str
        )
        with (self.directory / EVENTS_FILE).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def events(self) -> list[dict[str, Any]]:
        """Every event so far, in order."""
        path = self.directory / EVENTS_FILE
        if not path.is_file():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]

    def usage_totals(self) -> dict[str, Any]:
        """Summed tokens and cost over every logged LLM call."""
        calls = [e for e in self.events() if e["kind"] == "llm_call"]
        totals: dict[str, Any] = {"llm_calls": len(calls)}
        for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
            values = [c["usage"].get(key) for c in calls]
            totals[key] = sum(v for v in values if v is not None)
        costs = [c.get("cost_usd") for c in calls]
        totals["cost_usd"] = (
            None if any(c is None for c in costs) else round(sum(costs), 6)  # type: ignore[arg-type]
        )
        return totals
