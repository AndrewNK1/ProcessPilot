"""Resource types (human / batch / unassigned) on a hand-built log.

Evidence schema 2: workload ranks people only, a handover is a change between
two different people, and steps with a batch job or no recorded user are
system/vendor transitions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from processpilot.ingest.mapping import ColumnMapping, apply_mapping
from processpilot.ingest.resource_types import (
    ResourceTypeSettings,
    classify_resources,
    resource_type_breakdown,
)
from processpilot.ingest.validate import apply_cleaning, validate
from processpilot.mining.evidence import (
    EVIDENCE_SCHEMA_VERSION,
    EvidenceItem,
    EvidenceStore,
)
from processpilot.mining.resources import analyse_resources
from processpilot.mining.run import mine_segment, write_run
from processpilot.mining.settings import MiningSettings

H = 3600.0
RULES = ResourceTypeSettings(human=r"^u\d+$", batch=r"^b\d+$", unassigned=["NONE"])
CAPS = {"top_n_system_transitions": 10, "top_n_type_activities": 5}


@pytest.fixture
def typed_log() -> pd.DataFrame:
    """5 cases, A -> B -> C, hours after each case's start; resource in brackets.

        c1  A(0)[u1]    B(1)[u2]  C(3)[b1]
        c2  A(0)[u1]    B(2)[u1]  C(4)[b1]
        c3  A(0)[NONE]  B(5)[u2]  C(6)[u2]
        c4  A(0)[u1]    B(1)[b1]  C(2)[b1]
        c5  A(0)[empty] B(4)[u1]  C(5)[u2]

    Answer key (15 events; u = human, b = batch, NONE/empty = unassigned):
        types: human 9, batch 4, unassigned 2 (NONE 1, empty 1)
        workload (people only): u1 5 events / 4 cases, u2 4 / 3; human share 5/9, 4/9
        steps: handover (person -> other person) c1 A->B 1h, c5 B->C 1h -> median 1h
               same person c2 A->B 2h, c3 B->C 1h                     -> median 1.5h
               system c1 B->C 2h, c2 B->C 2h, c3 A->B 5h, c4 A->B 1h,
                      c4 B->C 1h (batch -> same batch), c5 A->B 4h    -> median 2h
        HO edges: A->B and B->C, 1 handover each, share 1/5, system share 3/5,
                  impact 1h x 1/5 tie -> A->B first
        ST edges: A->B waits 5,1,4h median 4h, 3/5 cases, impact 2.4h,
                  kinds unassigned->human 2, human->batch 1
                  B->C waits 2,2,1h median 2h, 3/5 cases, impact 1.2h,
                  kinds human->batch 2, batch->batch 1
    """
    spec = {
        "c1": [("A", 0, "u1"), ("B", 1, "u2"), ("C", 3, "b1")],
        "c2": [("A", 0, "u1"), ("B", 2, "u1"), ("C", 4, "b1")],
        "c3": [("A", 0, "NONE"), ("B", 5, "u2"), ("C", 6, "u2")],
        "c4": [("A", 0, "u1"), ("B", 1, "b1"), ("C", 2, "b1")],
        "c5": [("A", 0, None), ("B", 4, "u1"), ("C", 5, "u2")],
    }
    rows = []
    for day, (case, events) in enumerate(spec.items(), start=1):
        start = pd.Timestamp(2019, 1, day)
        rows += [(case, act, start + pd.Timedelta(hours=h), res) for act, h, res in events]
    raw = pd.DataFrame(rows, columns=["case", "activity", "time", "user"])
    return apply_mapping(
        raw, ColumnMapping(case_id="case", activity="activity", timestamp="time", resource="user")
    )


def _settings(rules: ResourceTypeSettings | None = RULES) -> MiningSettings:
    analysis = {
        "top_n_bottlenecks": 10,
        "top_n_variants": 10,
        "top_n_rework": 10,
        "top_n_resources": 10,
        "top_n_handovers": 10,
        "top_n_system_transitions": 10,
        "top_n_resource_type_activities": 5,
        "top_n_activity_waits": 10,
        "top_n_model_deviations": 10,
    }
    return MiningSettings.model_validate(
        {
            "discovery": {
                "inductive_miner_noise_threshold": 0.0,
                "dfg_min_edge_frequency_share": 0,
            },
            "analysis": analysis,
            "resource_types": (rules or ResourceTypeSettings()).model_dump(),
        }
    )


def _items(
    log: pd.DataFrame, rules: ResourceTypeSettings | None = RULES
) -> dict[str, EvidenceItem]:
    store = mine_segment(log, "seg", _settings(rules), discover_model=False).evidence
    return {i.id: i for i in store.items if i.type == "resource"}


# --- classifier ------------------------------------------------------------------


def test_classifier_types_and_basis() -> None:
    values = pd.Series(["u1", "b7", "NONE", None, "", "  ", "x9"])
    types, basis = classify_resources(values, RULES)
    assert types.tolist() == [
        "human", "batch", "unassigned", "unassigned", "unassigned", "unassigned", "human"
    ]  # fmt: skip
    assert basis.tolist() == ["pattern", "pattern", "values", "empty", "empty", "empty", "default"]


def test_regex_must_match_the_whole_value() -> None:
    types, _ = classify_resources(
        pd.Series(["u1x", "xb1"]), RULES.model_copy(update={"default_type": "batch"})
    )
    assert types.tolist() == ["batch", "batch"]  # neither is a full match -> default


def test_value_claimed_by_two_types_fails_loudly() -> None:
    clash = ResourceTypeSettings(human=r"^u\d+$", batch=["u1"])
    with pytest.raises(ValueError, match="'u1' matches human, batch"):
        classify_resources(pd.Series(["u1", "u2"]), clash)


def test_bad_regex_and_unknown_keys_fail_at_load() -> None:
    with pytest.raises(ValueError):
        ResourceTypeSettings(human="([")
    with pytest.raises(ValueError):
        ResourceTypeSettings.model_validate({"robot": "^r"})


def test_no_rules_means_every_recorded_value_is_human() -> None:
    settings = ResourceTypeSettings.from_preset({})
    assert not settings.configured
    types, _ = classify_resources(pd.Series(["batch_01", "NONE", None]), settings)
    assert types.tolist() == ["human", "human", "unassigned"]
    assert "treated as human" in settings.describe()


# --- data-quality report -----------------------------------------------------------


def test_quality_report_has_the_breakdown(typed_log: pd.DataFrame) -> None:
    report = validate(typed_log, resource_types=RULES)
    bd = report.resource_types
    assert bd is not None and bd.configured
    by_type = {t.type: t for t in bd.types}
    assert [t.type for t in bd.types] == ["human", "batch", "unassigned"]
    assert (by_type["human"].event_count, by_type["batch"].event_count) == (9, 4)
    un = by_type["unassigned"]
    assert (un.event_count, un.case_count, un.distinct_resources) == (2, 2, 2)
    assert un.event_share == pytest.approx(2 / 15) and un.case_share == pytest.approx(2 / 5)
    assert un.events_by_basis == {"values": 1, "empty": 1}
    assert un.example_values == ["(empty)", "NONE"]
    assert by_type["human"].example_values == ["u1", "u2"]
    assert bd.defaulted_event_count == 0
    assert "resource_type_defaulted" not in {i.code for i in report.issues}


def test_unmatched_values_are_reported_as_defaulted(typed_log: pd.DataFrame) -> None:
    log = typed_log.copy()
    log.loc[0, "resource"] = "robot_1"
    report = validate(log, resource_types=RULES)
    assert report.resource_types.defaulted_event_count == 1
    assert report.resource_types.defaulted_values == ["robot_1"]
    issue = next(i for i in report.issues if i.code == "resource_type_defaulted")
    assert issue.severity == "info" and issue.count == 1 and "robot_1" in issue.message
    assert report.issues[-1].code == "log_size"  # the size note stays last


def test_breakdown_is_recomputed_after_cleaning(typed_log: pd.DataFrame) -> None:
    # c6: one event, no user recorded; dropping single-event cases removes it.
    extra = typed_log.iloc[[0]].assign(case_id="c6", resource="NONE")
    log = pd.concat([typed_log, extra], ignore_index=True)
    report = validate(log, resource_types=RULES)
    before = {t.type: t.event_count for t in report.resource_types.types}
    _, cleaned = apply_cleaning(log, report, drop_single_event_cases=True)
    after = {t.type: t.event_count for t in cleaned.resource_types.types}
    assert (before["unassigned"], after["unassigned"]) == (3, 2)
    assert cleaned.resource_types.event_count == 15


def test_no_resource_column_gives_no_breakdown(typed_log: pd.DataFrame) -> None:
    assert resource_type_breakdown(typed_log.drop(columns=["resource"]), RULES) is None


# --- mining --------------------------------------------------------------------------


def test_workload_ranks_people_only(typed_log: pd.DataFrame) -> None:
    res = analyse_resources(typed_log, typed_log, 10, 10, **CAPS, resource_types=RULES)
    assert [(w.resource, w.event_count, w.case_count) for w in res.workload] == [
        ("u1", 5, 4),
        ("u2", 4, 3),
    ]
    assert res.workload[0].event_share == pytest.approx(5 / 15)
    assert res.workload[0].human_event_share == pytest.approx(5 / 9)
    assert res.unassigned_event_count == 2


def test_type_summaries_list_top_activities(typed_log: pd.DataFrame) -> None:
    res = analyse_resources(typed_log, typed_log, 10, 10, **CAPS, resource_types=RULES)
    by_type = {t.type: t for t in res.type_summaries}
    assert [(a.activity, a.event_count) for a in by_type["human"].top_activities] == [
        ("B", 4),
        ("A", 3),
        ("C", 2),
    ]
    batch = by_type["batch"]
    assert (batch.event_count, batch.case_count, batch.distinct_resources) == (4, 3, 1)
    assert [(a.activity, a.event_count) for a in batch.top_activities] == [("C", 3), ("B", 1)]
    assert by_type["unassigned"].top_activities[0].share_of_type_events == 1.0


def test_handovers_only_between_two_people(typed_log: pd.DataFrame) -> None:
    res = analyse_resources(typed_log, typed_log, 10, 10, **CAPS, resource_types=RULES)
    assert (res.handover_transition_count, res.median_handover_wait_seconds) == (2, 1 * H)
    assert (res.same_resource_transition_count, res.median_same_resource_wait_seconds) == (
        2,
        1.5 * H,
    )
    # c4's batch -> same batch step is a system transition, not "same resource".
    assert (res.system_transition_count, res.median_system_transition_wait_seconds) == (6, 2 * H)
    ab, bc = res.handovers
    assert (ab.source, ab.target, bc.source, bc.target) == ("A", "B", "B", "C")
    assert ab.handover_share_of_transitions == pytest.approx(1 / 5)
    assert ab.system_transition_share_of_transitions == pytest.approx(3 / 5)
    assert ab.median_same_resource_wait_seconds == 2 * H
    assert bc.median_same_resource_wait_seconds == 1 * H


def test_system_transitions_ranked_with_kinds(typed_log: pd.DataFrame) -> None:
    res = analyse_resources(typed_log, typed_log, 10, 10, **CAPS, resource_types=RULES)
    ab, bc = res.system_transitions
    assert (ab.source, ab.target, ab.median_wait_seconds) == ("A", "B", 4 * H)
    assert ab.impact_score == pytest.approx(4 * H * 3 / 5)
    assert ab.kinds == {"unassigned -> human": 2, "human -> batch": 1}
    assert (ab.dominant_kind, ab.dominant_kind_share) == (
        "unassigned -> human",
        pytest.approx(2 / 3),
    )
    assert (bc.median_wait_seconds, bc.kinds) == (2 * H, {"human -> batch": 2, "batch -> batch": 1})
    assert ab.share_of_transitions == pytest.approx(3 / 5)


def test_evidence_ids_and_wording(typed_log: pd.DataFrame) -> None:
    items = _items(typed_log)
    assert sorted(items) == sorted(
        ["HO-00-seg", "RT-01-seg", "RT-02-seg", "RT-03-seg", "RS-01-seg", "RS-02-seg",
         "HO-01-seg", "HO-02-seg", "ST-01-seg", "ST-02-seg"]
    )  # fmt: skip
    assert "Batch jobs" in items["RT-02-seg"].summary
    assert "no user recorded" in items["RT-03-seg"].summary
    assert "never part of a handover" in items["RT-02-seg"].summary
    assert items["RS-01-seg"].summary.startswith("Person 'u1'")
    st = items["ST-01-seg"].summary
    assert "mostly no user recorded -> person (66.7%)" in st and "not a handover" in st
    ho0 = items["HO-00-seg"]
    assert ho0.metrics["system_transition_count"] == 6
    assert ho0.metrics["median_handover_wait_seconds"] == 1 * H
    assert "two different people" in ho0.summary
    assert set(ho0.refs) == {"RT-01-seg", "RT-02-seg", "RT-03-seg", "ST-01-seg", "ST-02-seg"}
    assert "assumed" not in ho0.summary


def test_rt_numbers_follow_the_type_not_the_rank(typed_log: pd.DataFrame) -> None:
    # Without batch events RT-02 is absent, and unassigned is still RT-03.
    log = typed_log.loc[typed_log["resource"] != "b1"].reset_index(drop=True)
    rt = sorted(i for i in _items(log) if i.startswith("RT-"))
    assert rt == ["RT-01-seg", "RT-03-seg"]


def test_unconfigured_types_are_labelled_as_assumed(typed_log: pd.DataFrame) -> None:
    items = _items(typed_log, rules=None)
    assert "assumed, not configured" in items["HO-00-seg"].summary
    assert "assumed, not configured" in items["RT-01-seg"].summary
    assert not items["HO-00-seg"].metrics["resource_types_configured"]
    # b1 and NONE count as people now; only the empty value is unassigned.
    assert {i for i in items if i.startswith("RT-")} == {"RT-01-seg", "RT-03-seg"}


# --- schema version ------------------------------------------------------------------


def test_schema_version_in_evidence_and_manifest(typed_log: pd.DataFrame, tmp_path: Path) -> None:
    settings = _settings()
    result = mine_segment(typed_log, "seg", settings, discover_model=False)
    store = write_run([result], tmp_path, settings)
    assert store.evidence_schema_version == EVIDENCE_SCHEMA_VERSION == 2
    on_disk = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    seg_file = json.loads((tmp_path / "seg" / "evidence.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["evidence_schema_version"] == seg_file["evidence_schema_version"] == 2
    assert manifest["evidence_schema_version"] == 2
    assert manifest["settings"]["resource_types"]["batch"] == r"^b\d+$"


def test_unversioned_file_reads_as_schema_1(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"items": []}), encoding="utf-8")
    store = EvidenceStore.from_json(path)
    assert store.evidence_schema_version is None and store.schema_version == 1


def test_stores_of_different_schemas_do_not_merge() -> None:
    item = EvidenceItem(id="KPI-01-a", segment="a", type="kpi", summary="s", metrics={})
    v1 = EvidenceStore(items=[item])
    v2 = EvidenceStore(
        items=[item.model_copy(update={"id": "KPI-01-b"})], evidence_schema_version=2
    )
    with pytest.raises(ValueError, match="schema versions"):
        v1.merged(v2)
