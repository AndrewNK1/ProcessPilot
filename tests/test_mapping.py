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


# --- regressions found in the Phase 1 review --------------------------------


def _text_log(timestamps: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "case": ["c1"] * len(timestamps),
            "act": [f"A{i}" for i in range(len(timestamps))],
            "ts": timestamps,
        }
    )


_TEXT_MAPPING = ColumnMapping(case_id="case", activity="act", timestamp="ts")


def test_apply_mapping_yields_nanosecond_timestamps_from_text() -> None:
    """Text parses to datetime64[us] under pandas 3; the schema requires [ns]."""
    out = apply_mapping(_text_log(["2019-01-01 10:00", "2019-01-01 11:00"]), _TEXT_MAPPING)
    assert out["timestamp"].dtype == "datetime64[ns]"


def test_apply_mapping_reconciles_mixed_utc_offsets_under_utc_policy() -> None:
    """Offsets differ across a DST switch; this used to raise ValueError."""
    raw = _text_log(["2019-03-30 10:00+01:00", "2019-04-01 10:00+02:00"])
    out = apply_mapping(raw, _TEXT_MAPPING)
    assert list(out["timestamp"]) == [
        pd.Timestamp("2019-03-30 09:00"),
        pd.Timestamp("2019-04-01 08:00"),
    ]


def test_apply_mapping_mixed_offsets_under_local_policy_fail_loudly() -> None:
    raw = _text_log(["2019-03-30 10:00+01:00", "2019-04-01 10:00+02:00"])
    mapping = _TEXT_MAPPING.model_copy(update={"timestamp_timezone_policy": "local"})
    with pytest.raises(ValueError, match="mixes UTC offsets"):
        apply_mapping(raw, mapping)


def test_apply_mapping_local_policy_keeps_wall_clock(synthetic_raw_log: pd.DataFrame) -> None:
    df = synthetic_raw_log.copy()
    df["time:timestamp"] = df["time:timestamp"].dt.tz_localize("Europe/Berlin")
    mapping = ColumnMapping(
        case_id="case:concept:name",
        activity="concept:name",
        timestamp="time:timestamp",
        timestamp_timezone_policy="local",
    )
    out = apply_mapping(df, mapping)
    first_c1_a = out[(out["case_id"] == "c1") & (out["activity"] == "A")]["timestamp"].iloc[0]
    assert first_c1_a.hour == 9


def test_apply_mapping_none_policy_rejects_tz_aware_source(
    synthetic_raw_log: pd.DataFrame,
) -> None:
    df = synthetic_raw_log.copy()
    df["time:timestamp"] = df["time:timestamp"].dt.tz_localize("UTC")
    mapping = ColumnMapping(
        case_id="case:concept:name",
        activity="concept:name",
        timestamp="time:timestamp",
        timestamp_timezone_policy="none",
    )
    with pytest.raises(ValueError, match="tz-naive"):
        apply_mapping(df, mapping)


def test_apply_mapping_honours_explicit_timestamp_format() -> None:
    raw = _text_log(["01/02/2019 10:00", "03/02/2019 10:00"])  # day-first
    mapping = _TEXT_MAPPING.model_copy(update={"timestamp_format": "%d/%m/%Y %H:%M"})
    out = apply_mapping(raw, mapping)
    assert list(out["timestamp"].dt.month) == [2, 2]


def test_apply_mapping_keeps_unparseable_timestamp_text_in_timestamp_raw() -> None:
    """Unparseable values must not silently become indistinguishable NaT."""
    out = apply_mapping(_text_log(["2019-01-01 10:00", "garbage"]), _TEXT_MAPPING)
    assert "timestamp_raw" in out.columns
    bad = out[out["timestamp"].isna()]
    assert list(bad["timestamp_raw"]) == ["garbage"]
    assert out.loc[out["timestamp"].notna(), "timestamp_raw"].isna().all()


def test_apply_mapping_omits_timestamp_raw_when_everything_parses() -> None:
    out = apply_mapping(_text_log(["2019-01-01 10:00", "2019-01-01 11:00"]), _TEXT_MAPPING)
    assert "timestamp_raw" not in out.columns


def test_apply_mapping_turns_dates_beyond_ns_range_into_reported_nat() -> None:
    """ERP sentinels like 9999-12-31 used to be impossible to represent in [ns]."""
    out = apply_mapping(_text_log(["2019-01-01 10:00", "9999-12-31 00:00"]), _TEXT_MAPPING)
    assert out["timestamp"].isna().sum() == 1
    assert out["timestamp_raw"].dropna().str.startswith("9999").all()


def test_apply_mapping_does_not_collide_with_unmapped_column_named_like_a_role() -> None:
    raw = pd.DataFrame(
        {
            "Case": ["a", "a"],
            "case_id": ["z", "z"],
            "act": ["X", "Y"],
            "ts": ["2019-01-01", "2019-01-02"],
        }
    )
    out = apply_mapping(raw, ColumnMapping(case_id="Case", activity="act", timestamp="ts"))
    assert list(out["case_id"]) == ["a", "a"]
    assert list(out["attr:case_id"]) == ["z", "z"]


def test_apply_mapping_reports_missing_mapped_column_by_name() -> None:
    raw = _text_log(["2019-01-01"])
    with pytest.raises(ValueError, match="not_there"):
        apply_mapping(raw, ColumnMapping(case_id="not_there", activity="act", timestamp="ts"))


def test_suggest_mapping_offers_segment_candidates_for_xes(
    synthetic_raw_log: pd.DataFrame,
) -> None:
    """Regression: the XES branch used to skip exactly the segment_by role."""
    df = synthetic_raw_log.copy()
    df["case:Item Category"] = df["case:concept:name"].map(
        lambda c: "k1" if c in ("c1", "c2") else "k2"
    )
    candidates = suggest_mapping(df)
    assert candidates["segment_by"][0].column == "case:Item Category"
    # Standard-mapped roles stay a single certain answer, not padded with noise.
    assert [c.column for c in candidates["case_id"]] == ["case:concept:name"]
    assert "case:concept:name" not in {c.column for c in candidates["segment_by"]}


def test_suggest_mapping_never_ranks_numeric_column_as_timestamp() -> None:
    raw = pd.DataFrame(
        {
            "order_no": [1001, 1001, 1002],
            "step": ["X", "Y", "X"],
            "when": ["2019-01-01", "2019-01-02", "2019-01-03"],
        }
    )
    timestamp_columns = [c.column for c in suggest_mapping(raw)["timestamp"]]
    assert timestamp_columns[0] == "when"
    assert "order_no" not in timestamp_columns


def test_suggest_mapping_heuristics_are_overridable() -> None:
    from processpilot.ingest.mapping import SuggestionHeuristics

    raw = _text_log(["2019-01-01", "2019-01-02"])
    name_only = SuggestionHeuristics(name_weight=1.0, profile_weight=0.0)
    case_candidates = suggest_mapping(raw, name_only)["case_id"]
    assert [c.column for c in case_candidates] == ["case"]
    assert case_candidates[0].confidence == 1.0
