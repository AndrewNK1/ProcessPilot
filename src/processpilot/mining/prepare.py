"""Shared building blocks for the mining functions.

WHY directly-follows pairs are computed here in pandas rather than taken from
pm4py's DFG functions: pm4py re-sorts events internally, and in BPI 2019 ~15%
of events tie on timestamp with another event of the same case. The canonical
log is stably sorted with the source order as tiebreaker; computing pairs from
that order keeps the DFG, the waiting times and the variants consistent with
each other and identical across reruns.

Waiting-time convention: the log has one timestamp per event (completion), so
the "wait" on edge ``a -> b`` is ``timestamp(b) - timestamp(a)`` -- the time
from finishing ``a`` to finishing ``b``. It includes ``b``'s processing time,
which a single-timestamp log cannot separate out.
"""

from __future__ import annotations

import pandas as pd

from processpilot.schema import assert_canonical_schema


def collapse_same_instant_repeats(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Keep one event per (case, activity, timestamp); return it and the count removed.

    WHY on the full key rather than only adjacent rows: events tied on
    timestamp have no meaningful order among themselves, so "adjacent" would
    depend on the source file's arbitrary tie order.
    """
    duplicated = df.duplicated(subset=["case_id", "activity", "timestamp"], keep="first")
    return df.loc[~duplicated].reset_index(drop=True), int(duplicated.sum())


def directly_follows_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """One row per directly-follows step within a case.

    Columns: ``case_id``, ``source``, ``target``, ``wait_seconds``, and, when a
    ``resource`` column exists, ``source_resource`` and ``target_resource``.
    """
    assert_canonical_schema(df)
    nxt = df.shift(-1)
    same_case = (df["case_id"] == nxt["case_id"]).fillna(False).astype(bool)
    pairs = pd.DataFrame(
        {
            "case_id": df["case_id"],
            "source": df["activity"],
            "target": nxt["activity"],
            "wait_seconds": (nxt["timestamp"] - df["timestamp"]).dt.total_seconds(),
        }
    )
    if "resource" in df.columns:
        pairs["source_resource"] = df["resource"]
        pairs["target_resource"] = nxt["resource"]
    return pairs.loc[same_case].reset_index(drop=True)


def case_throughput_seconds(df: pd.DataFrame) -> pd.Series:
    """Seconds from first to last event, indexed by ``case_id``."""
    grouped = df.groupby("case_id", sort=True)["timestamp"]
    return (grouped.max() - grouped.min()).dt.total_seconds()


def case_traces(df: pd.DataFrame) -> pd.Series:
    """Activity sequence (tuple) per case, in canonical order, indexed by ``case_id``."""
    return df.groupby("case_id", sort=True)["activity"].agg(tuple)


def case_end_activities(df: pd.DataFrame) -> pd.Series:
    """Last activity per case, indexed by ``case_id``."""
    return df.groupby("case_id", sort=True)["activity"].last()


# A unit-separator-joined string is a hashable, sortable stand-in for a trace
# tuple; activity names never contain U+001F.
VARIANT_SEP = "\x1f"


def case_variant_keys(df: pd.DataFrame) -> pd.Series:
    """Variant key (activities joined by ``VARIANT_SEP``) per case, indexed by ``case_id``."""
    return case_traces(df).map(VARIANT_SEP.join)


def ranked_variant_counts(keys: pd.Series) -> pd.Series:
    """Case count per variant key, most frequent first, ties by key.

    WHY one shared ordering: the variant filter and the variant evidence must
    agree on what "the top k variants" are, including at ties.
    """
    counts = keys.value_counts().rename_axis("key").reset_index(name="size")
    counts = counts.sort_values(["size", "key"], ascending=[False, True], kind="stable")
    return counts.set_index("key")["size"]
