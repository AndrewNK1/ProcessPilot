"""The rule-based mock model: its output must pass the same validators as a real model's.

Uses the hand-built segment from ``agent_fixtures`` (no activity-wait items,
one rework item, two bottlenecks; "Pay Invoice" protected), so every expected
proposal follows from the rules by hand.
"""

from __future__ import annotations

from pathlib import Path

from agent_fixtures import PROTECTED, SEGMENT, SLUG, agent_settings, preset, write_run_dir
from processpilot.agent.graph import AgentRunner, create_llm
from processpilot.agent.llm import ChatTurn
from processpilot.agent.state import AgentState, Diagnosis, RedesignSet
from processpilot.llm.mock import MockLLM


def _run(tmp_path: Path, comment: str | None = None) -> AgentState:
    run_dir = write_run_dir(tmp_path)
    with AgentRunner(run_dir, preset(), llm=MockLLM(run_dir), settings=agent_settings()) as r:
        arid, state = r.start(SEGMENT)
        if comment is not None:
            state = r.resume(SEGMENT, arid, {"decision": "reject", "comment": comment})
    return state


def test_mock_output_passes_every_validator_without_retries(tmp_path: Path) -> None:
    state = _run(tmp_path)
    assert state.status == "awaiting_review" and state.warnings == []
    assert [h.evidence_ids for h in state.diagnosis.hypotheses] == [
        [f"BN-01-{SLUG}"],
        [f"BN-02-{SLUG}"],
        [f"RW-01-{SLUG}"],
    ]
    proposals = state.proposals.proposals
    ops = [(p.heuristic_id, p.edit_operations[0].model_dump()) for p in proposals]
    # Rules interleaved: no activity waits here, so rework elimination comes first,
    # then automation of bottleneck targets (protected "Pay Invoice" may be automated).
    assert ops == [
        ("H-TASK-ELIMINATION", {"op": "remove_activity", "activity": "Approve Order"}),
        ("H-AUTOMATION", {"op": "automate_activity", "activity": "Receive Goods"}),
        ("H-AUTOMATION", {"op": "automate_activity", "activity": "Pay Invoice"}),
    ]
    assert all(p.title.startswith("[mock]") for p in proposals)
    assert all(v.status == "validated" for v in state.validation_results)


def test_mock_never_removes_a_protected_activity(tmp_path: Path) -> None:
    state = _run(tmp_path)
    removed = {
        op.activity
        for p in state.proposals.proposals
        for op in p.edit_operations
        if op.op == "remove_activity"
    }
    assert not removed & set(PROTECTED)


def test_revision_offers_fresh_proposals_first(tmp_path: Path) -> None:
    first = _run(tmp_path / "a").proposals.proposals
    revised = _run(tmp_path / "b", comment="Something else, please.")
    assert revised.status == "awaiting_review" and revised.revision_count == 1
    # Only three candidates exist, so the revision repeats them; it must still be valid.
    assert {p.title for p in revised.proposals.proposals} == {p.title for p in first}


def test_mock_is_deterministic(tmp_path: Path) -> None:
    a = _run(tmp_path / "a")
    b = _run(tmp_path / "b")
    assert a.diagnosis == b.diagnosis and a.proposals == b.proposals


def test_unknown_segment_or_schema_is_a_parse_error_not_an_exception(tmp_path: Path) -> None:
    llm = MockLLM(write_run_dir(tmp_path))
    no_segment = llm.invoke([ChatTurn(role="user", content="no segment line")], Diagnosis)
    assert no_segment.parsed is None and "Segment" in no_segment.parse_error
    turns = [ChatTurn(role="user", content=f"Segment: {SEGMENT}\n")]
    assert llm.invoke(turns, ChatTurn).parse_error.startswith("mock: no rule")
    # No as-is model: redesign cannot be built, and says why.
    (tmp_path / "20260101T000000Z" / SLUG / "as_is_tree.json").unlink()
    assert "no as-is model" in llm.invoke(turns, RedesignSet).parse_error


def test_create_llm_picks_the_mock_by_id(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path)
    assert isinstance(create_llm("mock", agent_settings(), run_dir), MockLLM)
    assert create_llm("anthropic:claude-opus-5-5", agent_settings(), run_dir).model_id == (
        "anthropic:claude-opus-5-5"
    )
