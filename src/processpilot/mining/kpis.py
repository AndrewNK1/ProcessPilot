"""Overall segment KPIs: size and throughput time.

WHY these are evidence items too: the agent needs a baseline to judge whether
a bottleneck matters ("a 9-day wait in a process whose median case takes 70
days"), and that baseline must be a cited number, not the model's impression.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from processpilot.mining.filtering import VariantFilterReport
from processpilot.mining.prepare import case_throughput_seconds


class SegmentKPIs(BaseModel):
    """Counts and throughput statistics for one segment."""

    case_count: int
    event_count: int
    raw_event_count: int
    collapsed_event_count: int
    activity_count: int
    median_throughput_seconds: float
    mean_throughput_seconds: float
    p90_throughput_seconds: float
    first_timestamp: str
    last_timestamp: str
    # Set when a variant filter was configured, whatever it was applied to,
    # so a filtered model or filtered numbers can always be told apart.
    variant_filter: VariantFilterReport | None = None


def compute_kpis(
    mining_df: pd.DataFrame,
    raw_event_count: int,
    collapsed: int,
    variant_filter: VariantFilterReport | None = None,
) -> SegmentKPIs:
    """KPIs on the mining view; ``raw_event_count``/``collapsed`` record what changed."""
    throughput = case_throughput_seconds(mining_df)
    return SegmentKPIs(
        variant_filter=variant_filter,
        case_count=int(mining_df["case_id"].nunique()),
        event_count=len(mining_df),
        raw_event_count=raw_event_count,
        collapsed_event_count=collapsed,
        activity_count=int(mining_df["activity"].nunique()),
        median_throughput_seconds=float(throughput.median()),
        mean_throughput_seconds=float(throughput.mean()),
        p90_throughput_seconds=float(throughput.quantile(0.9)),
        first_timestamp=mining_df["timestamp"].min().isoformat(),
        last_timestamp=mining_df["timestamp"].max().isoformat(),
    )
