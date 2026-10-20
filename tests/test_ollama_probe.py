r"""Opt-in reliability probe: the real ``diagnose`` node on a local Ollama model.

Needs a running Ollama server with the model in ``PP_MODEL=ollama:<tag>`` pulled
and a mined BPI 2019 run, so it is excluded by default. Run with:

    .\.venv\python.exe -m pytest tests/test_ollama_probe.py -m ollama -o addopts="" -s

WHY the production node instead of a toy prompt: the question is whether a
small local model can do *this* task, i.e. the real evidence digest (~4.5k
tokens for "2-way match"), the real ``Diagnosis`` schema (required, non-empty
``evidence_ids`` per hypothesis) and the real validator and retry loop. Every
call is logged in the normal run-log format under
``outputs/runs/<run_id>/<segment>/agent/probe-*/`` (``inputs.json`` carries the
model digest), and a summary is written next to them.

``PP_PROBE_RUNS`` sets the number of independent runs (default 5).
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from processpilot.agent import nodes
from processpilot.agent.graph import AgentRunner
from processpilot.agent.llm import model_fingerprint
from processpilot.agent.state import AgentState
from processpilot.config import find_project_root, load_env
from processpilot.ingest.mapping import load_preset
from processpilot.mining.evidence import segment_slug

SEGMENT = "2-way match"

pytestmark = [pytest.mark.llm, pytest.mark.ollama]


def _latest_run_dir() -> Path | None:
    runs = find_project_root() / "outputs" / "runs"
    candidates = sorted(p for p in runs.glob("*") if (p / "evidence.json").is_file())
    return candidates[-1] if candidates else None


def test_local_model_diagnosis_reliability() -> None:
    load_env()
    model_id = os.environ.get("PP_MODEL", "")
    if not model_id.startswith("ollama:"):
        pytest.skip("PP_MODEL is not an ollama:<tag> model")
    fingerprint = model_fingerprint(model_id)
    if fingerprint is None or "error" in fingerprint:
        pytest.skip(f"local model unavailable: {fingerprint}")
    run_dir = _latest_run_dir()
    if run_dir is None:
        pytest.skip("no mined run under outputs/runs")
    n_runs = int(os.environ.get("PP_PROBE_RUNS", "5"))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    _, preset = load_preset("bpi2019")
    results = []
    with AgentRunner(run_dir, preset) as runner:
        num_ctx = runner.settings.model.kwargs_for(model_id).get("num_ctx")
        for i in range(n_runs):
            agent_run_id = f"probe-{stamp}-{i + 1}"
            ctx = runner._context(SEGMENT, agent_run_id)
            state = AgentState(
                run_id=runner.run_id,
                agent_run_id=agent_run_id,
                segment=SEGMENT,
                constraints=runner.default_constraints(),
            )
            state = state.model_copy(update=nodes.load_evidence(state, ctx))
            started = time.perf_counter()
            update = nodes.diagnose(state, ctx)
            elapsed = time.perf_counter() - started
            calls = [e for e in ctx.logger.events() if e["kind"] == "llm_call"]
            results.append(
                {
                    "agent_run_id": agent_run_id,
                    "valid": "diagnosis" in update,
                    "attempts": len(calls),
                    "retries": max(len(calls) - 1, 0),
                    "seconds_total": round(elapsed, 1),
                    "seconds_per_call": round(elapsed / max(len(calls), 1), 1),
                    "first_attempt_issue_codes": sorted(
                        {i["code"] for i in calls[0]["issues"]} if calls else set()
                    ),
                    "input_tokens": [c["usage"]["input_tokens"] for c in calls],
                    "output_tokens": [c["usage"]["output_tokens"] for c in calls],
                    "stop_reasons": [c["stop_reason"] for c in calls],
                    "hypotheses": len(update["diagnosis"].hypotheses)
                    if "diagnosis" in update
                    else 0,
                    "warnings": [w.code for w in update.get("warnings", [])],
                    "error": update.get("error"),
                }
            )
            print(json.dumps(results[-1]))

    summary = {
        "model_id": model_id,
        "model_fingerprint": fingerprint,
        "num_ctx": num_ctx,
        "segment": SEGMENT,
        "runs": n_runs,
        "valid": sum(r["valid"] for r in results),
        "first_attempt_valid": sum(r["valid"] and r["attempts"] == 1 for r in results),
        "total_retries": sum(r["retries"] for r in results),
        "results": results,
    }
    out = run_dir / segment_slug(SEGMENT) / "agent" / f"probe-{stamp}-summary.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {out}")

    # A prompt that reaches num_ctx was truncated by Ollama without an error.
    max_input = max((t or 0) for r in results for t in r["input_tokens"])
    assert num_ctx is None or max_input < num_ctx, f"prompt ({max_input}) hit num_ctx"
    assert summary["valid"] == n_runs, f"{summary['valid']}/{n_runs} runs valid"
