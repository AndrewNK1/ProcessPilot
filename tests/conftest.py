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

from processpilot.mining.settings import MiningSettings


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


@pytest.fixture
def mining_log() -> pd.DataFrame:
    """6 clean cases for the mining engine, every expected number hand-derived.

    Times are hours after each case's own start day (cases never overlap in
    meaning, so absolute dates do not matter). Resources in brackets.

        c1  A(0)[r1]  B(1)[r1]  C(3)[r2]
        c2  A(0)[r1]  B(2)[r2]  C(4)[r2]
        c3  A(0)[r1]  B(1)[r1]  B(5)[r1]  C(6)[r2]        rework on B
        c4  A(0)[r1]  C(10)[r3]                          skips B
        c5  A(0)[r1]  B(1)[r1]  C(2)[r2]  C(2)[r2]       batch repeat of C
        c6  A(0)[r2]  B(3)[r2]                           never reaches C

    Answer key (mining view = c5's same-instant C collapsed, 17 events):
        A->B  freq 5, waits [1,2,1,1,3]h  median 1h, mean 1.6h, P90 2.6h, share 5/6
        B->C  freq 4, waits [2,2,1,1]h    median 1.5h, mean 1.5h, P90 2h, share 4/6
        B->B  freq 1, 4h; A->C freq 1, 10h; each share 1/6
        impact (h): A->C 1.667 > B->C 1.0 > A->B 0.833 > B->B 0.667
        variants: ABC x3 (c1,c2,c5; median/mean throughput 3h), then AB, ABBC, AC
        rework: only B (c3), extra = 6h - median(3,4,2,3)=3h -> 3h, impact 0.5h
        throughput per case (h): 3,4,6,10,2,3 -> median 3.5, mean 4.667, P90 8
        workload (raw, 18 events): r1 9, r2 8, r3 1
        handover waits [2,2,1,10,1] median 2h; same-resource [1,1,1,2,3,4] median 1.5h
    """
    spec = {
        "c1": [("A", 0, "r1"), ("B", 1, "r1"), ("C", 3, "r2")],
        "c2": [("A", 0, "r1"), ("B", 2, "r2"), ("C", 4, "r2")],
        "c3": [("A", 0, "r1"), ("B", 1, "r1"), ("B", 5, "r1"), ("C", 6, "r2")],
        "c4": [("A", 0, "r1"), ("C", 10, "r3")],
        "c5": [("A", 0, "r1"), ("B", 1, "r1"), ("C", 2, "r2"), ("C", 2, "r2")],
        "c6": [("A", 0, "r2"), ("B", 3, "r2")],
    }
    rows = []
    for day, (case, events) in enumerate(spec.items(), start=1):
        start = pd.Timestamp(2019, 1, day)
        rows += [(case, act, start + pd.Timedelta(hours=h), res) for act, h, res in events]
    raw = pd.DataFrame(rows, columns=["case", "activity", "time", "user"])

    from processpilot.ingest.mapping import ColumnMapping, apply_mapping

    return apply_mapping(
        raw, ColumnMapping(case_id="case", activity="activity", timestamp="time", resource="user")
    )


@pytest.fixture
def mining_settings() -> MiningSettings:
    """Settings with rules exercising every conformance code path on ``mining_log``."""
    return MiningSettings.model_validate(
        {
            "discovery": {
                "inductive_miner_noise_threshold": 0.0,
                "dfg_min_edge_frequency_share": 0.5,
            },
            "analysis": {
                "top_n_bottlenecks": 10,
                "top_n_variants": 10,
                "top_n_rework": 10,
                "top_n_resources": 10,
                "top_n_handovers": 10,
                "max_example_cases": 5,
            },
            "case_completion": {"end_activities": ["C"]},
            "conformance_rules": [
                {
                    "id": "R-1",
                    "name": "a_before_c",
                    "type": "activity_a_before_activity_b",
                    "description": "A precedes C",
                    "params": {"a": "A", "b": "C"},
                },
                {
                    "id": "R-2",
                    "name": "c_before_b",
                    "type": "activity_a_before_activity_b",
                    "description": "C precedes B",
                    "params": {"a": "C", "b": "B"},
                },
                {
                    "id": "R-3",
                    "name": "c_occurs",
                    "type": "activity_must_occur",
                    "description": "C must occur",
                    "params": {"activity": "C"},
                },
                {
                    "id": "R-4",
                    "name": "z_occurs",
                    "type": "activity_must_occur",
                    "description": "Z must occur",
                    "params": {"activity": "Z"},
                },
                {
                    "id": "R-5",
                    "name": "other_segment_only",
                    "type": "activity_must_occur",
                    "description": "not for this segment",
                    "params": {"activity": "A"},
                    "applies_to_segments": ["other"],
                },
            ],
        }
    )
