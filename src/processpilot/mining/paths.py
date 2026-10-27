"""Waiting time step by step along one variant: the data behind the wait ladder.

WHY per path and not per edge: a bottleneck edge's wait is a median over every
case that takes that transition, whatever path it is on. Laid end to end along
one variant, those edge medians do not add up to anything a case of that
variant experienced (the 2-way-match happy path takes 40 min; its one edge has
a segment-wide median of days). Here every number comes from the cases that
followed exactly this activity sequence.

WHY the mean positions the steps: means are additive, so the mean waits of a
path's steps sum to exactly that path's mean throughput, and a ladder drawn
from them ends where the path's cases end on average. Medians and P90 are
reported per step too, for reading, but they do not add up.

Input is ``case_waits`` as mining writes it (``mining.prepare.event_waits``:
case_id, activity, wait_seconds in canonical order, NaN on each case's first
event), so the same convention holds: a step's wait includes its own
processing time.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
from pydantic import BaseModel


class PathStep(BaseModel):
    """One step of a path; the first step has no wait (nothing came before it)."""

    position: int
    activity: str
    mean_wait_seconds: float | None
    median_wait_seconds: float | None
    p90_wait_seconds: float | None


class PathWaits(BaseModel):
    """Per-step waits of the cases that followed exactly ``activities``."""

    activities: list[str]
    case_count: int
    mean_throughput_seconds: float | None
    median_throughput_seconds: float | None
    steps: list[PathStep]


def path_waits(case_waits: pd.DataFrame, activities: Sequence[str]) -> PathWaits:
    """Wait statistics per step for the cases whose whole sequence is ``activities``.

    A path no case follows gives ``case_count == 0`` and no statistics rather
    than an error, so a caller can show the path as empty.
    """
    wanted = tuple(activities)
    sequences = case_waits.groupby("case_id", sort=False)["activity"].agg(tuple)
    cases = sequences.index[sequences.map(lambda s: s == wanted)]
    rows = case_waits[case_waits["case_id"].isin(cases)].copy()
    rows["position"] = rows.groupby("case_id", sort=False).cumcount()
    by_position = rows.groupby("position")["wait_seconds"]
    steps = []
    for position, activity in enumerate(wanted):
        waits = by_position.get_group(position).dropna() if len(cases) else pd.Series()
        has_wait = position > 0 and not waits.empty
        steps.append(
            PathStep(
                position=position,
                activity=activity,
                mean_wait_seconds=float(waits.mean()) if has_wait else None,
                median_wait_seconds=float(waits.median()) if has_wait else None,
                p90_wait_seconds=float(waits.quantile(0.9)) if has_wait else None,
            )
        )
    throughput = rows.groupby("case_id")["wait_seconds"].sum()
    return PathWaits(
        activities=list(wanted),
        case_count=len(cases),
        mean_throughput_seconds=float(throughput.mean()) if len(cases) else None,
        median_throughput_seconds=float(throughput.median()) if len(cases) else None,
        steps=steps,
    )
