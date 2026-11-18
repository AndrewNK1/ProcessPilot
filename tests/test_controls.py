"""Control rules in code (evaluation Phase 3): protection per segment, unverifiable flags.

The protection tests read the real ``configs/bpi2019.yaml`` so the segment
lists under test are the ones the evaluation uses. They run on a hand-built
tree that contains every activity involved: on the real models Record Goods
Receipt is absent from 2-way match, so a removal there would be rejected as
``unknown_activity`` and could not show that protection allows it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_fixtures import (
    SEGMENT,
    SLUG,
    ScriptedLLM,
    agent_settings,
    evidence_items,
    preset,
    valid_diagnosis,
    valid_redesign,
    write_run_dir,
)
from processpilot.agent.catalogue import load_heuristics
from processpilot.agent.controls import ControlRules
from processpilot.agent.graph import AgentRunner
from processpilot.agent.runlog import RunLogger
from processpilot.agent.state import Constraints, RedesignSet
from processpilot.agent.tools import EvidenceTools
from processpilot.agent.validation import ValidationContext, validate_redesign
from processpilot.config import load_preset_yaml
from processpilot.mining.evidence import EvidenceStore
from processpilot.process_tree import TreeNode, leaf, node
from processpilot.redesign.operations import (
    AddActivity,
    AddCheck,
    AutomateActivity,
    EditError,
    MergeActivities,
    ParallelizeActivities,
    RemoveActivity,
    ReorderActivities,
    apply_operations,
)

BPI_SEGMENTS = (
    "2-way match",
    "3-way match, invoice after GR",
    "3-way match, invoice before GR",
    "Consignment",
)
GR_PROTECTED = BPI_SEGMENTS[1:]
FLAG = "approval/segregation of duties cannot be checked against this log"
P2P = [
    "Create Purchase Order Item",
    "Change Approval for Purchase Order",
    "Release Purchase Order",
    "Record Goods Receipt",
    "Record Invoice Receipt",
    "Clear Invoice",
]


@pytest.fixture(scope="module")
def bpi() -> ControlRules:
    return ControlRules.from_preset(load_preset_yaml("bpi2019"))


@pytest.fixture
def p2p_tree() -> TreeNode:
    return node("sequence", *(leaf(a) for a in P2P))


def _ctx(protected: list[str], tree: TreeNode) -> ValidationContext:
    store = EvidenceStore(items=evidence_items())
    return ValidationContext(
        segment=SEGMENT,
        store=EvidenceTools(store, SEGMENT).segment_store(),
        activities=frozenset(P2P),
        catalogue=load_heuristics(),
        domain=None,
        protected_activities=frozenset(protected),
        as_is_tree=tree,
    )


def _proposals(ops: list[dict]) -> RedesignSet:
    """``valid_redesign`` with the first proposal's operations replaced, the second harmless."""
    raw = valid_redesign()
    raw["proposals"][0]["edit_operations"] = ops
    raw["proposals"][1]["edit_operations"] = [
        {"op": "automate_activity", "activity": "Record Invoice Receipt"}
    ]
    return RedesignSet.model_validate(raw)


# --- protection per segment (the real BPI preset) -------------------------------


def test_bpi_protection_per_segment(bpi: ControlRules) -> None:
    assert bpi.protected_for("2-way match") == ["Clear Invoice"]
    for seg in GR_PROTECTED:
        assert bpi.protected_for(seg) == ["Clear Invoice", "Record Goods Receipt"]
    # The free-text goods-receipt constraint was removed; the list states it.
    assert not any("oods receipt" in t for t in bpi.constraints_text)
    bpi.check_segments(BPI_SEGMENTS)  # every per-segment key is a real segment


@pytest.mark.parametrize("segment", GR_PROTECTED)
def test_removing_goods_receipt_is_rejected_in_3way_and_consignment(
    bpi: ControlRules, p2p_tree: TreeNode, segment: str
) -> None:
    ops = [{"op": "remove_activity", "activity": "Record Goods Receipt"}]
    ctx = _ctx(bpi.protected_for(segment), p2p_tree)
    issues = validate_redesign(_proposals(ops), ctx)
    assert [i.code for i in issues] == ["protected_activity"]
    assert issues[0].path == "proposals[0].edit_operations[0]"
    # apply_operation refuses it too, independent of the validator.
    with pytest.raises(EditError, match="protected"):
        apply_operations(
            p2p_tree,
            [RemoveActivity(activity="Record Goods Receipt")],
            frozenset(bpi.protected_for(segment)),
        )


def test_removing_goods_receipt_is_allowed_in_2way(bpi: ControlRules, p2p_tree: TreeNode) -> None:
    ops = [{"op": "remove_activity", "activity": "Record Goods Receipt"}]
    ctx = _ctx(bpi.protected_for("2-way match"), p2p_tree)
    assert validate_redesign(_proposals(ops), ctx) == []
    out = apply_operations(
        p2p_tree,
        [RemoveActivity(activity="Record Goods Receipt")],
        frozenset(bpi.protected_for("2-way match")),
    )
    assert out != p2p_tree


@pytest.mark.parametrize("segment", BPI_SEGMENTS)
def test_clear_invoice_is_protected_everywhere(
    bpi: ControlRules, p2p_tree: TreeNode, segment: str
) -> None:
    ops = [{"op": "remove_activity", "activity": "Clear Invoice"}]
    issues = validate_redesign(_proposals(ops), _ctx(bpi.protected_for(segment), p2p_tree))
    assert [i.code for i in issues] == ["protected_activity"]


# --- the floor ---------------------------------------------------------------------


def test_floor_keeps_preset_protection_and_adds_the_analysts(bpi: ControlRules) -> None:
    seg = "Consignment"
    requested = Constraints(text=["mine"], protected_activities=["Create Purchase Order Item"])
    merged = bpi.with_floor(seg, requested)
    assert merged.protected_activities == [
        "Clear Invoice",
        "Record Goods Receipt",
        "Create Purchase Order Item",
    ]
    assert merged.text == ["mine"]
    # Unticking everything in the UI does not drop the preset's protection.
    assert bpi.with_floor(seg, Constraints()).protected_activities == bpi.protected_for(seg)
    assert bpi.with_floor(seg, None) == bpi.defaults_for(seg)


def test_per_segment_list_adds_to_the_common_list() -> None:
    rules = ControlRules.model_validate(
        {
            "protected_activities": ["A"],
            "protected_activities_per_segment": {"s1": ["B", "A"]},
        }
    )
    assert rules.protected_for("s1") == ["A", "B"]
    assert rules.protected_for("s2") == ["A"]


def test_misspelt_segment_and_key_fail_loudly() -> None:
    rules = ControlRules.model_validate({"protected_activities_per_segment": {"Consigment": ["A"]}})
    with pytest.raises(ValueError, match="Consigment"):
        rules.check_segments(["Consignment"])
    with pytest.raises(ValidationError):
        ControlRules.model_validate({"protected_activity": ["A"]})


# --- unverifiable-controls flag ------------------------------------------------------


def test_bpi_approval_matching(bpi: ControlRules) -> None:
    approval = bpi.unverifiable_controls["approval"]
    assert approval.flag == FLAG
    for name in (
        "Change Approval for Purchase Order",
        "SRM: Awaiting Approval",
        "Release Purchase Order",
        "Release Purchase Requisition",
        "approve invoice",  # case-insensitive pattern
    ):
        assert approval.matches(name), name
    for name in ("Record Goods Receipt", "Clear Invoice", "Release Hold"):
        assert not approval.matches(name), name


def test_automating_change_approval_passes_but_is_flagged(
    bpi: ControlRules, p2p_tree: TreeNode
) -> None:
    ops = [AutomateActivity(activity="Change Approval for Purchase Order")]
    for segment in BPI_SEGMENTS:
        ctx = _ctx(bpi.protected_for(segment), p2p_tree)
        assert validate_redesign(_proposals([o.model_dump() for o in ops]), ctx) == []
    marks = bpi.flag_proposal(ops)
    assert marks.flags == [FLAG]
    assert marks.activities == ["Change Approval for Purchase Order"]


@pytest.mark.parametrize(
    ("ops", "touched"),
    [
        # The `after` anchor counts (user decision A).
        (
            [AddCheck(name="Three-way check", after="Release Purchase Order")],
            ["Release Purchase Order"],
        ),
        (
            [ReorderActivities(activity="Record Invoice Receipt", after="Release Purchase Order")],
            ["Release Purchase Order"],
        ),
        ([RemoveActivity(activity="Release Purchase Order")], ["Release Purchase Order"]),
        # A new activity whose name matches the pattern counts.
        (
            [AddActivity(name="Approve invoice", after="Record Invoice Receipt")],
            ["Approve invoice"],
        ),
        (
            [
                MergeActivities(
                    activities=["Create Purchase Order Item", "Release Purchase Order"],
                    merged_name="Create and release",
                )
            ],
            ["Release Purchase Order"],
        ),
    ],
)
def test_release_activities_and_anchors_are_flagged(
    bpi: ControlRules, ops: list, touched: list[str]
) -> None:
    marks = bpi.flag_proposal(ops)
    assert marks.flags == [FLAG]
    assert marks.activities == touched


def test_untouched_approvals_raise_no_flag(bpi: ControlRules) -> None:
    ops = [
        ParallelizeActivities(activities=["Record Goods Receipt", "Record Invoice Receipt"]),
        AutomateActivity(activity="Clear Invoice"),
    ]
    marks = bpi.flag_proposal(ops)
    assert marks.flags == [] and marks.activities == []
    assert ControlRules().flag_proposal([RemoveActivity(activity="Approve")]).flags == []


def test_invalid_pattern_fails_at_load() -> None:
    with pytest.raises(ValidationError):
        ControlRules.model_validate(
            {"unverifiable_controls": {"approval": {"patterns": ["("], "flag": "x"}}}
        )


# --- through the graph (scripted model, no LLM) ---------------------------------------


def _flagging_preset(floor: list[str] | None = None) -> dict:
    """The fixture preset plus approval controls and, optionally, a floor for Seg A."""
    p = preset()
    p["redesign"]["protected_activities_per_segment"] = {SEGMENT: floor or []}
    p["redesign"]["unverifiable_controls"] = {
        "approval": {"patterns": ["Approv"], "activities": [], "flag": FLAG}
    }
    return p


def test_flag_reaches_state_payload_and_logs(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path)
    # valid_redesign: proposal 1 merges "Create Order" + "Approve Order" (an
    # approval), proposal 2 parallelizes two steps without one.
    llm = ScriptedLLM([valid_diagnosis(), valid_redesign()])
    with AgentRunner(run_dir, _flagging_preset(), llm=llm, settings=agent_settings()) as runner:
        agent_run_id, state = runner.start(SEGMENT)
        payload = runner.pending_review(SEGMENT, agent_run_id)
    assert state.status == "awaiting_review"
    first, second = state.validation_results
    assert first.status == "validated"  # flagged, not blocked
    assert first.unverifiable_controls == [FLAG]
    # Both the merged approval and the new name it gets (decision A).
    assert first.unverifiable_activities == ["Approve Order", "Create and Approve Order"]
    assert second.unverifiable_controls == [] and second.unverifiable_activities == []
    assert payload["validation_results"][0]["unverifiable_controls"] == [FLAG]

    log = RunLogger(run_dir / SLUG / "agent" / agent_run_id)
    review = json.loads((log.directory / "review_r0.json").read_text(encoding="utf-8"))
    assert review["unverifiable_controls"][0] == {
        "proposal_index": 0,
        "flags": [FLAG],
        "activities": ["Approve Order", "Create and Approve Order"],
    }
    validate_event = next(e for e in log.events() if e.get("node") == "validate")
    assert validate_event["results"][0]["unverifiable_controls"] == [FLAG]
    inputs = json.loads((log.directory / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["preset_protected_activities"] == ["Pay Invoice"]
    assert inputs["unverifiable_controls"]["approval"]["flag"] == FLAG


def test_floor_holds_when_the_request_drops_it(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path)
    # Parallelizing Receive Goods (protected by the preset for Seg A) is
    # rejected on every attempt, although the request protects nothing.
    llm = ScriptedLLM([valid_diagnosis(), valid_redesign(), valid_redesign(), valid_redesign()])
    p = _flagging_preset(floor=["Receive Goods"])
    with AgentRunner(run_dir, p, llm=llm, settings=agent_settings()) as runner:
        _, state = runner.start(SEGMENT, Constraints(text=[], protected_activities=[]))
    assert state.status == "failed"
    assert "protected_activity" in state.error and "Receive Goods" in state.error
    assert state.constraints.protected_activities == ["Pay Invoice", "Receive Goods"]


def test_runner_refuses_a_misspelt_segment(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path)
    p = preset()
    p["redesign"]["protected_activities_per_segment"] = {"Seg Z": ["Pay Invoice"]}
    with pytest.raises(ValueError, match="Seg Z"):
        AgentRunner(run_dir, p, llm=ScriptedLLM([]), settings=agent_settings())
