"""Turning a path's waits (from the API) into wait-ladder rows, for both pages.

The ladder is drawn from the cases that followed exactly one variant
(``mining.paths``), so its rungs add up to that path's mean throughput. This
module only formats and labels; the numbers come from the API.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import streamlit as st

from processpilot.app.ui import client, fmt_duration, fmt_share


@st.cache_data(show_spinner=False, max_entries=64)
def path_waits(run_id: str, slug: str, rank: int) -> dict[str, Any]:
    """The API's per-step waits for variant ``rank`` (runs never change once mined)."""
    return client().variant_waits(run_id, slug, rank)


def path_label(metrics: Mapping[str, Any]) -> str:
    """``Path 3 · 14.8% of cases · 5 steps · median 23.7 d`` for a variant's metrics."""
    return (
        f"Path {metrics['rank']} · {fmt_share(metrics['case_share'])} of cases · "
        f"{len(metrics['activities'])} steps · "
        f"median {fmt_duration(metrics['median_throughput_seconds'])}"
    )


def transitions(activities: Sequence[str]) -> set[tuple[str, str]]:
    """The directly-follows pairs a path walks through."""
    return set(zip(activities, activities[1:], strict=False))


def default_rank(variants: Sequence[Mapping[str, Any]]) -> int:
    """The most frequent listed path with more than one wait, else the most frequent path.

    WHY not always path 1: the happy path is often short (two steps, 40 min in
    BPI's 2-way match), a ladder of one rung. Choosing the path through the
    worst bottleneck instead was tried and opened on a path of four cases;
    frequency keeps the ladder about what most cases do.
    """
    for item in variants:
        if len(item["metrics"]["activities"]) >= 3:
            return int(item["metrics"]["rank"])
    return int(variants[0]["metrics"]["rank"]) if variants else 1


def rows(
    waits: Mapping[str, Any],
    bottleneck_ids: Mapping[tuple[str, str], str],
    marks: Mapping[str, tuple[str, str]] | None = None,
    added: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Ladder rows for one path.

    ``bottleneck_ids`` maps a transition to its BN evidence id, shown on the rung
    it leads into. ``marks`` maps an activity to ``(mark, tag)`` for a proposal
    (``automated`` / ``removed``); ``added`` maps an anchor activity to the name
    of a step or check a proposal inserts after it.
    """
    marks = marks or {}
    added = added or {}
    out: list[dict[str, Any]] = []
    previous: str | None = None
    for step in waits["steps"]:
        activity = step["activity"]
        mean = step["mean_wait_seconds"]
        mark, tag = marks.get(activity, (None, None))
        row: dict[str, Any] = {
            "activity": activity,
            "seconds": mean,
            "value": fmt_duration(mean) if mean is not None else "start",
            "sub": f"median {fmt_duration(step['median_wait_seconds'])}"
            if mean is not None
            else "",
            "evidence_id": bottleneck_ids.get((previous, activity)) if previous else None,
            "mark": mark,
            "tag": tag,
        }
        if mean is not None:
            row["hover"] = (
                f"{activity}: mean {fmt_duration(mean)}, median "
                f"{fmt_duration(step['median_wait_seconds'])}, P90 "
                f"{fmt_duration(step['p90_wait_seconds'])} since the previous step"
            )
        out.append(row)
        if activity in added:
            out.append(
                {
                    "activity": added[activity],
                    "seconds": None,
                    "value": "added",
                    "sub": "wait assumed",
                    "mark": "added",
                    "tag": "new step",
                }
            )
        previous = activity
    return out


def caption(waits: Mapping[str, Any]) -> str:
    """What the bars are, in one sentence the analyst can check."""
    n = waits["case_count"]
    return (
        f"Each bar is the mean wait before that step across the {n:,} case(s) on this path, "
        f"laid end to end, so the ladder ends at their mean throughput of "
        f"{fmt_duration(waits['mean_throughput_seconds'])}. A wait is the time since the "
        "previous event, so it includes the step's own work."
    )
