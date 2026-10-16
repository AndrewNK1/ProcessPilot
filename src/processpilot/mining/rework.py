"""Rework and loop detection.

WHY it is measured separately from bottlenecks: a repeated activity is a
*quality* signal, not a waiting signal, and it maps to different redesign
heuristics (knock-out ordering, resequencing) than a long queue does.

Per activity: repetition count within a case, share of cases affected, and the
extra elapsed time attributable to repetitions.

"Extra time" is defined comparatively, because a single-timestamp log cannot
attribute elapsed time to one repetition: the median throughput of cases that
repeat the activity minus the median throughput of cases that perform it
exactly once. It is an association, not a causal effect -- cases that loop may
also be larger or more complex -- and the evidence summary says so.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from processpilot.mining.prepare import case_throughput_seconds, directly_follows_pairs


class ReworkItem(BaseModel):
    """Repetition statistics for one activity."""

    rank: int
    activity: str
    case_count: int
    case_share: float
    extra_occurrences: int
    direct_loop_count: int
    median_throughput_with_rework_seconds: float
    median_throughput_single_occurrence_seconds: float | None
    extra_median_throughput_seconds: float | None
    impact_score: float
    example_case_ids: list[str]


def analyse_rework(df: pd.DataFrame, top_n: int, max_example_cases: int) -> list[ReworkItem]:
    """Activities repeated within a case, ranked by impact then case count.

    ``impact_score`` = case share x extra median throughput (seconds, floored
    at 0), mirroring the bottleneck score so the two rank on the same scale.
    """
    total_cases = int(df["case_id"].nunique())
    counts = df.groupby(["case_id", "activity"], sort=True).size()
    throughput = case_throughput_seconds(df)
    pairs = directly_follows_pairs(df)
    self_loops = pairs.loc[pairs["source"] == pairs["target"], "source"].value_counts()

    items: list[ReworkItem] = []
    for activity, per_case in counts.groupby(level="activity", sort=True):
        per_case = per_case.droplevel("activity")
        repeated = per_case[per_case > 1]
        if repeated.empty:
            continue
        single = per_case[per_case == 1]
        with_rework = float(throughput.loc[repeated.index].median())
        single_median = float(throughput.loc[single.index].median()) if len(single) else None
        extra = with_rework - single_median if single_median is not None else None
        share = len(repeated) / total_cases
        items.append(
            ReworkItem(
                rank=0,
                activity=str(activity),
                case_count=len(repeated),
                case_share=share,
                extra_occurrences=int((repeated - 1).sum()),
                direct_loop_count=int(self_loops.get(activity, 0)),
                median_throughput_with_rework_seconds=with_rework,
                median_throughput_single_occurrence_seconds=single_median,
                extra_median_throughput_seconds=extra,
                impact_score=share * max(extra or 0.0, 0.0),
                example_case_ids=[str(c) for c in sorted(repeated.index)[:max_example_cases]],
            )
        )

    items.sort(key=lambda i: (-i.impact_score, -i.case_count, i.activity))
    return [i.model_copy(update={"rank": r}) for r, i in enumerate(items[:top_n], start=1)]
