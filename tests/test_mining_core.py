"""Mining core on the hand-built ``mining_log``.

Covers prepare, DFG, bottlenecks, variants, rework and KPIs; expected values
are the fixture's answer key.
"""

from __future__ import annotations

import pandas as pd
import pytest

from processpilot.mining.bottlenecks import rank_bottlenecks
from processpilot.mining.discovery import DFGEdge, DFGResult, compute_dfg
from processpilot.mining.kpis import compute_kpis
from processpilot.mining.prepare import (
    case_throughput_seconds,
    collapse_same_instant_repeats,
    directly_follows_pairs,
    event_waits,
)
from processpilot.mining.rework import analyse_rework
from processpilot.mining.variants import analyse_variants

H = 3600.0


@pytest.fixture
def view(mining_log: pd.DataFrame) -> pd.DataFrame:
    collapsed, _ = collapse_same_instant_repeats(mining_log)
    return collapsed


def _edge(dfg: DFGResult, source: str, target: str) -> DFGEdge:
    return next(e for e in dfg.edges if (e.source, e.target) == (source, target))


def test_collapse_removes_only_the_same_instant_repeat(mining_log: pd.DataFrame) -> None:
    collapsed, removed = collapse_same_instant_repeats(mining_log)
    assert removed == 1
    assert len(collapsed) == 17
    assert list(collapsed.loc[collapsed["case_id"] == "c5", "activity"]) == ["A", "B", "C"]


def test_rework_at_different_times_is_not_collapsed(view: pd.DataFrame) -> None:
    assert list(view.loc[view["case_id"] == "c3", "activity"]) == ["A", "B", "B", "C"]


def test_directly_follows_pairs_never_cross_cases(view: pd.DataFrame) -> None:
    pairs = directly_follows_pairs(view)
    assert len(pairs) == 17 - 6  # events minus one per case
    assert (pairs["wait_seconds"] >= 0).all()


def test_dfg_edge_statistics_match_answer_key(view: pd.DataFrame) -> None:
    dfg = compute_dfg(view)
    ab = _edge(dfg, "A", "B")
    assert (ab.frequency, ab.case_count) == (5, 5)
    assert ab.case_share == pytest.approx(5 / 6)
    assert ab.median_wait_seconds == 1 * H
    assert ab.mean_wait_seconds == pytest.approx(1.6 * H)
    assert ab.p90_wait_seconds == pytest.approx(2.6 * H)
    bc = _edge(dfg, "B", "C")
    assert (bc.frequency, bc.median_wait_seconds, bc.p90_wait_seconds) == (4, 1.5 * H, 2 * H)
    assert _edge(dfg, "B", "B").median_wait_seconds == 4 * H
    assert _edge(dfg, "A", "C").median_wait_seconds == 10 * H
    assert len(dfg.edges) == 4


def test_dfg_start_and_end_activities(view: pd.DataFrame) -> None:
    dfg = compute_dfg(view)
    assert dfg.start_activities == {"A": 6}
    assert dfg.end_activities == {"B": 1, "C": 5}


def test_dfg_frequencies_agree_with_pm4py(view: pd.DataFrame) -> None:
    """Cross-check: on a log without timestamp ties, pm4py must agree with us."""
    import pm4py

    from processpilot.schema import to_pm4py

    pm4py_dfg, _, _ = pm4py.discover_dfg(to_pm4py(view))
    ours = {(e.source, e.target): e.frequency for e in compute_dfg(view).edges}
    assert ours == dict(pm4py_dfg)


def test_dfg_filtered_view_reports_hidden_edges(view: pd.DataFrame) -> None:
    filtered, hidden = compute_dfg(view).filtered(min_case_share=0.5)
    assert {(e.source, e.target) for e in filtered.edges} == {("A", "B"), ("B", "C")}
    assert hidden == 2


def test_bottlenecks_ranked_by_median_wait_times_case_share(view: pd.DataFrame) -> None:
    ranked = rank_bottlenecks(compute_dfg(view), top_n=10)
    assert [(b.edge.source, b.edge.target) for b in ranked] == [
        ("A", "C"),
        ("B", "C"),
        ("A", "B"),
        ("B", "B"),
    ]
    assert ranked[0].impact_score == pytest.approx(10 * H / 6)
    assert [b.rank for b in ranked] == [1, 2, 3, 4]


def test_bottlenecks_respect_top_n(view: pd.DataFrame) -> None:
    assert len(rank_bottlenecks(compute_dfg(view), top_n=2)) == 2


def test_variants_happy_path_and_tie_order(view: pd.DataFrame) -> None:
    summary = analyse_variants(view, top_n=10, max_example_cases=5)
    assert summary.variant_count == 4
    happy = summary.variants[0]
    assert happy.activities == ["A", "B", "C"]
    assert happy.is_happy_path
    assert happy.case_count == 3
    assert happy.case_share == pytest.approx(0.5)
    assert happy.mean_throughput_seconds == pytest.approx(3 * H)
    assert happy.example_case_ids == ["c1", "c2", "c5"]
    # Equal-count variants are ordered by their activity sequence.
    assert [v.activities for v in summary.variants[1:]] == [
        ["A", "B"],
        ["A", "B", "B", "C"],
        ["A", "C"],
    ]
    assert summary.top_n_case_share == pytest.approx(1.0)


def test_rework_found_only_on_b_with_extra_time(view: pd.DataFrame) -> None:
    items = analyse_rework(view, top_n=10, max_example_cases=5)
    assert [i.activity for i in items] == ["B"]
    b = items[0]
    assert (b.case_count, b.extra_occurrences, b.direct_loop_count) == (1, 1, 1)
    assert b.median_throughput_with_rework_seconds == 6 * H
    assert b.median_throughput_single_occurrence_seconds == 3 * H
    assert b.extra_median_throughput_seconds == 3 * H
    assert b.impact_score == pytest.approx(3 * H / 6)
    assert b.example_case_ids == ["c3"]


def test_batch_repeat_is_rework_only_when_not_collapsed(mining_log: pd.DataFrame) -> None:
    items = analyse_rework(mining_log, top_n=10, max_example_cases=5)
    assert {i.activity for i in items} == {"B", "C"}


def test_kpis_match_answer_key(view: pd.DataFrame) -> None:
    kpis = compute_kpis(view, raw_event_count=18, collapsed=1)
    assert (kpis.case_count, kpis.event_count, kpis.activity_count) == (6, 17, 3)
    assert kpis.median_throughput_seconds == 3.5 * H
    assert kpis.mean_throughput_seconds == pytest.approx(28 / 6 * H)
    assert kpis.p90_throughput_seconds == pytest.approx(8 * H)


def test_inductive_miner_tree_contains_every_activity_once(view: pd.DataFrame) -> None:
    from processpilot.process_tree import discover_tree, labels

    tree = discover_tree(view, noise_threshold=0.0)
    assert sorted(labels(tree)) == ["A", "B", "C"]


def test_event_waits_sum_to_case_throughput(view: pd.DataFrame) -> None:
    waits = event_waits(view)
    assert list(waits.columns) == ["case_id", "activity", "wait_seconds"]
    first = ~waits["case_id"].duplicated()
    assert waits.loc[first, "wait_seconds"].isna().all()
    summed = waits.groupby("case_id")["wait_seconds"].sum()
    pd.testing.assert_series_equal(
        summed, case_throughput_seconds(view), check_names=False, check_index_type=False
    )
    # c3: A(0) B(1) B(5) C(6) -> waits before B, B, C are 1h, 4h, 1h.
    c3 = waits.loc[waits["case_id"] == "c3", "wait_seconds"].tolist()[1:]
    assert c3 == [1 * H, 4 * H, 1 * H]
