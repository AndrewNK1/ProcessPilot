"""Resources and conformance on the hand-built ``mining_log``."""

from __future__ import annotations

import pandas as pd
import pytest

from processpilot.mining.conformance import ConformanceResult, check_rules
from processpilot.mining.prepare import collapse_same_instant_repeats
from processpilot.mining.resources import analyse_resources
from processpilot.mining.settings import ConformanceRule, MiningSettings

H = 3600.0


@pytest.fixture
def view(mining_log: pd.DataFrame) -> pd.DataFrame:
    return collapse_same_instant_repeats(mining_log)[0]


def test_workload_counts_raw_events(mining_log: pd.DataFrame, view: pd.DataFrame) -> None:
    res = analyse_resources(mining_log, view, top_n_resources=10, top_n_handovers=10)
    assert [(w.resource, w.event_count, w.case_count) for w in res.workload] == [
        ("r1", 9, 5),
        ("r2", 8, 5),
        ("r3", 1, 1),
    ]
    assert res.workload[0].event_share == pytest.approx(9 / 18)


def test_handover_vs_same_resource_waits(mining_log: pd.DataFrame, view: pd.DataFrame) -> None:
    res = analyse_resources(mining_log, view, top_n_resources=10, top_n_handovers=10)
    assert res.median_handover_wait_seconds == 2 * H
    assert res.median_same_resource_wait_seconds == 1.5 * H


def test_handover_edges_ranked_by_impact(mining_log: pd.DataFrame, view: pd.DataFrame) -> None:
    res = analyse_resources(mining_log, view, top_n_resources=10, top_n_handovers=10)
    assert [(h.source, h.target) for h in res.handovers] == [("A", "C"), ("B", "C"), ("A", "B")]
    bc = res.handovers[1]
    assert (bc.handover_count, bc.handover_case_count) == (3, 3)
    assert bc.handover_share_of_transitions == pytest.approx(3 / 4)
    assert bc.median_handover_wait_seconds == 1 * H
    assert bc.median_same_resource_wait_seconds == 2 * H
    assert res.handovers[0].median_same_resource_wait_seconds is None  # A->C only by handover


def test_resources_skipped_explicitly_without_resource_column(view: pd.DataFrame) -> None:
    no_res = view.drop(columns=["resource"])
    res = analyse_resources(no_res, no_res, top_n_resources=10, top_n_handovers=10)
    assert not res.available
    assert "no resource column" in (res.skipped_reason or "")
    assert res.workload == [] and res.handovers == []


def _by_id(results: list[ConformanceResult]) -> dict[str, ConformanceResult]:
    return {r.rule_id: r for r in results}


def test_conformance_rule_outcomes(view: pd.DataFrame, mining_settings: MiningSettings) -> None:
    results = _by_id(check_rules(view, "<all>", mining_settings.conformance_rules, ["C"], 5))
    assert set(results) == {"R-1", "R-2", "R-3", "R-4"}  # R-5 scoped to another segment

    r1 = results["R-1"]  # A before C: holds wherever both occur
    assert (r1.evaluated_cases, r1.violation_count) == (5, 0)

    r2 = results["R-2"]  # C before B: broken in every case with both
    assert (r2.evaluated_cases, r2.violation_count) == (4, 4)
    assert r2.example_case_ids == ["c1", "c2", "c3", "c5"]


def test_must_occur_reports_all_and_completed_cases(
    view: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    r3 = _by_id(check_rules(view, "<all>", mining_settings.conformance_rules, ["C"], 5))["R-3"]
    # c6 lacks C -- but c6 never completed (ends in B), so among completed: 0.
    assert (r3.evaluated_cases, r3.violation_count) == (6, 1)
    assert (r3.completed_evaluated_cases, r3.completed_violation_count) == (5, 0)


def test_rule_naming_absent_activity_is_flagged(
    view: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    r4 = _by_id(check_rules(view, "<all>", mining_settings.conformance_rules, ["C"], 5))["R-4"]
    assert r4.activities_missing_from_segment == ["Z"]
    assert r4.violation_count == 6


def test_completed_counts_absent_without_completion_definition(view: pd.DataFrame) -> None:
    rule = ConformanceRule(
        id="R-X", name="c", type="activity_must_occur", description="d", params={"activity": "C"}
    )
    (result,) = check_rules(view, "<all>", [rule], [], 5)
    assert result.completed_evaluated_cases is None
    assert result.completed_violation_share is None


def test_timestamp_tie_is_not_a_violation() -> None:
    ts = pd.Timestamp("2019-01-01 10:00")
    df = pd.DataFrame(
        {
            "case_id": pd.array(["t1", "t1"], dtype="string"),
            "activity": pd.array(["B", "A"], dtype="string"),
            "timestamp": pd.Series([ts, ts]).astype("datetime64[ns]"),
        }
    )
    rule = ConformanceRule(
        id="R-T",
        name="a_before_b",
        type="activity_a_before_activity_b",
        description="d",
        params={"a": "A", "b": "B"},
    )
    (result,) = check_rules(df, "<all>", [rule], [], 5)
    assert result.violation_count == 0
    assert result.tied_cases == 1
