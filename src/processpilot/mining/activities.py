"""Waiting time before each activity, as opposed to per transition.

WHY both views: a bottleneck edge says "the wait between ``a`` and ``b`` is
long"; an analyst looking at one task on a process diagram asks "how long do
cases wait before *this* task, whatever came before it?". The per-activity
view answers that and is what a BPMN overlay will colour later; the edge
view stays in ``bottlenecks`` so neither is derived from the other's rounding.

Same convention as ``prepare``: the wait before an event is the time since
the previous event of its case, so it includes the activity's own processing
time. An activity's first event in a case is counted only when something came
before it; activities that only ever start a case have no waiting time and
are listed separately rather than reported as zero.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from processpilot.mining.prepare import directly_follows_pairs


class ActivityWait(BaseModel):
    """Waiting time into one activity. ``impact_score`` is in seconds."""

    rank: int
    activity: str
    occurrences: int
    case_count: int
    case_share: float
    median_wait_seconds: float
    mean_wait_seconds: float
    p90_wait_seconds: float
    impact_score: float
    top_predecessor: str
    top_predecessor_share: float


class ActivityWaitSummary(BaseModel):
    """Every activity with a waiting time, ranked, plus those that never wait."""

    activities: list[ActivityWait]
    no_predecessor_activities: list[str]


def compute_activity_waits(df: pd.DataFrame) -> ActivityWaitSummary:
    """Per-activity wait statistics, ranked by median wait x case share.

    The impact score matches the bottleneck one, so an activity and an edge
    can be compared on the same scale. Ties are broken by activity name.
    """
    pairs = directly_follows_pairs(df)
    case_count = int(df["case_id"].nunique())
    waited: list[ActivityWait] = []
    if not pairs.empty:
        grouped = pairs.groupby("target", sort=True)
        stats = grouped.agg(
            occurrences=("wait_seconds", "size"),
            case_count=("case_id", "nunique"),
            median_wait_seconds=("wait_seconds", "median"),
            mean_wait_seconds=("wait_seconds", "mean"),
        )
        stats["p90_wait_seconds"] = grouped["wait_seconds"].quantile(0.9)
        stats["impact_score"] = stats["median_wait_seconds"] * stats["case_count"] / case_count

        pred = pairs.groupby(["target", "source"], sort=True).size().rename("n").reset_index()
        pred = pred.sort_values(["target", "n", "source"], ascending=[True, False, True])
        top_pred = pred.drop_duplicates("target").set_index("target")

        stats = stats.reset_index().sort_values(
            ["impact_score", "target"], ascending=[False, True], kind="stable"
        )
        for rank, r in enumerate(stats.itertuples(index=False), start=1):
            best = top_pred.loc[r.target]
            waited.append(
                ActivityWait(
                    rank=rank,
                    activity=str(r.target),
                    occurrences=int(r.occurrences),
                    case_count=int(r.case_count),
                    case_share=float(r.case_count) / case_count,
                    median_wait_seconds=float(r.median_wait_seconds),
                    mean_wait_seconds=float(r.mean_wait_seconds),
                    p90_wait_seconds=float(r.p90_wait_seconds),
                    impact_score=float(r.impact_score),
                    top_predecessor=str(best["source"]),
                    top_predecessor_share=float(best["n"]) / int(r.occurrences),
                )
            )
    with_wait = {w.activity for w in waited}
    never = sorted(str(a) for a in df["activity"].unique() if a not in with_wait)
    return ActivityWaitSummary(activities=waited, no_predecessor_activities=never)
