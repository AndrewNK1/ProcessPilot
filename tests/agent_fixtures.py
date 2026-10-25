"""Hand-built evidence and a scripted fake model for the agent tests.

WHY a fake model: unit tests must never call a real LLM (cost, flakiness,
non-reproducibility). ``ScriptedLLM`` returns pre-written outputs in order and
records every prompt it received, so a test can assert both what the graph did
with an output and what it fed back on a retry.

The synthetic segment "Seg A" has five activities; "Pay Invoice" is protected.
Every number in the evidence summaries below is deliberate: the number check
is tested against them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

from processpilot.agent.catalogue import AgentSettings, ModelSettings, Price
from processpilot.agent.llm import ChatTurn, LLMResponse, TokenUsage
from processpilot.mining.evidence import EvidenceItem, EvidenceStore
from processpilot.mining.run import AS_IS_TREE_FILE, CASE_WAITS_FILE
from processpilot.process_tree import TreeNode, leaf, node
from processpilot.redesign.bpmn_edit import RedesignSettings, save_tree
from processpilot.redesign.simulate import EstimateSettings

SEGMENT = "Seg A"
SLUG = "seg-a"
ACTIVITIES = ["Create Order", "Approve Order", "Receive Goods", "Record Invoice", "Pay Invoice"]
PROTECTED = ["Pay Invoice"]


def evidence_items(segment: str = SEGMENT, slug: str = SLUG) -> list[EvidenceItem]:
    """Seven items covering every evidence type."""
    return [
        EvidenceItem(
            id=f"KPI-01-{slug}",
            segment=segment,
            type="kpi",
            summary=(
                f"Segment '{segment}' has 10 cases and 50 events (0 same-instant repeats "
                "collapsed); median case throughput is 12.0 days (mean 15.5 days, P90 30.0 days)."
            ),
            metrics={"case_count": 10},
        ),
        EvidenceItem(
            id=f"BN-01-{slug}",
            segment=segment,
            type="bottleneck",
            summary=(
                "Transition 'Approve Order' -> 'Receive Goods' occurs in 80.0% of cases (8) "
                "with a median wait of 5.0 days (mean 6.2 days, P90 9.0 days); impact rank 1."
            ),
            metrics={"source": "Approve Order", "target": "Receive Goods", "rank": 1},
        ),
        EvidenceItem(
            id=f"BN-02-{slug}",
            segment=segment,
            type="bottleneck",
            summary=(
                "Transition 'Record Invoice' -> 'Pay Invoice' occurs in 90.0% of cases (9) "
                "with a median wait of 29.3 days (mean 31.0 days, P90 45.0 days); impact rank 2."
            ),
            metrics={"source": "Record Invoice", "target": "Pay Invoice", "rank": 2},
        ),
        EvidenceItem(
            id=f"VA-01-{slug}",
            segment=segment,
            type="variant",
            summary=(
                "A 5-step path followed by 60.0% of cases (6) is the happy path; its median "
                "throughput is 11.0 days."
            ),
            metrics={"rank": 1, "activities": ACTIVITIES},
        ),
        EvidenceItem(
            id=f"RW-01-{slug}",
            segment=segment,
            type="rework",
            summary=(
                "'Approve Order' is repeated within 30.0% of cases (3; 4 extra executions); "
                "those cases take a median 6.0 days longer than cases performing it once."
            ),
            metrics={"activity": "Approve Order", "rank": 1},
        ),
        EvidenceItem(
            id=f"RS-00-{slug}",
            segment=segment,
            type="resource",
            summary="Resource analysis was not performed: no resource column.",
            metrics={"available": False},
        ),
        EvidenceItem(
            id=f"CF-R-01-{slug}",
            segment=segment,
            type="conformance",
            summary="Rule R-01 (order_before_pay) is violated in 1 of 10 evaluated cases (10.0%).",
            metrics={"rule_id": "R-01", "description": "Order must precede payment."},
        ),
    ]


def as_is_tree() -> TreeNode:
    """The five activities in strict sequence."""
    return node("sequence", *(leaf(a) for a in ACTIVITIES))


def case_waits() -> pd.DataFrame:
    """Two cases following the sequence; waits in hours (NaN on the first event).

    k1: -, 1, 10, 2, 4  -> throughput 17 h
    k2: -, 3, 20, 2, 6  -> throughput 31 h
    """
    rows = [("k1", a, w) for a, w in zip(ACTIVITIES, [None, 1, 10, 2, 4], strict=True)]
    rows += [("k2", a, w) for a, w in zip(ACTIVITIES, [None, 3, 20, 2, 6], strict=True)]
    df = pd.DataFrame(rows, columns=["case_id", "activity", "wait_seconds"])
    df["wait_seconds"] = df["wait_seconds"].astype(float) * 3600
    return df


def write_run_dir(root: Path, with_model: bool = True) -> Path:
    """A mining-run folder with evidence.json and the segment's dfg.json.

    By default also the as-is tree and per-event waits, as mining writes them;
    ``with_model=False`` mimics a run mined before Phase 4.
    """
    run_dir = root / "20260101T000000Z"
    store = EvidenceStore(items=evidence_items())
    store.to_json(run_dir / "evidence.json")
    if with_model:
        seg_dir = run_dir / SLUG
        save_tree(as_is_tree(), seg_dir / AS_IS_TREE_FILE)
        case_waits().to_parquet(seg_dir / CASE_WAITS_FILE, index=False)
    edges = [
        {"source": a, "target": b, "frequency": 8}
        for a, b in zip(ACTIVITIES, ACTIVITIES[1:], strict=False)
    ]
    (run_dir / SLUG).mkdir(parents=True, exist_ok=True)
    (run_dir / SLUG / "dfg.json").write_text(
        json.dumps(
            {
                "case_count": 10,
                "start_activities": {"Create Order": 10},
                "end_activities": {"Pay Invoice": 9},
                "edges": edges,
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def preset() -> dict[str, Any]:
    """Minimal preset: constraints only, no domain knowledge file."""
    return {
        "redesign": {
            "protected_activities": PROTECTED,
            "constraints_text": ["Payment is a financial control."],
        }
    }


def agent_settings(max_retries: int = 2, max_revision_loops: int = 2) -> AgentSettings:
    """Settings independent of configs/agent.yaml."""
    return AgentSettings(
        model=ModelSettings(env_var="PP_MODEL", structured_output_method="json_schema"),
        max_retries=max_retries,
        max_revision_loops=max_revision_loops,
        redesign=RedesignSettings(
            estimate=EstimateSettings(
                automate_wait_reduction=0.5, added_activity_wait="segment_median"
            ),
            woflan_max_transitions=40,
        ),
        pricing_usd_per_mtok={
            "claude-opus-5-5": Price(input=4.0, output=20.0, cache_read=0.2, cache_write=5.0)
        },
    )


def valid_diagnosis() -> dict[str, Any]:
    """Cites existing ids; every number appears in the cited items."""
    return {
        "hypotheses": [
            {
                "description": (
                    f"Approvals are repeated in 30.0% of cases [RW-01-{SLUG}], and the wait "
                    f"into goods receipt has a median of 5 days [BN-01-{SLUG}]."
                ),
                "evidence_ids": [f"RW-01-{SLUG}", f"BN-01-{SLUG}"],
                "confidence": "medium",
                "alternative_explanations": ["Cases still open at extraction inflate waits."],
            }
        ]
    }


def valid_redesign() -> dict[str, Any]:
    """Two catalogue-bound proposals that touch no protected activity."""
    return {
        "proposals": [
            {
                "title": "Combine order creation and approval",
                "heuristic_id": "H-TASK-COMPOSITION",
                "edit_operations": [
                    {
                        "op": "merge_activities",
                        "activities": ["Create Order", "Approve Order"],
                        "merged_name": "Create and Approve Order",
                    }
                ],
                "evidence_ids": [f"RW-01-{SLUG}", f"BN-01-{SLUG}"],
                "rationale": "Repeated approvals add waiting between the two steps.",
                "expected_effect": "Fewer approval loops and less waiting before goods receipt.",
                "risks": ["Weakens segregation of duties."],
                "assumptions": ["Approval rules can be applied at creation."],
            },
            {
                "title": "Record the invoice while goods are in transit",
                "heuristic_id": "H-PARALLELISM",
                "edit_operations": [
                    {
                        "op": "parallelize_activities",
                        "activities": ["Receive Goods", "Record Invoice"],
                    }
                ],
                "evidence_ids": [f"BN-01-{SLUG}"],
                "rationale": "Invoice recording does not need the goods receipt.",
                "expected_effect": "Shorter path to payment readiness.",
                "risks": ["Invoices for goods that never arrive."],
                "assumptions": ["Invoices arrive before goods."],
            },
        ]
    }


class ParseFailure:
    """Scripted output that simulates the provider returning unparseable output."""

    def __init__(self, message: str = "invalid JSON") -> None:
        """Message becomes the parse error."""
        self.message = message


class ScriptedLLM:
    """Returns scripted outputs in order; records every call."""

    model_id = "anthropic:claude-opus-5-5"

    def __init__(self, outputs: list[dict[str, Any] | ParseFailure | Exception]) -> None:
        """``outputs`` are consumed one per call."""
        self.outputs = list(outputs)
        self.calls: list[tuple[list[ChatTurn], type[BaseModel]]] = []

    def invoke(self, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResponse:
        """Next scripted output; fails the test if the script ran out."""
        self.calls.append((list(turns), schema))
        if not self.outputs:
            raise AssertionError(f"ScriptedLLM ran out of outputs (call {len(self.calls)})")
        out = self.outputs.pop(0)
        usage = TokenUsage(input_tokens=1000, output_tokens=200)
        if isinstance(out, Exception):
            raise out
        if isinstance(out, ParseFailure):
            return LLMResponse(parse_error=out.message, raw_text="{not json", usage=usage)
        return LLMResponse(parsed=out, raw_text=json.dumps(out), usage=usage)
