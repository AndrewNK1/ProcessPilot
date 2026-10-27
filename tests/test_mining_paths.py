"""Per-path step waits (the wait ladder) on the hand-built ``mining_log``.

Answer key for path A-B-C (c1, c2 and c5 after c5's batch repeat is
collapsed): waits into B [1, 2, 1] h, into C [2, 2, 1] h; throughput
[3, 4, 2] h. c3 (A-B-B-C) and c4 (A-C) share activities but not the whole
sequence, so they must not count.
"""

from __future__ import annotations

import pandas as pd
import pytest

from processpilot.mining.paths import path_waits
from processpilot.mining.prepare import collapse_same_instant_repeats, event_waits

H = 3600.0


@pytest.fixture
def case_waits(mining_log: pd.DataFrame) -> pd.DataFrame:
    collapsed, _ = collapse_same_instant_repeats(mining_log)
    return event_waits(collapsed)


def test_only_cases_with_exactly_this_sequence_count(case_waits: pd.DataFrame) -> None:
    result = path_waits(case_waits, ["A", "B", "C"])
    assert result.case_count == 3
    first, into_b, into_c = result.steps
    assert first.mean_wait_seconds is None and first.median_wait_seconds is None
    assert into_b.mean_wait_seconds == pytest.approx(4 / 3 * H)
    assert into_b.median_wait_seconds == pytest.approx(1 * H)
    assert into_c.mean_wait_seconds == pytest.approx(5 / 3 * H)
    assert into_c.median_wait_seconds == pytest.approx(2 * H)
    assert result.median_throughput_seconds == pytest.approx(3 * H)


def test_step_means_add_up_to_the_paths_mean_throughput(case_waits: pd.DataFrame) -> None:
    for path in (["A", "B", "C"], ["A", "B", "B", "C"], ["A", "C"], ["A", "B"]):
        result = path_waits(case_waits, path)
        total = sum(s.mean_wait_seconds or 0.0 for s in result.steps)
        assert total == pytest.approx(result.mean_throughput_seconds)


def test_repeated_activity_is_a_separate_step(case_waits: pd.DataFrame) -> None:
    result = path_waits(case_waits, ["A", "B", "B", "C"])
    assert result.case_count == 1
    assert [s.median_wait_seconds for s in result.steps] == [None, 1 * H, 4 * H, 1 * H]


def test_a_path_no_case_follows_is_empty_not_an_error(case_waits: pd.DataFrame) -> None:
    result = path_waits(case_waits, ["C", "A"])
    assert result.case_count == 0
    assert result.mean_throughput_seconds is None
    assert all(s.mean_wait_seconds is None for s in result.steps)
