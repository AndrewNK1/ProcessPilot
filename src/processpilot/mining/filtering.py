"""Variant filtering: keep the mainstream behaviour, report what was dropped.

WHY in pandas rather than ``pm4py.filter_variants_top_k``: pm4py re-sorts
events and would rank variants with its own tie order. Using the shared
ranking from ``prepare`` keeps "the top k variants" identical to the variants
reported as evidence (see the module docstring of ``prepare``).

The filter never runs silently: every call returns a ``VariantFilterReport``
with the cases and variants before and after, which is recorded with the KPIs.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from processpilot.mining.prepare import case_variant_keys, ranked_variant_counts
from processpilot.mining.settings import VariantFilterSettings


class VariantFilterReport(BaseModel):
    """What one variant filter kept."""

    mode: str
    value: float
    apply_to: str
    case_count_before: int
    case_count_after: int
    variant_count_before: int
    variant_count_after: int
    kept_case_share: float


def filter_variants(
    df: pd.DataFrame, settings: VariantFilterSettings
) -> tuple[pd.DataFrame, VariantFilterReport]:
    """Rows of the cases whose variant passes the filter, plus the report.

    ``settings.mode`` must not be ``"none"``; the caller decides whether to
    filter at all.
    """
    if settings.mode == "none" or settings.value is None:
        raise ValueError("filter_variants called with the filter disabled")
    keys = case_variant_keys(df)
    counts = ranked_variant_counts(keys)
    total = int(counts.sum())

    if settings.mode == "top_k":
        kept = counts.head(int(settings.value))
    elif settings.mode == "coverage":
        # Smallest prefix of the ranking whose cumulative share reaches value.
        cum_share = counts.cumsum() / total
        n = int((cum_share < settings.value).sum()) + 1
        kept = counts.head(n)
    else:
        kept = counts[counts / total >= settings.value]

    kept_cases = keys.index[keys.isin(kept.index)]
    out = df.loc[df["case_id"].isin(kept_cases)].reset_index(drop=True)
    report = VariantFilterReport(
        mode=settings.mode,
        value=settings.value,
        apply_to=settings.apply_to,
        case_count_before=total,
        case_count_after=len(kept_cases),
        variant_count_before=len(counts),
        variant_count_after=len(kept),
        kept_case_share=len(kept_cases) / total if total else 0.0,
    )
    return out, report
