"""Resource workload and handover analysis.

WHY handovers get their own metric: waiting time concentrated at a handover
points at an organisational fix (numerical involvement, extra resources) while
the same waiting inside one resource's queue points at a capacity fix. The
heuristic chosen depends on which it is.

Skipped entirely, with an explicit note in the evidence store, when the log has
no resource column -- a silent skip would let the agent assume no resource
problem exists.

Workload is counted on the *raw* canonical log (every posting is work someone
did); handovers on the mining view (collapsed batch postings), so they line up
with the DFG edges.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from processpilot.mining.prepare import directly_follows_pairs


class ResourceWorkload(BaseModel):
    """Events and cases handled by one resource."""

    rank: int
    resource: str
    event_count: int
    event_share: float
    case_count: int
    distinct_activities: int


class HandoverEdge(BaseModel):
    """Waits on one activity edge, split by whether the resource changed."""

    rank: int
    source: str
    target: str
    handover_count: int
    handover_case_count: int
    handover_case_share: float
    handover_share_of_transitions: float
    median_handover_wait_seconds: float
    median_same_resource_wait_seconds: float | None
    impact_score: float


class ResourceAnalysis(BaseModel):
    """Workload + handovers, or an explicit statement that they were skipped."""

    available: bool
    skipped_reason: str | None = None
    unassigned_event_count: int = 0
    median_handover_wait_seconds: float | None = None
    median_same_resource_wait_seconds: float | None = None
    workload: list[ResourceWorkload] = []
    handovers: list[HandoverEdge] = []


def analyse_resources(
    raw_df: pd.DataFrame, mining_df: pd.DataFrame, top_n_resources: int, top_n_handovers: int
) -> ResourceAnalysis:
    """Workload ranking (raw log) and handover-wait ranking (mining view)."""
    if "resource" not in raw_df.columns or raw_df["resource"].isna().all():
        return ResourceAnalysis(
            available=False,
            skipped_reason="the log has no resource column (or it is entirely empty)",
        )

    return ResourceAnalysis(
        available=True,
        unassigned_event_count=int(raw_df["resource"].isna().sum()),
        workload=_workload(raw_df, top_n_resources),
        **_handovers(mining_df, top_n_handovers),
    )


def _workload(df: pd.DataFrame, top_n: int) -> list[ResourceWorkload]:
    assigned = df.loc[df["resource"].notna()]
    stats = (
        assigned.groupby("resource", sort=True)
        .agg(
            event_count=("activity", "size"),
            case_count=("case_id", "nunique"),
            distinct_activities=("activity", "nunique"),
        )
        .reset_index()
        .sort_values(["event_count", "resource"], ascending=[False, True], kind="stable")
    )
    total = len(df)
    return [
        ResourceWorkload(
            rank=rank,
            resource=str(r.resource),
            event_count=int(r.event_count),
            event_share=float(r.event_count) / total,
            case_count=int(r.case_count),
            distinct_activities=int(r.distinct_activities),
        )
        for rank, r in enumerate(stats.head(top_n).itertuples(index=False), start=1)
    ]


def _handovers(df: pd.DataFrame, top_n: int) -> dict[str, object]:
    pairs = directly_follows_pairs(df)
    known = pairs["source_resource"].notna() & pairs["target_resource"].notna()
    pairs = pairs.loc[known].copy()
    pairs["handover"] = (pairs["source_resource"] != pairs["target_resource"]).astype(bool)
    total_cases = int(df["case_id"].nunique())

    edges: list[HandoverEdge] = []
    for (source, target), edge in pairs.groupby(["source", "target"], sort=True):
        hand = edge.loc[edge["handover"]]
        if hand.empty:
            continue
        same = edge.loc[~edge["handover"], "wait_seconds"]
        hand_cases = int(hand["case_id"].nunique())
        median_hand = float(hand["wait_seconds"].median())
        edges.append(
            HandoverEdge(
                rank=0,
                source=str(source),
                target=str(target),
                handover_count=len(hand),
                handover_case_count=hand_cases,
                handover_case_share=hand_cases / total_cases,
                handover_share_of_transitions=len(hand) / len(edge),
                median_handover_wait_seconds=median_hand,
                median_same_resource_wait_seconds=float(same.median()) if len(same) else None,
                impact_score=median_hand * hand_cases / total_cases,
            )
        )
    edges.sort(key=lambda e: (-e.impact_score, e.source, e.target))
    ranked = [e.model_copy(update={"rank": r}) for r, e in enumerate(edges[:top_n], start=1)]

    hand_waits = pairs.loc[pairs["handover"], "wait_seconds"]
    same_waits = pairs.loc[~pairs["handover"], "wait_seconds"]
    return {
        "median_handover_wait_seconds": float(hand_waits.median()) if len(hand_waits) else None,
        "median_same_resource_wait_seconds": float(same_waits.median())
        if len(same_waits)
        else None,
        "handovers": ranked,
    }
