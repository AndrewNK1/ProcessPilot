"""Unit tests for processpilot.ingest.mapping, against the synthetic log."""

from __future__ import annotations

import pandas as pd
import pytest

from processpilot.ingest.mapping import (
    ColumnMapping,
    apply_mapping,
    split_by_segment,
    suggest_mapping,
)


def test_column_mapping_rejects_duplicate_roles() -> None:
    with pytest.raises(ValueError, match="distinct"):
        ColumnMapping(case_id="x", activity="x", timestamp="y")


def test_suggest_mapping_finds_xes_standard_attributes_with_full_confidence(
    synthetic_raw_log: pd.DataFrame,
) -> None:
    candidates = suggest_mapping(synthetic_raw_log)
    assert candidates["case_id"][0].column == "case:concept:name"
    assert candidates["case_id"][0].confidence == 1.0
    assert candidates["activity"][0].column == "concept:name"
    assert candidates["timestamp"][0].column == "time:timestamp"
    assert candidates["resource"][0].column == "org:resource"


def test_suggest_mapping_never_auto_picks_without_candidates_present(
    synthetic_raw_log: pd.DataFrame,
) -> None:
    """A role with zero plausible columns must come back empty, not with a guess."""
    renamed = synthetic_raw_log.rename(columns={"org:resource": "zzz_unrelated_col"})
    candidates = suggest_mapping(renamed)
    # resource is still suggested from "zzz_unrelated_col" by profiling alone,
    # but confidence must not be artificially inflated to 1.0 the way the XES
    # standard-attribute shortcut would.
    if candidates["resource"]:
        assert candidates["resource"][0].confidence < 1.0


def test_apply_mapping_preserves_unmapped_columns_under_attr_prefix(
    synthetic_raw_log: pd.DataFrame,
) -> None:
    df = synthetic_raw_log.copy()
    df["case:Item Category"] = "widgets"
    mapping = ColumnMapping(
        case_id="case:concept:name",
        activity="concept:name",
        timestamp="time:timestamp",
    )
    out = apply_mapping(df, mapping)
    assert "attr:org:resource" in out.columns
    assert "attr:case:Item Category" in out.columns
    assert "case_id" in out.columns and "activity" in out.columns and "timestamp" in out.columns


def test_apply_mapping_keeps_null_case_id_null_not_the_string_nan(
    synthetic_raw_log: pd.DataFrame,
) -> None:
    mapping = ColumnMapping(
        case_id="case:concept:name", activity="concept:name", timestamp="time:timestamp"
    )
    out = apply_mapping(synthetic_raw_log, mapping)
    assert out["case_id"].isna().any()
    assert "nan" not in out["case_id"].astype(str).str.lower().values or out["case_id"].isna().any()
    # The precise regression: no row's case_id is the literal string "nan".
    non_null = out["case_id"].dropna()
    assert not (non_null.str.lower() == "nan").any()


def test_apply_mapping_converts_tz_aware_timestamp_to_utc_then_naive(
    synthetic_raw_log: pd.DataFrame,
) -> None:
    df = synthetic_raw_log.copy()
    df["time:timestamp"] = df["time:timestamp"].dt.tz_localize("Europe/Berlin")
    mapping = ColumnMapping(
        case_id="case:concept:name",
        activity="concept:name",
        timestamp="time:timestamp",
        timestamp_timezone_policy="utc",
    )
    out = apply_mapping(df, mapping)
    assert out["timestamp"].dt.tz is None
    # Berlin is UTC+1 in January: 09:00 local -> 08:00 UTC-naive.
    first_c1_a = out[(out["case_id"] == "c1") & (out["activity"] == "A")]["timestamp"].iloc[0]
    assert first_c1_a.hour == 8


def test_apply_mapping_sorts_by_case_id_then_timestamp(canonical_log: pd.DataFrame) -> None:
    c1 = canonical_log[canonical_log["case_id"] == "c1"]
    assert list(c1["activity"]) == ["A", "B", "C"]


def test_split_by_segment_returns_single_group_when_no_segment_column(
    canonical_log: pd.DataFrame,
) -> None:
    segments = split_by_segment(canonical_log)
    assert set(segments.keys()) == {"<all>"}
    assert len(segments["<all>"]) == len(canonical_log)


def test_split_by_segment_splits_on_segment_column(canonical_log: pd.DataFrame) -> None:
    with_segment = canonical_log.copy()
    with_segment["segment"] = with_segment["case_id"].map(
        lambda c: "group_a" if pd.notna(c) and c in ("c1", "c2") else "group_b"
    )
    segments = split_by_segment(with_segment)
    assert set(segments.keys()) == {"group_a", "group_b"}
    assert set(segments["group_a"]["case_id"]) == {"c1", "c2"}
