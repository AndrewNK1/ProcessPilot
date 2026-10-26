"""Process trees, edit operations, soundness and the impact ESTIMATE.

Every expected tree and number below is worked out by hand on two fixtures:

``T`` = ->('A', X('B', 'C'), 'D', 'E')  -- A, then B or C, then D, then E.

``waits`` (hours; '-' = first event, no wait):
    x1: A -, B 1, D 2, E 3   -> throughput 6
    x2: A -, C 4, D 5, E 1   -> throughput 10
    as-is median 8; median of all waits [1, 2, 3, 4, 5, 1] = 2.5
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from processpilot.process_tree import (
    TreeNode,
    leaf,
    node,
    normalize,
    to_bpmn,
    to_text,
    write_bpmn,
)
from processpilot.redesign.bpmn_edit import apply_proposal, load_tree, save_tree
from processpilot.redesign.operations import (
    AddActivity,
    AddCheck,
    AutomateActivity,
    EditError,
    MergeActivities,
    ParallelizeActivities,
    RemoveActivity,
    ReorderActivities,
    apply_operation,
    apply_operations,
)
from processpilot.redesign.simulate import EstimateSettings, estimate_impact
from processpilot.redesign.soundness import check_soundness, structural_issues

H = 3600.0


@pytest.fixture
def tree() -> TreeNode:
    return node("sequence", leaf("A"), node("xor", leaf("B"), leaf("C")), leaf("D"), leaf("E"))


@pytest.fixture
def waits() -> pd.DataFrame:
    rows = [
        ("x1", "A", None),
        ("x1", "B", 1),
        ("x1", "D", 2),
        ("x1", "E", 3),
        ("x2", "A", None),
        ("x2", "C", 4),
        ("x2", "D", 5),
        ("x2", "E", 1),
    ]
    df = pd.DataFrame(rows, columns=["case_id", "activity", "wait_seconds"])
    df["wait_seconds"] = df["wait_seconds"].astype(float) * H
    return df


SETTINGS = EstimateSettings(automate_wait_reduction=0.5, added_activity_wait="segment_median")


# --- process tree ---------------------------------------------------------------


def test_normalize_flattens_and_drops_silent_steps() -> None:
    t = node(
        "sequence",
        leaf("A"),
        node("sequence", leaf(None), leaf("B")),
        node("xor", leaf("C"), leaf(None), leaf(None), node("xor", leaf("D"))),
        node("parallel", leaf("E")),
    )
    assert to_text(normalize(t)) == "->('A', 'B', X('C', 'D', tau), 'E')"


def test_removing_a_loops_only_task_leaves_no_empty_loop() -> None:
    t = node("sequence", leaf("A"), node("loop", leaf("B"), leaf(None)), leaf("C"))
    assert to_text(apply_operation(t, RemoveActivity(activity="B"))) == "->('A', 'C')"


def test_tree_shape_is_validated() -> None:
    with pytest.raises(ValueError, match="leaf cannot have children"):
        TreeNode(label="A", children=[leaf("B")])
    with pytest.raises(ValueError, match="loop needs at least 2"):
        node("loop", leaf("A"))


def test_bpmn_ids_are_deterministic_and_named_after_activities(tree: TreeNode) -> None:
    a, b = to_bpmn(tree), to_bpmn(tree)
    ids = sorted(n.get_id() for n in a.get_nodes())
    assert ids == sorted(n.get_id() for n in b.get_nodes())
    assert {"start", "end", "task_a", "task_b", "task_c", "task_d", "task_e"} <= set(ids)
    assert {"gw_n_1_split", "gw_n_1_join"} <= set(ids)
    assert sorted(f.get_id() for f in a.get_flows()) == sorted(f.get_id() for f in b.get_flows())


def test_task_ids_survive_an_edit(tree: TreeNode) -> None:
    to_be = apply_operation(tree, AddActivity(name="N", after="A"))
    before = {n.get_name(): n.get_id() for n in to_bpmn(tree).get_nodes()}
    after = {n.get_name(): n.get_id() for n in to_bpmn(to_be).get_nodes()}
    for name in ("A", "B", "C", "D", "E"):
        assert after[name] == before[name]


@pytest.mark.parametrize(
    "t",
    [
        node("sequence", leaf("A"), leaf("B")),
        node("xor", leaf("A"), leaf(None)),
        node("parallel", leaf("A"), leaf("B"), leaf("C")),
        node("loop", leaf("A"), leaf("B")),
        node("loop", leaf(None), leaf("A")),
        node(
            "sequence", leaf("A"), node("loop", node("parallel", leaf("B"), leaf("C")), leaf(None))
        ),
    ],
    ids=["seq", "optional", "parallel", "loop", "loop-silent-do", "nested"],
)
def test_rendered_bpmn_is_structurally_sound(t: TreeNode) -> None:
    assert structural_issues(to_bpmn(t)) == []
    result = check_soundness(t, woflan_max_transitions=40)
    assert result.sound and result.woflan == "sound"


def test_structural_check_catches_a_dead_end(tree: TreeNode) -> None:
    from pm4py.objects.bpmn.obj import BPMN

    bpmn = to_bpmn(tree)
    bpmn.add_node(BPMN.Task(id="orphan", name="Orphan"))
    issues = structural_issues(bpmn)
    assert any("not reachable from the start" in i and "orphan" in i for i in issues)
    assert any("task orphan has 0 incoming" in i for i in issues)


def test_woflan_is_skipped_above_the_size_gate(tree: TreeNode) -> None:
    result = check_soundness(tree, woflan_max_transitions=1)
    assert result.woflan == "skipped_too_large" and result.sound
    assert "Woflan skipped" in result.method and "by construction" in result.method


def test_tree_json_and_bpmn_file_round_trip(tree: TreeNode, tmp_path: Path) -> None:
    import pm4py

    save_tree(tree, tmp_path / "t.json")
    assert load_tree(tmp_path / "t.json") == tree
    write_bpmn(tree, tmp_path / "t.bpmn")
    read = pm4py.read_bpmn(str(tmp_path / "t.bpmn"))
    assert {"task_a", "task_e"} <= {n.get_id() for n in read.get_nodes()}


def test_bpmn_file_is_byte_identical_on_rewrite(tmp_path: Path) -> None:
    # pm4py writes set-ordered elements and fresh uuid4 diagram/plane ids; one of
    # each operator gives the set order room to differ.
    t = node(
        "sequence",
        leaf("A"),
        node("xor", leaf("B"), leaf(None)),
        node("parallel", leaf("C"), leaf("D")),
        node("loop", leaf("E"), leaf("F")),
    )
    write_bpmn(t, tmp_path / "1.bpmn", process_id="as_is")
    write_bpmn(t, tmp_path / "2.bpmn", process_id="as_is")
    text = (tmp_path / "1.bpmn").read_text(encoding="utf-8")
    assert (tmp_path / "1.bpmn").read_bytes() == (tmp_path / "2.bpmn").read_bytes()
    # No random ids left, including pm4py's per-interpreter default process id.
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)
    assert 'id="idas_is"' in text and 'id="idas_is_diagram"' in text


# --- operations -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        (RemoveActivity(activity="D"), "->('A', X('B', 'C'), 'E')"),
        (RemoveActivity(activity="B"), "->('A', X('C', tau), 'D', 'E')"),
        (AddActivity(name="N", after="D"), "->('A', X('B', 'C'), 'D', 'N', 'E')"),
        (AddCheck(name="K", after="B"), "->('A', X(->('B', check 'K'), 'C'), 'D', 'E')"),
        (AutomateActivity(activity="E"), "->('A', X('B', 'C'), 'D', automated 'E')"),
        (ReorderActivities(activity="A", after="D"), "->(X('B', 'C'), 'D', 'A', 'E')"),
        (ParallelizeActivities(activities=["E", "D"]), "->('A', X('B', 'C'), +('D', 'E'))"),
        (MergeActivities(activities=["D", "E"], merged_name="DE"), "->('A', X('B', 'C'), 'DE')"),
        (MergeActivities(activities=["D", "E"], merged_name="D"), "->('A', X('B', 'C'), 'D')"),
    ],
)
def test_operation_results(tree: TreeNode, op: object, expected: str) -> None:
    assert to_text(apply_operation(tree, op)) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("op", "reason"),
    [
        (RemoveActivity(activity="Z"), "'Z' is not an activity of the current model"),
        (ParallelizeActivities(activities=["A", "D"]), "not adjacent; between them: B, C"),
        (ParallelizeActivities(activities=["B", "D"]), "must be steps of one sequence"),
        (MergeActivities(activities=["D", "D"], merged_name="X"), "listed more than once"),
        (MergeActivities(activities=["D", "E"], merged_name="A"), "'A' already exists"),
        (AddCheck(name="B", after="A"), "'B' already exists"),
        (ReorderActivities(activity="D", after="D"), "cannot be moved after itself"),
    ],
)
def test_inapplicable_operations_explain_why(tree: TreeNode, op: object, reason: str) -> None:
    with pytest.raises(EditError, match=reason):
        apply_operation(tree, op)  # type: ignore[arg-type]


def test_protected_activity_rules(tree: TreeNode) -> None:
    protected = frozenset({"E"})
    for op in (
        RemoveActivity(activity="E"),
        ReorderActivities(activity="E", after="A"),
        ParallelizeActivities(activities=["D", "E"]),
    ):
        with pytest.raises(EditError, match="protected"):
            apply_operation(tree, op, protected=protected)
    # Automating it, or anchoring a new step on it, is allowed (Phase 4 decision).
    apply_operation(tree, AutomateActivity(activity="E"), protected=protected)
    apply_operation(tree, AddCheck(name="K", after="E"), protected=protected)


def test_automating_twice_is_an_error(tree: TreeNode) -> None:
    once = apply_operation(tree, AutomateActivity(activity="D"))
    with pytest.raises(EditError, match="already automated"):
        apply_operation(once, AutomateActivity(activity="D"))


def test_operations_compose_and_the_error_names_the_failing_index(tree: TreeNode) -> None:
    ops = [AddActivity(name="N", after="A"), RemoveActivity(activity="N")]
    assert to_text(apply_operations(tree, ops)) == to_text(tree)
    with pytest.raises(EditError) as exc:
        apply_operations(tree, [AddActivity(name="N", after="A"), RemoveActivity(activity="Q")])
    assert exc.value.index == 1


def test_diff_lists_what_changed(tree: TreeNode) -> None:
    ops = [
        MergeActivities(activities=["D", "E"], merged_name="DE"),
        AddCheck(name="K", after="A"),
        AutomateActivity(activity="C"),
    ]
    _, diff = apply_proposal(tree, ops)
    assert diff.added == ["DE", "K"]
    assert diff.checks_added == ["K"]
    assert diff.removed == ["D", "E"]
    assert diff.automated == ["C"]
    assert diff.changes[1] == "add check 'K' after 'A'"


# --- impact ESTIMATE ------------------------------------------------------------


def test_as_is_replay_reproduces_throughput(waits: pd.DataFrame) -> None:
    est = estimate_impact(waits, [], SETTINGS)
    assert est.label == "ESTIMATE"
    assert est.as_is == est.to_be
    assert est.as_is.median_seconds == 8 * H
    assert est.cases_affected == 0


@pytest.mark.parametrize(
    ("op", "to_be_median_h", "affected"),
    [
        (RemoveActivity(activity="D"), 4.5, 2),  # 6-2, 10-5
        (AutomateActivity(activity="E"), 7.0, 2),  # 6-1.5, 10-0.5
        (AddActivity(name="N", after="B"), 9.25, 1),  # x1 6+2.5
        (ParallelizeActivities(activities=["D", "E"]), 6.5, 2),  # 6-2, 10-1
        (ParallelizeActivities(activities=["B", "E"]), 8.0, 0),  # never consecutive
    ],
)
def test_estimate_per_operation(
    waits: pd.DataFrame, op: object, to_be_median_h: float, affected: int
) -> None:
    est = estimate_impact(waits, [op], SETTINGS)  # type: ignore[list-item]
    assert est.to_be.median_seconds == pytest.approx(to_be_median_h * H)
    assert est.cases_affected == affected
    assert len(est.assumptions) == 2 and not est.not_estimated


def test_added_wait_can_be_assumed_zero(waits: pd.DataFrame) -> None:
    zero = SETTINGS.model_copy(update={"added_activity_wait": "zero"})
    est = estimate_impact(waits, [AddCheck(name="K", after="B")], zero)
    assert est.to_be.median_seconds == 8 * H
    assert "assumed zero" in est.assumptions[1]


def test_reorder_and_merge_are_not_estimated(waits: pd.DataFrame) -> None:
    est = estimate_impact(
        waits,
        [
            ReorderActivities(activity="A", after="D"),
            MergeActivities(activities=["D", "E"], merged_name="DE"),
        ],
        SETTINGS,
    )
    assert est.median_change_seconds == 0
    assert len(est.not_estimated) == 2
    assert est.not_estimated[0].startswith("move 'A' to directly after 'D'")


def test_removing_a_case_start_saves_nothing_and_says_so(waits: pd.DataFrame) -> None:
    est = estimate_impact(waits, [RemoveActivity(activity="A")], SETTINGS)
    assert est.median_change_seconds == 0
    assert "In 2 case(s) it is the first event" in est.assumptions[1]
