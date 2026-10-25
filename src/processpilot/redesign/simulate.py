"""Replay-based impact ESTIMATE. Not a simulation of reality.

WHY an estimate and not a discrete-event simulation: a DES would need arrival
rates, resource capacities and service-time distributions that the log does
not contain, and inventing them would make the result look more
authoritative than it is. Instead every historical case is replayed with the
edits applied to its own observed waits.

Each event's cost is its wait since the previous event of the case (it
includes the event's own processing time; single-timestamp log, see
``mining/prepare``). A case's as-is throughput is the sum of its costs, which
equals last minus first timestamp. Per operation:

- remove: the removed activity's waits drop out; the next step's wait is
  assumed unchanged.
- automate: the activity's waits shrink by ``automate_wait_reduction``.
- add / add check: each occurrence of the anchor activity adds an assumed
  wait (the segment's median wait before an activity, or zero).
- parallelize: consecutive events of the parallelized activities cost the
  longest of their waits instead of the sum.
- reorder, merge: no time effect is estimated, and the result says so --
  their effect depends on queueing that the log cannot show.

Every number produced here is labelled ESTIMATE, and the assumptions that
produced it travel with it. Inputs are the per-event waits written by mining
(``case_waits.parquet``); no raw event attributes are read.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from processpilot.mining.evidence import format_duration
from processpilot.redesign.operations import (
    AddActivity,
    AddCheck,
    AutomateActivity,
    EditOperation,
    MergeActivities,
    ParallelizeActivities,
    RemoveActivity,
    ReorderActivities,
    describe,
)


class EstimateSettings(BaseModel):
    """Assumptions of the estimate; shown with every result (``configs/agent.yaml``)."""

    # Share of an automated activity's wait that disappears. 0.5 is a cautious
    # default (user decision, Phase 4), not a measured value.
    automate_wait_reduction: float = Field(ge=0.0, le=1.0)
    # What a newly added activity or check costs per occurrence.
    added_activity_wait: Literal["segment_median", "zero"]


class ThroughputStats(BaseModel):
    """Case throughput statistics, in seconds."""

    median_seconds: float
    mean_seconds: float
    p90_seconds: float


class ImpactEstimate(BaseModel):
    """Projected throughput change of one proposal. Always an ESTIMATE."""

    label: Literal["ESTIMATE"] = "ESTIMATE"
    case_count: int
    cases_affected: int
    cases_affected_share: float
    as_is: ThroughputStats
    to_be: ThroughputStats
    median_change_seconds: float
    median_change_share: float | None
    assumptions: list[str]
    not_estimated: list[str]


def _stats(throughput: pd.Series) -> ThroughputStats:
    return ThroughputStats(
        median_seconds=float(throughput.median()),
        mean_seconds=float(throughput.mean()),
        p90_seconds=float(throughput.quantile(0.9)),
    )


def estimate_impact(
    case_waits: pd.DataFrame, ops: list[EditOperation], settings: EstimateSettings
) -> ImpactEstimate:
    """Replay ``case_waits`` (case_id, activity, wait_seconds) under ``ops``.

    ``case_waits`` must be in canonical event order; ``wait_seconds`` is NaN
    on each case's first event.
    """
    ev = case_waits[["case_id", "activity"]].copy()
    ev["cost"] = case_waits["wait_seconds"].fillna(0.0).astype(float)
    as_is = ev.groupby("case_id", sort=True)["cost"].sum()
    adjust = pd.Series(0.0, index=as_is.index)
    touched = pd.Series(False, index=ev.index)
    median_wait = float(case_waits["wait_seconds"].median())
    if pd.isna(median_wait):
        median_wait = 0.0
    added_wait = median_wait if settings.added_activity_wait == "segment_median" else 0.0

    assumptions = [
        "Replays every observed case with the edits applied; an event's cost is its wait "
        "since the previous event (including its own processing time). Arrival rates, "
        "capacity and resource contention are not modelled."
    ]
    not_estimated: list[str] = []
    for op in ops:
        match op:
            case RemoveActivity():
                rows = ev["activity"] == op.activity
                ev.loc[rows, "cost"] = 0.0
                touched |= rows
                starts = int((rows & case_waits["wait_seconds"].isna()).sum())
                assumptions.append(
                    f"Removing '{op.activity}' removes the time before it; the next step's own "
                    "wait is assumed unchanged."
                    + (
                        f" In {starts:,} case(s) it is the first event, whose duration the log "
                        "does not record, so removing it saves nothing there."
                        if starts
                        else ""
                    )
                )
            case AutomateActivity():
                rows = ev["activity"] == op.activity
                ev.loc[rows, "cost"] *= 1.0 - settings.automate_wait_reduction
                touched |= rows
                assumptions.append(
                    f"Automating '{op.activity}' shortens its wait by an assumed "
                    f"{settings.automate_wait_reduction:.0%} (configs/agent.yaml)."
                )
            case AddActivity() | AddCheck():
                rows = ev["activity"] == op.after
                ev.loc[rows, "cost"] += added_wait
                touched |= rows
                basis = (
                    "the segment's median wait before an activity"
                    if settings.added_activity_wait == "segment_median"
                    else "assumed zero"
                )
                assumptions.append(
                    f"'{op.name}' adds {format_duration(added_wait)} per occurrence of "
                    f"'{op.after}' ({basis})."
                )
            case ParallelizeActivities():
                saving, rows = _parallel_saving(ev, set(op.activities))
                adjust += saving.reindex(adjust.index, fill_value=0.0)
                touched |= rows
                assumptions.append(
                    "Parallel steps cost the longest of their waits instead of their sum, "
                    "assuming they can start as soon as their predecessor finishes."
                )
            case ReorderActivities() | MergeActivities():
                not_estimated.append(
                    f"{describe(op)}: no time effect estimated; it depends on queueing the "
                    "log does not record."
                )
    to_be = ev.groupby("case_id", sort=True)["cost"].sum() + adjust
    affected_cases = set(ev.loc[touched, "case_id"]) | set(adjust.index[adjust != 0])
    before, after = _stats(as_is), _stats(to_be)
    change = after.median_seconds - before.median_seconds
    return ImpactEstimate(
        case_count=len(as_is),
        cases_affected=len(affected_cases),
        cases_affected_share=len(affected_cases) / len(as_is) if len(as_is) else 0.0,
        as_is=before,
        to_be=after,
        median_change_seconds=change,
        median_change_share=change / before.median_seconds if before.median_seconds else None,
        assumptions=assumptions,
        not_estimated=not_estimated,
    )


def _parallel_saving(ev: pd.DataFrame, group: set[str]) -> tuple[pd.Series, pd.Series]:
    """Per-case saving (max - sum over runs of consecutive ``group`` events) and the rows."""
    in_group = ev["activity"].isin(group)
    new_case = ev["case_id"] != ev["case_id"].shift(1)
    run_id = ((in_group != in_group.shift(1)) | new_case).cumsum()
    runs = ev.loc[in_group].assign(run=run_id[in_group])
    per_run = runs.groupby("run").agg(
        case_id=("case_id", "first"),
        total=("cost", "sum"),
        longest=("cost", "max"),
        size=("cost", "size"),
    )
    multi = per_run[per_run["size"] > 1]
    rows = in_group & run_id.isin(multi.index)
    saving = (multi["longest"] - multi["total"]).groupby(multi["case_id"]).sum()
    return saving.astype(float), rows
