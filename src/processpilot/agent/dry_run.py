"""A scripted stand-in model for dry runs: the full graph, zero API calls.

WHY this exists although the app has no stand-in model (user decision
2026-11-02): notebooks and the evaluation scripts must be runnable end to end
without spending quota (EVALUATION_PLAN.md, rule 7), and a dry run must
exercise exactly the code a real run does: validation, edits, soundness,
estimate, review interrupt. The app never offers it, so an analyst cannot
mistake its output for a model's.

The canned outputs are built from the segment's own evidence and model so they
pass validation: they cite existing ids, name only modelled activities and
contain no numbers. They carry no analysis; every text says it is a dry run.
Its runs are logged under the model id ``scripted:dry-run`` with no token
counts, so they cannot be confused with, or summed into, real runs.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from pydantic import BaseModel

from processpilot.agent.llm import ChatTurn, LLMResponse, TokenUsage
from processpilot.agent.tools import EvidenceTools

DRY_RUN_MODEL_ID = "scripted:dry-run"
# A catalogue heuristic whose operation (automate_activity) applies to any
# modelled activity, protected ones included (user decision, Phase 4).
_HEURISTIC = "H-AUTOMATION"
_NOTE = "Dry run with a scripted model: placeholder text, not an analysis."


def _cited_ids(tools: EvidenceTools) -> list[str]:
    """The top bottleneck if there is one, else the first KPI or any item."""
    for items in (tools.get_top_bottlenecks(1), tools.get_kpis(), tools.all_items()):
        if items:
            return [items[0].id]
    raise ValueError(f"segment {tools.segment!r} has no evidence to cite")


def _automation_targets(tools: EvidenceTools, activities: list[str]) -> list[str]:
    """Two modelled activities, bottleneck targets first (one if only one exists)."""
    modelled = set(activities)
    ranked = [
        str(b.metrics.get("target"))
        for b in tools.get_top_bottlenecks()
        if b.metrics.get("target") in modelled
    ]
    ordered = list(dict.fromkeys([*ranked, *sorted(modelled)]))
    if not ordered:
        raise ValueError(f"segment {tools.segment!r} has no modelled activity to edit")
    return [ordered[0], ordered[1 % len(ordered)]]


def dry_run_outputs(tools: EvidenceTools, activities: list[str]) -> dict[str, dict[str, Any]]:
    """Valid ``Diagnosis`` and ``RedesignSet`` payloads for one segment."""
    ids = _cited_ids(tools)
    diagnosis = {
        "hypotheses": [
            {
                "description": f"{_NOTE} Restates the cited evidence without interpreting it.",
                "evidence_ids": ids,
                "confidence": "low",
                "alternative_explanations": [_NOTE],
            }
        ]
    }
    proposals = [
        {
            "title": f"Dry run: automate '{activity}'",
            "heuristic_id": _HEURISTIC,
            "edit_operations": [{"op": "automate_activity", "activity": activity}],
            "evidence_ids": ids,
            "rationale": _NOTE,
            "expected_effect": _NOTE,
            "risks": [_NOTE],
            "assumptions": [_NOTE],
        }
        for activity in _automation_targets(tools, activities)
    ]
    return {"Diagnosis": diagnosis, "RedesignSet": {"proposals": proposals}}


class DryRunLLM:
    """Answers each schema with its canned payload; counts the calls it received."""

    model_id = DRY_RUN_MODEL_ID

    def __init__(self, outputs: dict[str, dict[str, Any]]) -> None:
        """``outputs`` maps a schema class name to the payload returned for it."""
        self._outputs = outputs
        self.calls: list[str] = []

    @classmethod
    def for_segment(cls, tools: EvidenceTools, activities: list[str]) -> DryRunLLM:
        """A dry-run model whose answers are valid for this segment."""
        return cls(dry_run_outputs(tools, activities))

    def invoke(self, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResponse:
        """The canned payload for ``schema``; no network, no token counts."""
        self.calls.append(schema.__name__)
        if schema.__name__ not in self._outputs:
            raise KeyError(f"dry-run model has no scripted output for {schema.__name__}")
        payload = copy.deepcopy(self._outputs[schema.__name__])
        return LLMResponse(
            parsed=payload,
            raw_text=json.dumps(payload, ensure_ascii=False),
            stop_reason="scripted",
            served_model=DRY_RUN_MODEL_ID,
            usage=TokenUsage(),
        )
