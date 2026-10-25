"""Soundness of a to-be model, checked in three layers.

1. **By construction.** Every model is rendered from a process tree, and a
   process tree always translates to a sound workflow net (block structure:
   every split has its matching join). This is the guarantee.
2. **Structural check of the rendered BPMN** (always). The guarantee only
   holds if the tree-to-BPMN rendering is correct, so the rendered graph is
   checked: one start and one end event, every element reachable from the
   start and able to reach the end, every task with exactly one incoming and
   one outgoing flow, no gateway that neither splits nor joins.
3. **Woflan** (pm4py, ``pm4py.algo.analysis.woflan``; the non-deprecated
   route -- ``pm4py.check_soundness`` is deprecated since 2.3.0) as an
   independent check on the Petri net of the rendered BPMN, but only up to
   ``woflan_max_transitions``. WHY a size gate: Woflan explores the state
   space, and on BPI 2019's larger models (91 and 126 transitions, many loops
   and parallel blocks) it did not finish within two minutes. The gate is a
   count, not a time limit, so the verdict does not depend on machine speed.

The result always states which layers ran.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel

from processpilot.process_tree import TreeNode, to_bpmn

WoflanOutcome = Literal["sound", "not_sound", "skipped_too_large"]


class SoundnessResult(BaseModel):
    """Verdict plus how it was reached."""

    sound: bool
    by_construction: bool
    structural_issues: list[str]
    woflan: WoflanOutcome
    petri_net_transitions: int
    woflan_max_transitions: int
    method: str


def structural_issues(bpmn: Any) -> list[str]:
    """Problems in a rendered BPMN graph; empty when well-formed."""
    nodes = {n.get_id(): n for n in bpmn.get_nodes()}
    succ: dict[str, set[str]] = defaultdict(set)
    pred: dict[str, set[str]] = defaultdict(set)
    for f in bpmn.get_flows():
        s, t = f.get_source().get_id(), f.get_target().get_id()
        succ[s].add(t)
        pred[t].add(s)

    kinds = {i: type(n).__name__ for i, n in nodes.items()}
    starts = [i for i, k in kinds.items() if k.endswith("StartEvent")]
    ends = [i for i, k in kinds.items() if k.endswith("EndEvent")]
    issues: list[str] = []
    if len(starts) != 1 or len(ends) != 1:
        issues.append(f"expected one start and one end event, got {len(starts)} and {len(ends)}")
        return issues

    def reach(origin: str, edges: dict[str, set[str]]) -> set[str]:
        seen, stack = {origin}, [origin]
        while stack:
            for nxt in edges[stack.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    unreachable = set(nodes) - reach(starts[0], succ)
    dead_end = set(nodes) - reach(ends[0], pred)
    if unreachable:
        issues.append(f"not reachable from the start: {sorted(unreachable)}")
    if dead_end:
        issues.append(f"cannot reach the end: {sorted(dead_end)}")
    for i, kind in sorted(kinds.items()):
        n_in, n_out = len(pred[i]), len(succ[i])
        if kind == "Task" and (n_in, n_out) != (1, 1):
            issues.append(f"task {i} has {n_in} incoming and {n_out} outgoing flows")
        if kind.endswith("Gateway") and (n_in < 1 or n_out < 1 or (n_in == 1 and n_out == 1)):
            issues.append(f"gateway {i} neither splits nor joins ({n_in} in, {n_out} out)")
    return issues


def check_soundness(tree: TreeNode, woflan_max_transitions: int) -> SoundnessResult:
    """Run the three layers on the BPMN rendered from ``tree``."""
    import pm4py
    from pm4py.algo.analysis.woflan import algorithm as woflan

    bpmn = to_bpmn(tree)
    issues = structural_issues(bpmn)
    net, im, fm = pm4py.convert_to_petri_net(bpmn)
    n_trans = len(net.transitions)
    outcome: WoflanOutcome
    if n_trans > woflan_max_transitions:
        outcome = "skipped_too_large"
    else:
        ok = woflan.apply(
            net, im, fm, parameters={"print_diagnostics": False, "return_asap_when_not_sound": True}
        )
        outcome = "sound" if ok else "not_sound"
    sound = not issues and outcome != "not_sound"
    woflan_text = {
        "sound": "confirmed by Woflan",
        "not_sound": "Woflan found it NOT sound",
        "skipped_too_large": (
            f"Woflan skipped ({n_trans} Petri-net transitions > {woflan_max_transitions})"
        ),
    }[outcome]
    structure_text = "structure OK" if not issues else f"{len(issues)} structural issue(s)"
    return SoundnessResult(
        sound=sound,
        by_construction=True,
        structural_issues=issues,
        woflan=outcome,
        petri_net_transitions=n_trans,
        woflan_max_transitions=woflan_max_transitions,
        method=f"sound by construction (process tree); {structure_text}; {woflan_text}",
    )
