"""Unit tests for processpilot.ingest.validate, against the synthetic log.

Each test targets exactly one of the eight deliberate defects documented in
``conftest.synthetic_raw_log`` and checks that validate() reports it -- and
that nothing is ever silently dropped by validate() itself.
"""

from __future__ import annotations

import pandas as pd
import pytest

from processpilot.ingest.mapping import ColumnMapping, apply_mapping
from processpilot.ingest.validate import (
    DataQualityIssue,
    DataQualityReport,
    apply_cleaning,
    validate,
)


@pytest.fixture
def dirty_canonical_log(synthetic_raw_log: pd.DataFrame) -> pd.DataFrame:
    """Like ``canonical_log``, mapped from the same fixture.

    Named separately because this is specifically the fixture ``validate()``
    is meant to run against -- the nulls and other defects are the point.
    """
    mapping = ColumnMapping(
        case_id="case:concept:name",
        activity="concept:name",
        timestamp="time:timestamp",
        resource="org:resource",
    )
    return apply_mapping(synthetic_raw_log, mapping)


def _issue(report: DataQualityReport, code: str) -> DataQualityIssue | None:
    matches = [i for i in report.issues if i.code == code]
    return matches[0] if matches else None


def test_validate_does_not_mutate_input(dirty_canonical_log: pd.DataFrame) -> None:
    before = dirty_canonical_log.copy(deep=True)
    validate(dirty_canonical_log)
    pd.testing.assert_frame_equal(dirty_canonical_log, before)


def test_null_case_id_reported(dirty_canonical_log: pd.DataFrame) -> None:
    report = validate(dirty_canonical_log)
    issue = _issue(report, "null_case_id")
    assert issue is not None
    assert issue.severity == "error"
    assert issue.count == 1


def test_null_activity_reported(dirty_canonical_log: pd.DataFrame) -> None:
    report = validate(dirty_canonical_log)
    issue = _issue(report, "null_activity")
    assert issue is not None
    assert issue.count == 1


def test_single_event_case_reported(dirty_canonical_log: pd.DataFrame) -> None:
    report = validate(dirty_canonical_log, min_events_per_case_warn=2)
    issue = _issue(report, "single_event_case")
    assert issue is not None
    assert issue.severity == "warn"
    assert issue.count == 1  # only c3


def test_duplicate_event_reported(dirty_canonical_log: pd.DataFrame) -> None:
    report = validate(dirty_canonical_log)
    issue = _issue(report, "duplicate_event")
    assert issue is not None
    assert issue.count == 2  # the two identical B rows in c4


def test_timestamp_tie_reported(dirty_canonical_log: pd.DataFrame) -> None:
    report = validate(dirty_canonical_log)
    issue = _issue(report, "timestamp_tie_within_case")
    assert issue is not None
    assert issue.severity == "info"
    # Two ties, each counting only the later of the pair: c5's B/C (same instant,
    # different activities) and c4's exact-duplicate B rows (a duplicate is, by
    # definition, also a timestamp tie within its case).
    assert issue.count == 2


def test_out_of_range_timestamp_reported_with_configured_bounds(
    dirty_canonical_log: pd.DataFrame,
) -> None:
    report = validate(
        dirty_canonical_log,
        plausible_timestamp_min="2017-01-01",
        plausible_timestamp_max="2020-01-01",
    )
    issue = _issue(report, "timestamp_out_of_plausible_range")
    assert issue is not None
    assert issue.severity == "error"
    assert issue.count == 1  # c6's 1970 row


def test_out_of_range_timestamp_not_checked_without_configured_bounds(
    dirty_canonical_log: pd.DataFrame,
) -> None:
    report = validate(dirty_canonical_log)
    assert _issue(report, "timestamp_out_of_plausible_range") is None


def test_high_activity_cardinality_warns_when_threshold_exceeded(
    dirty_canonical_log: pd.DataFrame,
) -> None:
    report = validate(dirty_canonical_log, max_distinct_activities_warn=2)
    issue = _issue(report, "high_activity_cardinality")
    assert issue is not None
    assert issue.severity == "warn"


def test_log_size_summary_always_present(dirty_canonical_log: pd.DataFrame) -> None:
    report = validate(dirty_canonical_log)
    issue = _issue(report, "log_size")
    assert issue is not None
    assert issue.count == len(dirty_canonical_log)
    assert report.event_count == len(dirty_canonical_log)


def test_validate_never_drops_rows_itself(dirty_canonical_log: pd.DataFrame) -> None:
    report = validate(
        dirty_canonical_log,
        plausible_timestamp_min="2017-01-01",
        plausible_timestamp_max="2020-01-01",
    )
    assert report.event_count == len(dirty_canonical_log)
    assert report.cleaning_log == []


def test_apply_cleaning_drops_only_cases_with_out_of_range_timestamps(
    dirty_canonical_log: pd.DataFrame,
) -> None:
    report = validate(
        dirty_canonical_log,
        plausible_timestamp_min="2017-01-01",
        plausible_timestamp_max="2020-01-01",
    )
    cleaned_df, cleaned_report = apply_cleaning(
        dirty_canonical_log,
        report,
        drop_cases_with_out_of_range_timestamps=True,
        plausible_timestamp_min="2017-01-01",
        plausible_timestamp_max="2020-01-01",
    )
    assert "c6" not in set(cleaned_df["case_id"].dropna())
    assert len(cleaned_report.cleaning_log) == 1
    assert cleaned_report.cleaning_log[0].code == "drop_cases_with_out_of_range_timestamps"
    assert cleaned_report.cleaning_log[0].cases_removed == 1
    # every other case must survive untouched
    assert set(cleaned_df["case_id"].dropna()) == {"c1", "c2", "c3", "c4", "c5", "c7", "c8"}


def test_apply_cleaning_is_opt_in_per_flag(dirty_canonical_log: pd.DataFrame) -> None:
    report = validate(dirty_canonical_log)
    cleaned_df, cleaned_report = apply_cleaning(dirty_canonical_log, report)
    assert len(cleaned_df) == len(dirty_canonical_log)
    assert cleaned_report.cleaning_log == []


def test_apply_cleaning_drops_single_event_cases_when_requested(
    dirty_canonical_log: pd.DataFrame,
) -> None:
    report = validate(dirty_canonical_log)
    cleaned_df, cleaned_report = apply_cleaning(
        dirty_canonical_log, report, drop_single_event_cases=True
    )
    assert "c3" not in set(cleaned_df["case_id"].dropna())
    assert cleaned_report.cleaning_log[0].cases_removed == 1


def test_apply_cleaning_drops_exact_duplicates_when_requested(
    dirty_canonical_log: pd.DataFrame,
) -> None:
    report = validate(dirty_canonical_log)
    cleaned_df, cleaned_report = apply_cleaning(
        dirty_canonical_log, report, drop_exact_duplicate_events=True
    )
    c4 = cleaned_df[cleaned_df["case_id"] == "c4"]
    assert len(c4) == 2  # A, B -- one copy of the duplicated B removed
    assert cleaned_report.cleaning_log[0].rows_removed == 1


# --- regressions found in the Phase 1 review --------------------------------


def test_unparseable_timestamp_is_not_double_reported_as_null() -> None:
    raw = pd.DataFrame(
        {
            "case": ["c1", "c1", "c2"],
            "act": ["A", "B", "A"],
            "ts": ["2019-01-01 10:00", "garbage", None],
        }
    )
    report = validate(
        apply_mapping(raw, ColumnMapping(case_id="case", activity="act", timestamp="ts"))
    )
    unparseable = _issue(report, "unparseable_timestamp")
    null = _issue(report, "null_timestamp")
    assert unparseable is not None and unparseable.count == 1
    assert unparseable.example_rows[0]["timestamp_raw"] == "garbage"
    assert null is not None and null.count == 1  # only the genuinely missing c2 value


def test_non_monotonic_timestamp_detected_in_unsorted_input(
    dirty_canonical_log: pd.DataFrame,
) -> None:
    """Regression: the check re-sorted first, so it could never fire."""
    c1 = dirty_canonical_log[dirty_canonical_log["case_id"] == "c1"]
    reversed_c1 = c1.iloc[::-1].reset_index(drop=True)
    issue = _issue(validate(reversed_c1), "non_monotonic_timestamp")
    assert issue is not None
    assert issue.count == 2  # B after C, then A after B


def test_canonical_log_has_no_non_monotonic_rows(dirty_canonical_log: pd.DataFrame) -> None:
    assert _issue(validate(dirty_canonical_log), "non_monotonic_timestamp") is None


def test_single_event_case_share_is_a_share_of_rows(dirty_canonical_log: pd.DataFrame) -> None:
    issue = _issue(validate(dirty_canonical_log), "single_event_case")
    assert issue is not None
    # c3 is the only single-event case: 1 of the fixture's event rows.
    assert issue.share == pytest.approx(1 / len(dirty_canonical_log))
