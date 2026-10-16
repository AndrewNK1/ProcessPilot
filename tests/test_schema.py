"""Unit tests for processpilot.schema, against the hand-built synthetic log."""

from __future__ import annotations

import pandas as pd
import pytest

from processpilot.schema import (
    PM4PY_ACTIVITY,
    PM4PY_CASE_ID,
    PM4PY_RESOURCE,
    PM4PY_TIMESTAMP,
    assert_canonical_schema,
    sort_canonical,
    to_pm4py,
)


def _clean_subset(canonical_log: pd.DataFrame) -> pd.DataFrame:
    """c1/c2 only -- the two cases in the fixture with no defects at all."""
    return canonical_log[canonical_log["case_id"].isin(["c1", "c2"])].reset_index(drop=True)


def test_assert_canonical_schema_passes_on_clean_data(canonical_log: pd.DataFrame) -> None:
    clean = _clean_subset(canonical_log)
    assert_canonical_schema(clean)  # must not raise


def test_assert_canonical_schema_rejects_null_case_id(canonical_log: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="case_id"):
        assert_canonical_schema(canonical_log)  # fixture has a null case_id (c7)


def test_assert_canonical_schema_rejects_tz_aware_timestamp(canonical_log: pd.DataFrame) -> None:
    clean = _clean_subset(canonical_log)
    tz_aware = clean.copy()
    tz_aware["timestamp"] = tz_aware["timestamp"].dt.tz_localize("UTC")
    with pytest.raises(ValueError, match="tz-naive"):
        assert_canonical_schema(tz_aware)


def test_assert_canonical_schema_rejects_missing_column(canonical_log: pd.DataFrame) -> None:
    clean = _clean_subset(canonical_log).drop(columns=["activity"])
    with pytest.raises(ValueError, match="missing required"):
        assert_canonical_schema(clean)


def test_assert_canonical_schema_rejects_unsorted_data(canonical_log: pd.DataFrame) -> None:
    clean = _clean_subset(canonical_log)
    shuffled = clean.iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError, match="not sorted"):
        assert_canonical_schema(shuffled)


def test_sort_canonical_is_stable_on_timestamp_ties(canonical_log: pd.DataFrame) -> None:
    """Checks c5 (B and C tied at the same timestamp).

    Original row order (B before C) must survive the sort since nothing in
    the data breaks that tie.
    """
    c5 = canonical_log[canonical_log["case_id"] == "c5"]
    shuffled = pd.concat([c5.iloc[[1, 0, 2]]])  # B, A, C
    result = sort_canonical(shuffled)
    assert list(result["activity"]) == ["A", "B", "C"]


def test_to_pm4py_renames_canonical_columns(canonical_log: pd.DataFrame) -> None:
    clean = _clean_subset(canonical_log)
    out = to_pm4py(clean)
    assert PM4PY_CASE_ID in out.columns
    assert PM4PY_ACTIVITY in out.columns
    assert PM4PY_TIMESTAMP in out.columns
    assert PM4PY_RESOURCE in out.columns
    assert "case_id" not in out.columns


def test_to_pm4py_refuses_dirty_data(canonical_log: pd.DataFrame) -> None:
    with pytest.raises(ValueError):
        to_pm4py(canonical_log)  # has nulls; must not silently pass through


def test_assert_canonical_schema_rejects_non_nanosecond_timestamp(
    canonical_log: pd.DataFrame,
) -> None:
    clean = _clean_subset(canonical_log)
    clean["timestamp"] = clean["timestamp"].astype("datetime64[us]")
    with pytest.raises(ValueError, match=r"datetime64\[ns\]"):
        assert_canonical_schema(clean)
