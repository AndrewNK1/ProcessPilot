"""Activity waits, variant filter and reference-model conformance on ``mining_log``.

Answer key (mining view, c5's same-instant C collapsed), hours:

Waits before each activity (time since the previous event in the case):
    B  from A [1,2,1,1,3] + from B [4]  -> [1,1,1,2,3,4]
       median 1.5, mean 2.0, P90 3.5; cases c1,c2,c3,c5,c6 = 5/6; impact 1.25
       top predecessor A, 5 of 6 occurrences
    C  from B [2,2,1,1] + from A [10]   -> [1,1,2,2,10]
       median 2.0, mean 3.2, P90 6.8; cases c1..c5 = 5/6; impact 1.667
       top predecessor B, 4 of 5
    A  only ever starts a case -> no waiting time
    rank: C (1.667) before B (1.25)

Variant ranking (count desc, then sequence): ABC x3, AB, ABBC, AC (x1 each).
    top_k 1          -> ABC: c1, c2, c5
    coverage 0.6     -> ABC (0.5) + AB (0.667): c1, c2, c5, c6
    min_case_share .2 -> ABC only

Replay on tests/data/abc_reference.bpmn (strict A -> B -> C):
    ABC (c1, c2, c5) fit
    ABBC (c3)  B executed where not allowed
    AC (c4)    C executed where not allowed (B skipped)
    AB (c6)    stops before the model's end, nothing flagged
    conforming 3 of 6; completed (ends in C: c1..c5) 3 of 5
    tokens: missing 3, consumed 23, remaining 3, produced 23 -> fitness 20/23
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from processpilot.config import find_project_root
from processpilot.mining.activities import compute_activity_waits
from processpilot.mining.filtering import filter_variants
from processpilot.mining.prepare import collapse_same_instant_repeats
from processpilot.mining.reference import check_reference_model, load_reference_model
from processpilot.mining.run import mine_segment
from processpilot.mining.settings import MiningSettings, VariantFilterSettings

H = 3600.0
REFERENCE = "tests/data/abc_reference.bpmn"


@pytest.fixture
def view(mining_log: pd.DataFrame) -> pd.DataFrame:
    collapsed, _ = collapse_same_instant_repeats(mining_log)
    return collapsed


@pytest.fixture
def abc_model():  # noqa: ANN201 - pm4py BPMN object
    import pm4py

    return pm4py.read_bpmn(str(find_project_root() / REFERENCE))


def _with(settings: MiningSettings, **update: object) -> MiningSettings:
    return MiningSettings.model_validate({**settings.model_dump(), **update})


def _cases(df: pd.DataFrame) -> list[str]:
    return sorted(df["case_id"].unique())


# --- waiting time before each activity -----------------------------------


def test_activity_waits_match_answer_key(view: pd.DataFrame) -> None:
    summary = compute_activity_waits(view)
    c, b = summary.activities
    assert (c.rank, c.activity, b.rank, b.activity) == (1, "C", 2, "B")
    assert (b.occurrences, b.case_count) == (6, 5)
    assert b.case_share == pytest.approx(5 / 6)
    assert (b.median_wait_seconds, b.mean_wait_seconds) == (1.5 * H, 2 * H)
    assert b.p90_wait_seconds == pytest.approx(3.5 * H)
    assert b.impact_score == pytest.approx(1.25 * H)
    assert (b.top_predecessor, b.top_predecessor_share) == ("A", pytest.approx(5 / 6))
    assert (c.occurrences, c.median_wait_seconds) == (5, 2 * H)
    assert c.mean_wait_seconds == pytest.approx(3.2 * H)
    assert c.p90_wait_seconds == pytest.approx(6.8 * H)
    assert (c.top_predecessor, c.top_predecessor_share) == ("B", 0.8)
    assert summary.no_predecessor_activities == ["A"]


def test_activity_wait_evidence_links_incoming_bottlenecks(
    mining_log: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    store = mine_segment(mining_log, "<all>", mining_settings, discover_model=False).evidence
    assert [i.id for i in store.query(type="activity")] == ["AW-01-all", "AW-02-all"]
    aw_c = store.get("AW-01-all")
    # Bottleneck ranks: A->C BN-01, B->C BN-02, A->B BN-03, B->B BN-04.
    assert aw_c.refs == ["activity_waits.json", "KPI-01-all", "BN-01-all", "BN-02-all"]
    assert store.get("AW-02-all").refs[2:] == ["BN-03-all", "BN-04-all"]
    assert "median 2.0 h before 'C'" in aw_c.summary


def test_activity_wait_cap_applies_to_evidence_only(
    mining_log: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    capped = _with(
        mining_settings,
        analysis={**mining_settings.analysis.model_dump(), "top_n_activity_waits": 1},
    )
    result = mine_segment(mining_log, "<all>", capped, discover_model=False)
    assert [i.id for i in result.evidence.query(type="activity")] == ["AW-01-all"]
    assert len(result.activity_waits.activities) == 2


# --- variant filter ---------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "value", "cases", "variants_kept"),
    [
        ("top_k", 1, ["c1", "c2", "c5"], 1),
        ("coverage", 0.6, ["c1", "c2", "c5", "c6"], 2),
        ("coverage", 0.5, ["c1", "c2", "c5"], 1),
        ("min_case_share", 0.2, ["c1", "c2", "c5"], 1),
        ("min_case_share", 0.1, ["c1", "c2", "c3", "c4", "c5", "c6"], 4),
    ],
)
def test_variant_filter_modes(
    view: pd.DataFrame, mode: str, value: float, cases: list[str], variants_kept: int
) -> None:
    out, report = filter_variants(view, VariantFilterSettings(mode=mode, value=value))
    assert _cases(out) == cases
    assert (report.case_count_before, report.case_count_after) == (6, len(cases))
    assert (report.variant_count_before, report.variant_count_after) == (4, variants_kept)
    assert report.kept_case_share == pytest.approx(len(cases) / 6)


@pytest.mark.parametrize(
    "bad",
    [
        {"mode": "top_k"},
        {"mode": "top_k", "value": 1.5},
        {"mode": "coverage", "value": 1.2},
        {"mode": "min_case_share", "value": 0},
        {"mode": "none", "value": 3},
    ],
)
def test_variant_filter_rejects_bad_settings(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        VariantFilterSettings.model_validate(bad)


def test_filter_for_discovery_leaves_every_other_number_alone(
    mining_log: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    plain = mine_segment(mining_log, "<all>", mining_settings, discover_model=False).evidence
    filtered_settings = _with(mining_settings, variant_filter={"mode": "top_k", "value": 1})
    filtered = mine_segment(mining_log, "<all>", filtered_settings).evidence

    assert [i.metrics for i in filtered.items if i.type != "kpi"] == [
        i.metrics for i in plain.items if i.type != "kpi"
    ]
    kpi = filtered.get("KPI-01-all")
    assert kpi.metrics["case_count"] == 6
    assert kpi.metrics["variant_filter"]["case_count_after"] == 3
    assert "the as-is model only" in kpi.summary


def test_filter_for_all_restricts_every_analysis(
    mining_log: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    settings = _with(
        mining_settings, variant_filter={"mode": "top_k", "value": 1, "apply_to": "all"}
    )
    store = mine_segment(mining_log, "<all>", settings, discover_model=False).evidence
    kpi = store.get("KPI-01-all").metrics
    # c1, c2, c5 keep 9 mining events; c5's batch repeat makes 10 raw events.
    assert (kpi["case_count"], kpi["event_count"]) == (3, 9)
    assert (kpi["raw_event_count"], kpi["collapsed_event_count"]) == (10, 1)
    assert store.query(type="rework") == []
    assert "every number in this segment" in store.get("KPI-01-all").summary


def test_no_filter_note_when_filter_is_off(
    mining_log: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    store = mine_segment(mining_log, "<all>", mining_settings, discover_model=False).evidence
    kpi = store.get("KPI-01-all")
    assert kpi.metrics["variant_filter"] is None
    assert "variant filter" not in kpi.summary


# --- conformance against a reference model ---------------------------------


def test_reference_replay_matches_answer_key(view: pd.DataFrame, abc_model) -> None:  # noqa: ANN001
    ref = check_reference_model(view, abc_model, "abc_reference.bpmn", ["C"], 10, 5)
    assert (ref.evaluated_cases, ref.conforming_cases) == (6, 3)
    assert ref.conforming_share == 0.5
    assert (ref.completed_evaluated_cases, ref.completed_conforming_cases) == (5, 3)
    assert ref.replay_fitness == pytest.approx(20 / 23)
    assert ref.cases_unfit_without_flagged_activity == 1  # c6 stops after B
    assert [(d.rank, d.activity, d.kind, d.example_case_ids) for d in ref.deviations] == [
        (1, "B", "executed_when_not_allowed", ["c3"]),
        (2, "C", "executed_when_not_allowed", ["c4"]),
    ]
    assert ref.example_case_ids == ["c3", "c4", "c6"]
    assert (ref.unmodelled_activities, ref.unobserved_model_activities) == ([], [])


def test_reference_fitness_agrees_with_pm4py_per_case_replay(
    view: pd.DataFrame,
    abc_model,  # noqa: ANN001
) -> None:
    import pm4py
    from pm4py.objects.log.obj import Event, EventLog, Trace

    per_case = EventLog(
        [
            Trace([Event({"concept:name": a}) for a in g["activity"]])
            for _, g in view.groupby("case_id", sort=True)
        ]
    )
    net, im, fm = pm4py.convert_to_petri_net(abc_model)
    expected = pm4py.fitness_token_based_replay(per_case, net, im, fm)["log_fitness"]
    ref = check_reference_model(view, abc_model, "abc", ["C"], 10, 5)
    assert ref.replay_fitness == pytest.approx(expected)


def test_activity_outside_the_model_makes_a_case_non_conforming(abc_model) -> None:  # noqa: ANN001
    # pm4py's replay skips X and calls x2 fitting; it must not conform here.
    rows = [("x1", a, h) for h, a in enumerate("ABC")]
    rows += [("x2", a, h) for h, a in enumerate("ABXC")]
    df = pd.DataFrame(rows, columns=["case_id", "activity", "h"])
    df["timestamp"] = pd.Timestamp(2019, 1, 1) + pd.to_timedelta(df.pop("h"), unit="h")
    ref = check_reference_model(df, abc_model, "abc", [], 10, 5)
    assert (ref.conforming_cases, ref.cases_with_unmodelled_activities) == (1, 1)
    assert ref.unmodelled_activities == ["X"]
    (dev,) = ref.deviations
    assert (dev.activity, dev.kind, dev.case_count) == ("X", "not_in_model", 1)
    assert ref.completed_evaluated_cases is None


def test_model_activity_never_observed_is_reported(abc_model) -> None:  # noqa: ANN001
    df = pd.DataFrame(
        {
            "case_id": ["y1", "y1"],
            "activity": ["A", "B"],
            "timestamp": [pd.Timestamp(2019, 1, 1), pd.Timestamp(2019, 1, 2)],
        }
    )
    ref = check_reference_model(df, abc_model, "abc", [], 10, 5)
    assert ref.unobserved_model_activities == ["C"]
    assert ref.conforming_cases == 0


def test_reference_evidence_end_to_end(
    mining_log: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    settings = _with(mining_settings, reference_models={"default": REFERENCE})
    model = load_reference_model(settings, "<all>")
    store = mine_segment(
        mining_log, "<all>", settings, discover_model=False, reference_model=model
    ).evidence
    cm = [i.id for i in store.items if i.id.startswith("CM-")]
    assert cm == ["CM-00-all", "CM-01-all", "CM-02-all"]
    overall = store.get("CM-00-all")
    assert "3 of 6 cases (50.0%) conform" in overall.summary
    assert "3 of 5 (60.0%)" in overall.summary
    assert "1 non-conforming cases only stop before the model's end" in overall.summary
    assert store.get("CM-01-all").refs == ["CM-00-all"]


def test_missing_reference_model_is_itself_evidence(
    mining_log: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    store = mine_segment(mining_log, "<all>", mining_settings, discover_model=False).evidence
    item = store.get("CM-00-all")
    assert item.metrics == {
        "available": False,
        "skipped_reason": "no reference model is configured for this segment",
    }
    assert "NOT evidence that the segment follows its intended process" in item.summary


def test_configured_but_unloaded_reference_model_fails_loudly(
    mining_log: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    settings = _with(mining_settings, reference_models={"default": REFERENCE})
    with pytest.raises(ValueError, match="load_reference_model"):
        mine_segment(mining_log, "<all>", settings, discover_model=False)


def test_reference_model_per_segment_override(
    mining_settings: MiningSettings, tmp_path: Path
) -> None:
    settings = _with(
        mining_settings,
        reference_models={"default": REFERENCE, "per_segment": {"other": None}},
    )
    assert load_reference_model(settings, "other") is None
    assert load_reference_model(settings, "<all>") is not None
    with pytest.raises(FileNotFoundError, match="reference_models"):
        load_reference_model(settings, "<all>", root=tmp_path)
