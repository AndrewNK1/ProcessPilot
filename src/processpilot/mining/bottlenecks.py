"""Waiting-time analysis per directly-follows edge.

WHY an impact score of ``median wait x share of cases affected``: a long wait on
an edge that two cases traverse is a curiosity, not a bottleneck. Ranking by
median rather than mean keeps a handful of junk-timestamp outliers from
inventing a bottleneck that does not exist.

Per edge: frequency, median / mean / P90 waiting time, share of cases affected,
impact score. Returned ranked, so evidence IDs are stable across reruns.

The ranking is computed from ``DFGResult`` rather than from the log again, so a
bottleneck's numbers are by construction the same numbers as its DFG edge.
"""

from __future__ import annotations

from pydantic import BaseModel

from processpilot.mining.discovery import DFGEdge, DFGResult


class Bottleneck(BaseModel):
    """One ranked edge. ``impact_score`` is in seconds (median wait x case share)."""

    rank: int
    edge: DFGEdge
    impact_score: float


def rank_bottlenecks(dfg: DFGResult, top_n: int) -> list[Bottleneck]:
    """Top-``top_n`` edges by impact; ties broken by source then target name."""
    scored = sorted(
        dfg.edges,
        key=lambda e: (-(e.median_wait_seconds * e.case_share), e.source, e.target),
    )
    return [
        Bottleneck(rank=i, edge=e, impact_score=e.median_wait_seconds * e.case_share)
        for i, e in enumerate(scored[:top_n], start=1)
    ]
