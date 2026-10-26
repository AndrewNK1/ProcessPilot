"""Rule-based mock model (``PP_MODEL=mock``): the whole pipeline without an API key.

WHY a mock behind the real interface instead of a shortcut around the agent:
the point is to exercise everything a real model's output goes through
(schema parsing, evidence-ID and number checks, trial application of edits,
BPMN writing, the estimate, the review interrupt and the revision loop). So
the mock answers ``invoke(turns, schema)`` like any model, and its output is
validated like any model's.

What it may read is what the real model is shown, in machine-readable form:
the segment's evidence items (``evidence.json``, the store behind the
digest), the as-is model's activities (``as_is_tree.json``) and the
heuristic catalogue. It reads the segment name, the protected activities and
any previous proposals from the prompt. It never opens event data, and it
writes no numbers, so the number check has nothing to object to.

Every text it writes starts with ``[mock]`` and ``model_id`` is ``mock``, so
mock output can never be mistaken for a language model's in a run log. It is
also the rule-based baseline for the evaluation: "what would simple rules
have proposed from the same evidence?"
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from processpilot.agent.catalogue import HeuristicCatalogue, load_heuristics
from processpilot.agent.llm import ChatTurn, LLMResponse
from processpilot.agent.state import (
    AutomateActivity,
    Diagnosis,
    EditOperation,
    RedesignSet,
    RemoveActivity,
)
from processpilot.mining.evidence import EvidenceItem, EvidenceStore, segment_slug
from processpilot.mining.run import AS_IS_TREE_FILE
from processpilot.process_tree import TreeNode, labels
from processpilot.redesign.operations import EditError, apply_operations

MOCK_MODEL_ID = "mock"
_TAG = "[mock]"
_SEGMENT_LINE = re.compile(r"^Segment: (.+)$", re.MULTILINE)
_PROTECTED_LINE = re.compile(r"^Protected activities \(.*?\): (.*)$", re.MULTILINE)
_MAX_HYPOTHESES = 5
_MAX_PROPOSALS = 3
_MIN_PROPOSALS = 2


class MockLLM:
    """Deterministic, rule-based answers for ``Diagnosis`` and ``RedesignSet``."""

    model_id = MOCK_MODEL_ID

    def __init__(self, run_dir: Path, catalogue: HeuristicCatalogue | None = None) -> None:
        """Bind to one mining run's evidence; the catalogue defaults to the configured one."""
        self.run_dir = Path(run_dir)
        self._store = EvidenceStore.from_json(self.run_dir / "evidence.json")
        self._editable = {h.id for h in (catalogue or load_heuristics()).editable()}

    def invoke(self, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResponse:
        """Answer like a model: a parse error instead of an exception when stuck."""
        prompt = next((t.content for t in turns if t.role == "user"), "")
        match = _SEGMENT_LINE.search(prompt)
        if match is None or match.group(1).strip() not in self._store.segments():
            return _error("mock: no known 'Segment:' line in the prompt")
        segment = match.group(1).strip()
        items = self._store.query(segment)
        if schema is Diagnosis:
            return _answer(self._diagnose(items))
        if schema is RedesignSet:
            return self._redesign(segment, items, prompt)
        return _error(f"mock: no rule for schema {schema.__name__}")

    # --- diagnose ------------------------------------------------------------

    def _diagnose(self, items: list[EvidenceItem]) -> dict[str, Any]:
        """One hypothesis per leading finding: top bottlenecks, activity wait, rework."""
        picked = [*_of(items, "bottleneck")[:2], *_of(items, "activity")[:1]]
        picked += _of(items, "rework")[:1]
        if not picked:
            picked = _of(items, "kpi")[:1]
        hypotheses = [
            {
                "description": _describe(item),
                "evidence_ids": [item.id],
                "confidence": "medium" if rank == 0 else "low",
                "alternative_explanations": [_ALTERNATIVES.get(item.type, _ALTERNATIVES["kpi"])],
            }
            for rank, item in enumerate(picked[:_MAX_HYPOTHESES])
        ]
        return {"hypotheses": hypotheses}

    # --- redesign ------------------------------------------------------------

    def _redesign(self, segment: str, items: list[EvidenceItem], prompt: str) -> LLMResponse:
        tree_path = self.run_dir / segment_slug(segment) / AS_IS_TREE_FILE
        if not tree_path.is_file():
            return _error(f"mock: no as-is model at {tree_path}")
        tree = TreeNode.model_validate(json.loads(tree_path.read_text(encoding="utf-8")))
        model = set(labels(tree))
        listed = _protected(prompt)
        protected = model if listed is None else listed
        applicable = [
            _proposal(c)
            for c in self._candidates(items, model, protected)
            if _applies(tree, c[2], protected)
        ]
        # On a revision the previous proposals are in the prompt: offer others first.
        fresh = [c for c in applicable if c["title"] not in prompt]
        ordered = fresh + [c for c in applicable if c["title"] in prompt]
        chosen = ordered[:_MAX_PROPOSALS]
        if len(chosen) < _MIN_PROPOSALS:
            return _error(
                f"mock: found {len(chosen)} applicable rule-based proposal(s), need "
                f"{_MIN_PROPOSALS}; the rules only automate or remove modelled activities"
            )
        return _answer({"proposals": chosen})

    def _candidates(
        self, items: list[EvidenceItem], model: set[str], protected: set[str]
    ) -> list[tuple[str, EvidenceItem, EditOperation]]:
        """(heuristic, evidence, operation), the three rules interleaved, without duplicates.

        Interleaved so the 2-3 proposals shown are alternatives of different
        kinds rather than three automations.
        """
        automate_waits = [
            ("H-AUTOMATION", i, AutomateActivity(activity=i.metrics["activity"]))
            for i in _of(items, "activity")
            if i.metrics.get("activity") in model
        ]
        eliminate_rework = [
            ("H-TASK-ELIMINATION", i, RemoveActivity(activity=i.metrics["activity"]))
            for i in _of(items, "rework")
            if i.metrics.get("activity") in model and i.metrics["activity"] not in protected
        ]
        automate_targets = [
            ("H-AUTOMATION", i, AutomateActivity(activity=i.metrics["target"]))
            for i in _of(items, "bottleneck")
            if i.metrics.get("target") in model
        ]
        rules = [automate_waits, eliminate_rework, automate_targets]
        out = [rule[k] for k in range(max(map(len, rules))) for rule in rules if k < len(rule)]
        seen: set[str] = set()
        unique = []
        for heuristic, item, op in out:
            key = op.model_dump_json()
            if heuristic in self._editable and key not in seen:
                seen.add(key)
                unique.append((heuristic, item, op))
        return unique


# --- helpers -------------------------------------------------------------------

_ALTERNATIVES = {
    "bottleneck": (
        f"{_TAG} The wait may be set by outside parties (vendor response, payment terms) "
        "rather than by internal handling."
    ),
    "activity": (
        f"{_TAG} The queue may reflect batching on fixed days rather than a lack of capacity."
    ),
    "rework": (
        f"{_TAG} Repeats may be legitimate amendments requested by the business, not errors."
    ),
    "kpi": f"{_TAG} The segment may have no single dominant cause of delay.",
}


def _of(items: list[EvidenceItem], type_: str) -> list[EvidenceItem]:
    """Items of one type that report a finding (``available: False`` notices excluded)."""
    return [i for i in items if i.type == type_ and i.metrics.get("available", True)]


def _describe(item: EvidenceItem) -> str:
    m = item.metrics
    if item.type == "bottleneck":
        return (
            f"{_TAG} Cases wait long between '{m['source']}' and '{m['target']}' ({item.id}); "
            "a hand-off or approval queue between these steps is the rule-based suspect."
        )
    if item.type == "activity":
        return (
            f"{_TAG} Work queues up before '{m['activity']}' ({item.id}), whatever step "
            "precedes it."
        )
    if item.type == "rework":
        return (
            f"{_TAG} '{m['activity']}' is repeated within cases ({item.id}); repeats are "
            "associated with longer cases."
        )
    return f"{_TAG} No single finding dominates this segment ({item.id})."


def _proposal(candidate: tuple[str, EvidenceItem, EditOperation]) -> dict[str, Any]:
    heuristic, item, op = candidate
    activity = op.activity  # type: ignore[union-attr]
    if isinstance(op, AutomateActivity):
        title = f"{_TAG} Automate '{activity}'"
        rationale = f"{_TAG} Rule: the step with a leading wait ({item.id}) is automated."
        effect = "Less waiting before the automated step; see the replay ESTIMATE."
        risks = [f"{_TAG} Automation may not suit every case variant of this step."]
        assumptions = [f"{_TAG} The system can perform '{activity}' without manual input."]
    else:
        title = f"{_TAG} Eliminate repeated '{activity}'"
        rationale = f"{_TAG} Rule: the most repeated non-protected step ({item.id}) is removed."
        effect = "Cases no longer wait for or repeat this step; see the replay ESTIMATE."
        risks = [f"{_TAG} The step may carry a control or information the process needs."]
        assumptions = [f"{_TAG} The work of '{activity}' is not needed or is done elsewhere."]
    return {
        "title": title,
        "heuristic_id": heuristic,
        "edit_operations": [op.model_dump()],
        "evidence_ids": [item.id],
        "rationale": rationale,
        "expected_effect": f"{_TAG} {effect}",
        "risks": risks,
        "assumptions": assumptions,
    }


def _applies(tree: TreeNode, op: EditOperation, protected: set[str]) -> bool:
    """The same trial application the validator runs, so the mock never offers a dud."""
    try:
        apply_operations(tree, [op], frozenset(protected))
    except EditError:
        return False
    return True


def _protected(prompt: str) -> set[str] | None:
    """Protected activities from the constraints line; ``None`` if the line is missing."""
    match = _PROTECTED_LINE.search(prompt)
    if match is None:
        return None
    listed = match.group(1).strip()
    return set() if listed == "(none)" else {a.strip() for a in listed.split(",")}


def _answer(parsed: dict[str, Any]) -> LLMResponse:
    return LLMResponse(parsed=parsed, raw_text=json.dumps(parsed, ensure_ascii=False))


def _error(message: str) -> LLMResponse:
    return LLMResponse(parse_error=message, raw_text="")
