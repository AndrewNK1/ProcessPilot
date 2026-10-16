"""Evidence store, deterministic ids, end-to-end segment mining and run output."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from processpilot.mining.evidence import EvidenceStore, format_duration, segment_slug
from processpilot.mining.run import mine_segment, new_run_id, write_run
from processpilot.mining.settings import MiningSettings


@pytest.fixture
def store(mining_log: pd.DataFrame, mining_settings: MiningSettings) -> EvidenceStore:
    return mine_segment(mining_log, "<all>", mining_settings, discover_model=False).evidence


def test_segment_slug_is_filesystem_and_id_safe() -> None:
    assert segment_slug("3-way match, invoice before GR") == "3-way-match-invoice-before-gr"
    assert segment_slug("<all>") == "all"
    with pytest.raises(ValueError):
        segment_slug("!!!")


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(None, "n/a"), (30, "30 s"), (600, "10 min"), (5400, "1.5 h"), (3 * 86400, "3.0 days")],
)
def test_format_duration(seconds: float | None, text: str) -> None:
    assert format_duration(seconds) == text


def test_evidence_ids_follow_the_scheme(store: EvidenceStore) -> None:
    assert store.ids() == [
        "KPI-01-all",
        "BN-01-all",
        "BN-02-all",
        "BN-03-all",
        "BN-04-all",
        "VA-01-all",
        "VA-02-all",
        "VA-03-all",
        "VA-04-all",
        "RW-01-all",
        "HO-00-all",
        "RS-01-all",
        "RS-02-all",
        "RS-03-all",
        "HO-01-all",
        "HO-02-all",
        "HO-03-all",
        "CF-R-1-all",
        "CF-R-2-all",
        "CF-R-3-all",
        "CF-R-4-all",
    ]


def test_evidence_is_deterministic_across_runs(
    mining_log: pd.DataFrame, mining_settings: MiningSettings, store: EvidenceStore
) -> None:
    again = mine_segment(mining_log, "<all>", mining_settings, discover_model=False).evidence
    assert again.model_dump_json() == store.model_dump_json()


def test_evidence_independent_of_input_row_order(
    mining_log: pd.DataFrame, mining_settings: MiningSettings, store: EvidenceStore
) -> None:
    from processpilot.schema import sort_canonical

    shuffled = sort_canonical(mining_log.sample(frac=1.0, random_state=7))
    again = mine_segment(shuffled, "<all>", mining_settings, discover_model=False).evidence
    assert again.model_dump_json() == store.model_dump_json()


def test_summaries_restate_metrics(store: EvidenceStore) -> None:
    top = store.get("BN-01-all")
    assert "'A' -> 'C'" in top.summary
    assert "16.7% of cases" in top.summary
    assert "10.0 h" in top.summary
    assert "happy path" in store.get("VA-01-all").summary
    assert "association, not proven cause" in store.get("RW-01-all").summary


def test_cross_references_point_at_existing_items(store: EvidenceStore) -> None:
    assert store.get("RW-01-all").refs == ["KPI-01-all", "BN-04-all"]  # B->B self-loop
    assert store.get("HO-01-all").refs == ["BN-01-all"]  # A->C
    for item in store.items:
        assert store.unknown_ids([r for r in item.refs if not r.endswith(".json")]) == []


def test_unknown_ids_detects_fabricated_citations(store: EvidenceStore) -> None:
    assert store.unknown_ids(["BN-01-all", "BN-99-all", "E-014"]) == ["BN-99-all", "E-014"]


def test_get_raises_on_unknown_id(store: EvidenceStore) -> None:
    with pytest.raises(KeyError):
        store.get("BN-99-all")


def test_query_filters_by_type_and_segment(store: EvidenceStore) -> None:
    assert [i.id for i in store.query(type="rework")] == ["RW-01-all"]
    assert store.query(segment="other") == []
    assert len(store.query(segment="<all>")) == len(store.items)


def test_skipped_resource_analysis_is_itself_evidence(
    mining_log: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    no_res = mining_log.drop(columns=["resource"])
    evidence = mine_segment(no_res, "<all>", mining_settings, discover_model=False).evidence
    (item,) = evidence.query(type="resource")
    assert item.id == "RS-00-all"
    assert "NOT evidence that no resource problem exists" in item.summary


def test_merge_rejects_duplicate_ids(store: EvidenceStore) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        store.merged(store)


def test_json_round_trip(store: EvidenceStore, tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    store.to_json(path)
    assert EvidenceStore.from_json(path) == store


def test_write_run_layout_and_manifest(
    mining_log: pd.DataFrame, mining_settings: MiningSettings, tmp_path: Path
) -> None:
    segmented = mining_log.assign(
        segment=mining_log["case_id"].map(lambda c: "Group A" if c in ("c1", "c2", "c3") else "B")
    )
    results = [
        mine_segment(seg_df.reset_index(drop=True), name, mining_settings)
        for name, seg_df in segmented.groupby("segment", sort=True)
    ]
    run_dir = tmp_path / "runs" / new_run_id()
    merged = write_run(results, run_dir, mining_settings, {"type": "test"})

    for slug in ("group-a", "b"):
        for name in ("as_is.bpmn", "dfg.json", "dfg_view.json", "evidence.json"):
            assert (run_dir / slug / name).is_file(), f"{slug}/{name} missing"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["segments"] == {"B": "b", "Group A": "group-a"}
    assert manifest["library_versions"]["pm4py"] != "not installed"
    assert manifest["evidence_count"] == len(merged.items)
    assert EvidenceStore.from_json(run_dir / "evidence.json") == merged
    assert {i.segment for i in merged.items} == {"Group A", "B"}


def test_new_run_id_is_sortable_and_path_safe() -> None:
    from datetime import UTC, datetime

    assert new_run_id(datetime(2026, 10, 16, 14, 30, 5, tzinfo=UTC)) == "20261016T143005Z"


def test_rule_with_absent_activities_says_it_was_not_evaluated(
    mining_log: pd.DataFrame, mining_settings: MiningSettings
) -> None:
    from processpilot.mining.settings import ConformanceRule

    settings = mining_settings.model_copy(
        update={
            "conformance_rules": [
                ConformanceRule(
                    id="R-9",
                    name="y_before_z",
                    type="activity_a_before_activity_b",
                    description="d",
                    params={"a": "Y", "b": "Z"},
                )
            ]
        }
    )
    store = mine_segment(mining_log, "<all>", settings, discover_model=False).evidence
    summary = store.get("CF-R-9-all").summary
    assert "could not be evaluated on any case" in summary
    assert "likely misconfigured" in summary
