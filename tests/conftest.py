"""Shared pytest fixtures.

WHY a hand-built synthetic log is the primary test fixture: on BPI 2019 nobody
knows the correct answer, so a passing assertion only proves the code ran. The
synthetic log here is small enough that every defect it contains -- and the
report entry it must produce -- was worked out by hand before being encoded as
an assertion.

BPI 2019 is used only in tests marked ``@pytest.mark.bpi2019``, which are
excluded from the default run because they need a 695 MB file outside the repo.
"""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def synthetic_raw_log() -> pd.DataFrame:
    """8 hand-built cases with known, deliberate defects.

    Defects, by case:
        c1  clean: A -> B -> C, three distinct timestamps
        c2  clean, different resource and timestamps
        c3  single-event case (only A) -- triggers single_event_case
        c4  duplicate event: the same (case, activity, timestamp, resource)
            row appears twice -- triggers duplicate_event
        c5  timestamp tie: B and C share one timestamp -- triggers
            timestamp_tie_within_case (order between them is ambiguous)
        c6  junk timestamp (year 1970, outside the plausible 2017-2020 range
            used in tests) -- triggers timestamp_out_of_plausible_range
        c7  null case_id on one row -- triggers null_case_id
        c8  null activity on one row -- triggers null_activity

    Columns use XES-like raw names (``case:concept:name`` etc.) so the same
    fixture exercises both the XES-style mapping path and, via
    ``canonical_log``, the already-mapped canonical path.
    """
    rows = [
        # c1: clean happy path
        ("c1", "A", "2019-01-01 09:00:00", "r1"),
        ("c1", "B", "2019-01-01 10:00:00", "r1"),
        ("c1", "C", "2019-01-02 09:00:00", "r2"),
        # c2: clean, different resource/timestamps
        ("c2", "A", "2019-01-03 08:00:00", "r2"),
        ("c2", "B", "2019-01-03 09:30:00", "r3"),
        ("c2", "C", "2019-01-03 12:00:00", "r2"),
        # c3: single event
        ("c3", "A", "2019-01-04 08:00:00", "r1"),
        # c4: exact duplicate row
        ("c4", "A", "2019-01-05 08:00:00", "r1"),
        ("c4", "B", "2019-01-05 09:00:00", "r1"),
        ("c4", "B", "2019-01-05 09:00:00", "r1"),
        # c5: timestamp tie between B and C
        ("c5", "A", "2019-01-06 08:00:00", "r2"),
        ("c5", "B", "2019-01-06 09:00:00", "r2"),
        ("c5", "C", "2019-01-06 09:00:00", "r3"),
        # c6: junk timestamp (year 1970)
        ("c6", "A", "1970-01-01 00:00:00", "r1"),
        ("c6", "B", "2019-01-07 09:00:00", "r1"),
        # c7: null case_id on one row (second row of this trio)
        ("c7", "A", "2019-01-08 08:00:00", "r1"),
        (None, "B", "2019-01-08 09:00:00", "r1"),
        ("c7", "C", "2019-01-08 10:00:00", "r1"),
        # c8: null activity on one row
        ("c8", "A", "2019-01-09 08:00:00", "r2"),
        ("c8", None, "2019-01-09 09:00:00", "r2"),
    ]
    columns = ["case:concept:name", "concept:name", "time:timestamp", "org:resource"]
    df = pd.DataFrame(rows, columns=columns)
    df["time:timestamp"] = pd.to_datetime(df["time:timestamp"])
    return df


@pytest.fixture
def canonical_log(synthetic_raw_log: pd.DataFrame) -> pd.DataFrame:
    """``synthetic_raw_log`` already mapped to the canonical schema."""
    from processpilot.ingest.mapping import ColumnMapping, apply_mapping

    mapping = ColumnMapping(
        case_id="case:concept:name",
        activity="concept:name",
        timestamp="time:timestamp",
        resource="org:resource",
    )
    return apply_mapping(synthetic_raw_log, mapping)
