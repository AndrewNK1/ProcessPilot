"""As-is process discovery: BPMN model, DFG, and performance DFG.

WHY the inductive miner: it is the only mainstream discovery algorithm that
guarantees a sound model, and ``apply_edits`` later needs a structurally valid
BPMN to transform. The noise threshold is configurable because real logs like
BPI 2019 produce an unreadable flower model at threshold 0.

The DFG and performance DFG are computed in pandas from the canonical order
(see ``prepare``), so they agree exactly with the bottleneck numbers. pm4py is
used only for the BPMN model (``pm4py.discover_bpmn_inductive``, verified
against pm4py 2.7.23.11). Writing files is the caller's job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
from pydantic import BaseModel

from processpilot.mining.prepare import directly_follows_pairs
from processpilot.schema import PM4PY_ACTIVITY, PM4PY_CASE_ID, PM4PY_TIMESTAMP, to_pm4py

if TYPE_CHECKING:
    from pm4py.objects.bpmn.obj import BPMN


class DFGEdge(BaseModel):
    """One directly-follows edge with frequency and timing."""

    source: str
    target: str
    frequency: int
    case_count: int
    case_share: float
    median_wait_seconds: float
    mean_wait_seconds: float
    p90_wait_seconds: float


class DFGResult(BaseModel):
    """Frequency + performance DFG of one segment."""

    case_count: int
    start_activities: dict[str, int]
    end_activities: dict[str, int]
    edges: list[DFGEdge]

    def filtered(self, min_case_share: float) -> tuple[DFGResult, int]:
        """A display view without rare edges, plus how many were left out."""
        kept = [e for e in self.edges if e.case_share >= min_case_share]
        return self.model_copy(update={"edges": kept}), len(self.edges) - len(kept)


def compute_dfg(df: pd.DataFrame) -> DFGResult:
    """Directly-follows graph with per-edge frequency and wait statistics.

    Edges are ordered by frequency (descending), then source/target names, so
    the JSON output is byte-identical across reruns.
    """
    pairs = directly_follows_pairs(df)
    case_count = int(df["case_id"].nunique())
    edges: list[DFGEdge] = []
    if not pairs.empty:
        grouped = pairs.groupby(["source", "target"], sort=True)
        stats = grouped.agg(
            frequency=("wait_seconds", "size"),
            case_count=("case_id", "nunique"),
            median_wait_seconds=("wait_seconds", "median"),
            mean_wait_seconds=("wait_seconds", "mean"),
        )
        stats["p90_wait_seconds"] = grouped["wait_seconds"].quantile(0.9)
        stats = stats.reset_index().sort_values(
            ["frequency", "source", "target"], ascending=[False, True, True], kind="stable"
        )
        edges = [
            DFGEdge(
                source=str(r.source),
                target=str(r.target),
                frequency=int(r.frequency),
                case_count=int(r.case_count),
                case_share=float(r.case_count) / case_count,
                median_wait_seconds=float(r.median_wait_seconds),
                mean_wait_seconds=float(r.mean_wait_seconds),
                p90_wait_seconds=float(r.p90_wait_seconds),
            )
            for r in stats.itertuples(index=False)
        ]

    by_case = df.groupby("case_id", sort=True)["activity"]
    starts = by_case.first().value_counts()
    ends = by_case.last().value_counts()
    return DFGResult(
        case_count=case_count,
        start_activities={str(k): int(v) for k, v in sorted(starts.items())},
        end_activities={str(k): int(v) for k, v in sorted(ends.items())},
        edges=edges,
    )


def discover_bpmn(df: pd.DataFrame, noise_threshold: float) -> BPMN:
    """Inductive-miner BPMN model of the segment (pm4py object, not written)."""
    import pm4py

    log = to_pm4py(df[["case_id", "activity", "timestamp"]])
    return pm4py.discover_bpmn_inductive(
        log,
        noise_threshold=noise_threshold,
        activity_key=PM4PY_ACTIVITY,
        timestamp_key=PM4PY_TIMESTAMP,
        case_id_key=PM4PY_CASE_ID,
    )
