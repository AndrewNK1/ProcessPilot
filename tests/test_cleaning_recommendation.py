"""ProcessPilot's cleaning recommendation on the hand-built log with known defects.

``canonical_log`` (conftest): c3 has one event, c4 a duplicate event, c6 a
1970 timestamp, c7 a row without case id, c8 a missing activity; 20 rows.
Every expected count below follows from that by hand.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from processpilot.config import load_preset_yaml
from processpilot.pipeline import (
    RecommendationSettings,
    recommend_cleaning,
    validate_log,
)

LENIENT = RecommendationSettings(max_event_share_removed=0.5, single_event_case_share_to_remove=0.5)


def _preset(collapse: bool = True, with_range: bool = True) -> dict[str, Any]:
    preset = load_preset_yaml("generic")
    preset["validation"] = {"min_events_per_case_warn": 2}
    if with_range:
        preset["validation"] |= {
            "plausible_timestamp_min": "2017-01-01",
            "plausible_timestamp_max": "2020-05-01",
        }
    preset["preprocessing"] = {"collapse_same_instant_repeats": collapse}
    return preset


def _advice(df: pd.DataFrame, preset: dict[str, Any], settings: RecommendationSettings) -> dict:
    report = validate_log(df, preset)
    rec = recommend_cleaning(df, report, preset, settings)
    return {a.option: a for a in rec.advice}


def test_recommends_removing_gaps_and_junk_dates_with_exact_costs(canonical_log) -> None:
    a = _advice(canonical_log, _preset(), LENIENT)
    missing = a["drop_cases_with_missing_values"]
    # The id-less row goes alone; c8 goes whole (2 rows) for its missing activity.
    assert (missing.recommended, missing.rows_removed, missing.cases_removed) == (True, 3, 1)
    junk = a["drop_cases_with_out_of_range_timestamps"]
    assert (junk.recommended, junk.rows_removed, junk.cases_removed) == (True, 2, 1)
    assert "2017-01-01 to 2020-05-01" in junk.reason
    assert junk.event_share_removed == 2 / 20


def test_keeps_few_single_event_cases_and_batch_duplicates(canonical_log) -> None:
    a = _advice(canonical_log, _preset(), LENIENT)
    single = a["drop_single_event_cases"]
    assert not single.recommended and single.cases_removed == 1
    assert "real cases" in single.reason
    dup = a["drop_exact_duplicate_events"]
    assert not dup.recommended and dup.rows_removed == 1
    assert "counts same-case, same-activity, same-instant events once" in dup.reason


def test_many_single_event_cases_are_recommended_for_removal(canonical_log) -> None:
    strict = RecommendationSettings(
        max_event_share_removed=0.5, single_event_case_share_to_remove=0.05
    )
    single = _advice(canonical_log, _preset(), strict)["drop_single_event_cases"]
    assert single.recommended and "zero throughput" in single.reason


def test_large_removals_are_refused_as_a_mapping_or_range_problem(canonical_log) -> None:
    tight = RecommendationSettings(
        max_event_share_removed=0.01, single_event_case_share_to_remove=0.5
    )
    a = _advice(canonical_log, _preset(), tight)
    assert not a["drop_cases_with_missing_values"].recommended
    assert "mapping or timestamp format" in a["drop_cases_with_missing_values"].reason
    assert not a["drop_cases_with_out_of_range_timestamps"].recommended
    assert "range in the preset is probably too narrow" in (
        a["drop_cases_with_out_of_range_timestamps"].reason
    )


def test_without_a_range_or_without_collapsing(canonical_log) -> None:
    a = _advice(canonical_log, _preset(collapse=False, with_range=False), LENIENT)
    assert not a["drop_cases_with_out_of_range_timestamps"].recommended
    assert "no plausible date range" in a["drop_cases_with_out_of_range_timestamps"].reason
    dup = a["drop_exact_duplicate_events"]
    assert dup.recommended and "phantom rework" in dup.reason


def test_clean_log_gets_nothing_recommended(canonical_log) -> None:
    clean = canonical_log[canonical_log["case_id"].isin(["c1", "c2", "c5"])].reset_index(drop=True)
    rec = recommend_cleaning(clean, validate_log(clean, _preset()), _preset(), LENIENT)
    assert not any(a.recommended for a in rec.advice)
    assert all(a.rows_removed == 0 for a in rec.advice)
    assert rec.policy().model_dump() == {a.option: False for a in rec.advice}


def test_configured_thresholds_load() -> None:
    settings = RecommendationSettings.load()
    assert 0 < settings.max_event_share_removed < 1
