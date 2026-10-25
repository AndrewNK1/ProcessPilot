"""Trace variants and the happy path.

WHY variants matter to redesign: the gap between the most frequent variant's
throughput time and the log's median is the size of the prize. Variants also
tell the redesign agent which exceptional paths it must not break.

Top-N variants with case share and mean throughput time; the most frequent
variant is labelled the happy path (frequency, not desirability -- the
distinction is documented in the evidence summary).
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from processpilot.mining.prepare import (
    VARIANT_SEP,
    case_throughput_seconds,
    case_variant_keys,
    ranked_variant_counts,
)


class Variant(BaseModel):
    """One distinct activity sequence and the cases that follow it."""

    rank: int
    activities: list[str]
    case_count: int
    case_share: float
    mean_throughput_seconds: float
    median_throughput_seconds: float
    is_happy_path: bool
    example_case_ids: list[str]


class VariantSummary(BaseModel):
    """Top-N variants plus how much of the log they cover."""

    variant_count: int
    top_n_case_share: float
    variants: list[Variant]


def analyse_variants(df: pd.DataFrame, top_n: int, max_example_cases: int) -> VariantSummary:
    """Rank variants by case count; ties broken by the activity sequence itself."""
    table = pd.DataFrame({"key": case_variant_keys(df), "throughput": case_throughput_seconds(df)})
    case_count = len(table)

    order = ranked_variant_counts(table["key"]).index
    stats = table.groupby("key", sort=False)["throughput"].agg(["size", "mean", "median"])
    stats = stats.reindex(order).reset_index()

    variants: list[Variant] = []
    for rank, row in enumerate(stats.head(top_n).itertuples(index=False), start=1):
        members = sorted(table.index[table["key"] == row.key])
        variants.append(
            Variant(
                rank=rank,
                activities=row.key.split(VARIANT_SEP),
                case_count=int(row.size),
                case_share=float(row.size) / case_count,
                mean_throughput_seconds=float(row.mean),
                median_throughput_seconds=float(row.median),
                is_happy_path=rank == 1,
                example_case_ids=[str(c) for c in members[:max_example_cases]],
            )
        )
    return VariantSummary(
        variant_count=len(stats),
        top_n_case_share=sum(v.case_share for v in variants),
        variants=variants,
    )
