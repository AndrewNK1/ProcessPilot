"""The scripted dry-run model drives the real graph to review with zero API calls."""

from __future__ import annotations

import json
from pathlib import Path

from agent_fixtures import SEGMENT, SLUG, agent_settings, preset, write_run_dir
from processpilot.agent.dry_run import DRY_RUN_MODEL_ID, DryRunLLM, dry_run_outputs
from processpilot.agent.graph import AgentRunner, load_as_is_tree
from processpilot.agent.runlog import RunLogger
from processpilot.agent.tools import EvidenceTools
from processpilot.config import find_project_root, resolve_source_path
from processpilot.ingest.mapping import load_preset
from processpilot.mining.evidence import EvidenceStore
from processpilot.pipeline import (
    clean_log,
    load_canonical,
    mine_log,
    suggested_cleaning_policy,
    validate_log,
)
from processpilot.process_tree import labels
from processpilot.sources.file_source import FileSource


def _dry_llm(run_dir: Path, segment: str) -> DryRunLLM:
    store = EvidenceStore.from_json(run_dir / "evidence.json")
    tree = load_as_is_tree(run_dir, segment)
    assert tree is not None
    return DryRunLLM.for_segment(EvidenceTools(store, segment), sorted(labels(tree)))


def test_outputs_cite_existing_evidence_and_modelled_activities(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path)
    store = EvidenceStore.from_json(run_dir / "evidence.json")
    out = dry_run_outputs(EvidenceTools(store, SEGMENT), ["Approve Order", "Receive Goods"])
    assert out["Diagnosis"]["hypotheses"][0]["evidence_ids"] == [f"BN-01-{SLUG}"]
    # Bottleneck targets first: BN-01 -> Receive Goods; BN-02's target is not modelled here.
    ops = [p["edit_operations"][0] for p in out["RedesignSet"]["proposals"]]
    assert ops == [
        {"op": "automate_activity", "activity": "Receive Goods"},
        {"op": "automate_activity", "activity": "Approve Order"},
    ]


def test_dry_run_reaches_review_without_retries(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path)
    llm = _dry_llm(run_dir, SEGMENT)
    with AgentRunner(run_dir, preset(), llm=llm, settings=agent_settings()) as runner:
        agent_run_id, state = runner.start(SEGMENT)
    assert state.status == "awaiting_review", state.error
    assert llm.calls == ["Diagnosis", "RedesignSet"]  # no retry: the outputs validate
    assert [v.status for v in state.validation_results] == ["validated"] * 2
    log = RunLogger(run_dir / SLUG / "agent" / agent_run_id)
    review = json.loads((log.directory / "review_r0.json").read_text(encoding="utf-8"))
    assert review["model_id"] == review["served_models"][0] == DRY_RUN_MODEL_ID
    assert review["usage"]["input_tokens"] == 0 and review["usage"]["cost_usd"] is None


def test_dry_run_on_committed_sample_every_segment(tmp_path: Path) -> None:
    """What notebook 03 does by default: mine the sample, dry-run each segment."""
    mapping, sample = load_preset("bpi2019_sample")
    canonical = load_canonical(
        FileSource(resolve_source_path(sample), cache_dir=tmp_path / "cache"), mapping
    )
    report = validate_log(canonical, sample)
    clean, _ = clean_log(canonical, report, suggested_cleaning_policy(sample), sample)
    run_dir = tmp_path / "runs" / "20990101T000000Z"
    store = mine_log(clean, sample, run_dir)
    assert find_project_root() not in run_dir.parents  # nothing written into outputs/

    for segment in store.segments():
        with AgentRunner(run_dir, sample, llm=_dry_llm(run_dir, segment)) as runner:
            _, state = runner.start(segment)
        assert state.status == "awaiting_review", (segment, state.error)
        assert all(v.soundness.sound for v in state.validation_results), segment
