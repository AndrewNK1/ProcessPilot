r"""Opt-in integration test against the real model in ``PP_MODEL``.

Costs money and needs network, an API key and a mined BPI 2019 run, so it is
excluded by default. Run with:

    .\.venv\python.exe -m pytest -m llm -o addopts=""

It runs the smallest segment ("2-way match") to the review interrupt and then
rejects without comment, so exactly two model calls are made unless a retry
is needed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from processpilot.agent.graph import AgentRunner
from processpilot.config import find_project_root, load_env
from processpilot.ingest.mapping import load_preset

SEGMENT = "2-way match"

pytestmark = pytest.mark.llm


def _latest_run_dir() -> Path | None:
    runs = find_project_root() / "outputs" / "runs"
    candidates = sorted(p for p in runs.glob("*") if (p / "evidence.json").is_file())
    return candidates[-1] if candidates else None


def test_real_model_two_way_match_reaches_review() -> None:
    load_env()
    model_id = os.environ.get("PP_MODEL", "")
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not model_id.startswith("anthropic:"):
        pytest.skip("PP_MODEL is not an anthropic: model (local models: test_ollama_probe)")
    if not key or "REPLACE" in key:
        pytest.skip("ANTHROPIC_API_KEY not configured")
    run_dir = _latest_run_dir()
    if run_dir is None:
        pytest.skip("no mined run under outputs/runs")

    _, preset = load_preset("bpi2019")
    with AgentRunner(run_dir, preset) as runner:
        agent_run_id, state = runner.start(SEGMENT)
        assert state.status == "awaiting_review", state.error
        assert state.diagnosis is not None and 1 <= len(state.diagnosis.hypotheses) <= 5
        assert state.proposals is not None and 2 <= len(state.proposals.proposals) <= 3
        final = runner.resume(SEGMENT, agent_run_id, {"decision": "reject", "comment": None})
    assert final.status == "rejected"
