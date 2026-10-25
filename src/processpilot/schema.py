"""Canonical event-log schema and the pydantic models shared across modules.

WHY a canonical schema: every data source (XES file, CSV, SQL, Odoo) produces a
different shape. Mining code must not know where data came from, so all sources
converge here first. That single conversion point is also the only place where
timestamp and dtype guarantees are established, which is what makes the mining
functions pure.

Guarantees after ``apply_mapping`` + validation (Phase 1):
    - columns ``case_id`` (str), ``activity`` (str), ``timestamp``
      (tz-naive ``datetime64[ns]``) are present and non-null
    - optional ``resource`` (str) and ``segment`` (str)
    - optional ``timestamp_raw`` (str): present only when some source
      timestamps could not be converted; holds the original value on exactly
      those rows (null elsewhere), so ``validate`` can tell an unparseable
      value apart from a genuinely missing one
    - all other source columns preserved under an ``attr:`` prefix so no
      information is silently dropped
    - stably sorted by ``(case_id, timestamp)`` so event order within equal
      timestamps reflects the source order rather than an arbitrary permutation
"""

from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = ("case_id", "activity", "timestamp")
OPTIONAL_COLUMNS = ("resource", "segment")
TIMESTAMP_RAW = "timestamp_raw"
# pandas 3 infers microsecond resolution when parsing strings; the schema pins
# nanoseconds so every source yields the same dtype (and pm4py's documented one).
TIMESTAMP_DTYPE = "datetime64[ns]"
CANONICAL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

# pm4py's standard column names (its "stream" / dataframe convention).
PM4PY_CASE_ID = "case:concept:name"
PM4PY_ACTIVITY = "concept:name"
PM4PY_TIMESTAMP = "time:timestamp"
PM4PY_RESOURCE = "org:resource"


def assert_canonical_schema(df: pd.DataFrame) -> None:
    """Raise if ``df`` does not meet the canonical-schema guarantees.

    WHY a standalone assertion rather than inlining checks in every caller:
    every mining function takes a canonical dataframe as a precondition, not
    as something it re-derives. Centralising the check means that precondition
    is enforced identically everywhere, and the error message is identical
    everywhere too.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required canonical column(s): {missing}")

    for col in REQUIRED_COLUMNS:
        if df[col].isna().any():
            n = int(df[col].isna().sum())
            raise ValueError(f"canonical column '{col}' has {n} null value(s)")

    ts = df["timestamp"]
    if not pd.api.types.is_datetime64_any_dtype(ts):
        raise ValueError(f"'timestamp' must be datetime64, got {ts.dtype}")
    if getattr(ts.dt, "tz", None) is not None:
        raise ValueError("'timestamp' must be tz-naive")
    if ts.dtype != TIMESTAMP_DTYPE:
        raise ValueError(f"'timestamp' must be {TIMESTAMP_DTYPE}, got {ts.dtype}")

    # One linear pass instead of re-sorting: mining calls this on every
    # function entry, and a full sort of a 1.3M-row segment costs ~1 s each.
    case_id = df["case_id"]
    prev_case = case_id.shift(1)
    case_goes_back = (case_id < prev_case).fillna(False)
    time_goes_back = ((case_id == prev_case) & (ts < ts.shift(1))).fillna(False)
    if bool((case_goes_back | time_goes_back).any()):
        raise ValueError("dataframe is not sorted by (case_id, timestamp)")


def sort_canonical(df: pd.DataFrame) -> pd.DataFrame:
    """Stable-sort by ``(case_id, timestamp)``, resetting the index.

    WHY a stable sort specifically: equal timestamps are common (BPI 2019 has
    many same-instant events within a case). A stable sort preserves the
    source file's original row order as the tiebreaker, which is the only
    order we actually have evidence for; an unstable sort would silently
    invent a different tie order on every run.
    """
    return df.sort_values(["case_id", "timestamp"], kind="stable").reset_index(drop=True)


def to_pm4py(df: pd.DataFrame) -> pd.DataFrame:
    """Rename canonical columns to pm4py's standard names.

    Only the four canonical roles are renamed; every ``attr:`` and ``segment``
    column passes through unchanged, since pm4py's discovery functions ignore
    columns they don't recognise.
    """
    assert_canonical_schema(df)
    rename = {
        "case_id": PM4PY_CASE_ID,
        "activity": PM4PY_ACTIVITY,
        "timestamp": PM4PY_TIMESTAMP,
    }
    if "resource" in df.columns:
        rename["resource"] = PM4PY_RESOURCE
    return df.rename(columns=rename)
