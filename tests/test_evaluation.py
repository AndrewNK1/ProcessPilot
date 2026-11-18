"""Evaluation Phase 4: SQLite migration, answer key, campaign manifest (no LLM).

The answer-key tests use a hand-built evidence store whose expected key is
worked out in each test; the campaign tests build a fake frozen run folder, so
nothing here reads BPI 2019.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from pathlib import Path

import pytest

from processpilot.api.db import SCHEMA_VERSION, Database
from processpilot.evaluation.answer_key import (
    AnswerKeySettings,
    build_answer_key,
    segment_key,
    sha256_file,
    write_answer_key,
)
from processpilot.evaluation.campaign import (
    MINING_RECORD,
    RUN_FILES,
    SEGMENT_FILES,
    CampaignManifest,
    CampaignSettings,
    Environment,
    GitState,
    check_environment,
    check_frozen,
    check_unchanged,
    draft_campaign,
    environment_problems,
    frozen_run_record,
    preset_sha256,
    seal_campaign,
)
from processpilot.mining.evidence import EVIDENCE_SCHEMA_VERSION, EvidenceItem, EvidenceStore

SEG = "Seg A"
SETTINGS = AnswerKeySettings(top_n_bottlenecks=3, conformance_completed_share_gt=0.01)

# --- SQLite migration -------------------------------------------------------------

_V1_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE datasets (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
    preset TEXT NOT NULL, source_path TEXT NOT NULL, mapping_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE runs (run_id TEXT PRIMARY KEY, origin TEXT NOT NULL, status TEXT NOT NULL,
    dataset_id TEXT, preset TEXT, cleaning_json TEXT, evidence_count INTEGER,
    created_at TEXT NOT NULL);
CREATE TABLE agent_runs (run_id TEXT NOT NULL, segment TEXT NOT NULL,
    agent_run_id TEXT NOT NULL, model_id TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, segment, agent_run_id));
CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
    segment TEXT NOT NULL, agent_run_id TEXT NOT NULL, round INTEGER NOT NULL,
    decision TEXT NOT NULL, comment TEXT, analyst TEXT, proposal_titles_json TEXT NOT NULL,
    decided_at TEXT NOT NULL);
CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
    target_json TEXT NOT NULL, message TEXT, error TEXT, result_json TEXT,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
INSERT INTO meta VALUES ('schema_version', '1');
INSERT INTO runs VALUES ('r1', 'disk', 'mined', NULL, NULL, NULL, 10, 't');
INSERT INTO agent_runs VALUES ('r1', 'Seg A', 'a1', 'm', 'approved', 't', 't');
INSERT INTO decisions (run_id, segment, agent_run_id, round, decision, comment, analyst,
    proposal_titles_json, decided_at)
    VALUES ('r1', 'Seg A', 'a1', 1, 'approve', NULL, 'x', '[]', 't');
"""


def _v1_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(_V1_SCHEMA)
    conn.close()
    return path


def test_v1_database_is_upgraded_in_place_with_a_backup(tmp_path: Path) -> None:
    path = _v1_db(tmp_path / "pp.sqlite")
    db = Database(path)
    assert db.backup_path == tmp_path / "pp.sqlite.bak-v1" and db.backup_path.is_file()
    run = db.run("r1")
    assert run["purpose"] == "dev" and run["campaign_id"] is None and run["evidence_count"] == 10
    (agent,) = db.agent_runs("r1")
    assert (agent["purpose"], agent["launched_by"], agent["condition"]) == (
        "dev",
        "app",
        "processpilot",
    )
    assert len(db.decisions()) == 1  # the audit trail survives
    # The backup is the untouched v1 file.
    old = sqlite3.connect(db.backup_path)
    assert old.execute("SELECT value FROM meta").fetchone() == ("1",)
    old.close()
    # Reopening does nothing more.
    again = Database(path)
    assert again.backup_path is None


def test_new_and_upgraded_databases_have_the_same_tables(tmp_path: Path) -> None:
    def shape(path: Path) -> dict[str, list[str]]:
        conn = sqlite3.connect(path)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        out = {
            t: [c[1] for c in conn.execute(f"PRAGMA table_info({t})")]
            for t in sorted(tables)
            if t != "sqlite_sequence"
        }
        version = conn.execute("SELECT value FROM meta").fetchone()[0]
        conn.close()
        return {**out, "version": [version]}

    Database(tmp_path / "new.sqlite")
    Database(_v1_db(tmp_path / "old.sqlite"))
    new, old = shape(tmp_path / "new.sqlite"), shape(tmp_path / "old.sqlite")
    assert new == old and new["version"] == [str(SCHEMA_VERSION)]
    assert {"eval_campaigns", "eval_runs"} <= set(new)


def test_purpose_is_constrained_and_newer_schema_refused(tmp_path: Path) -> None:
    db = Database(tmp_path / "pp.sqlite")
    db.add_run("r1", "disk", "mined", purpose="evaluation", campaign_id="c")
    assert db.run("r1")["purpose"] == "evaluation"
    with pytest.raises(sqlite3.IntegrityError):
        db.add_run("r2", "disk", "mined", purpose="production")
    conn = sqlite3.connect(tmp_path / "pp.sqlite")
    conn.execute("UPDATE meta SET value = '99'")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer"):
        Database(tmp_path / "pp.sqlite")


# --- answer key ---------------------------------------------------------------------


def _bn(rank: int, source: str, target: str) -> EvidenceItem:
    return EvidenceItem(
        id=f"BN-{rank:02d}-seg-a",
        segment=SEG,
        type="bottleneck",
        summary=f"bottleneck {rank}",
        metrics={
            "rank": rank,
            "source": source,
            "target": target,
            "median_wait_seconds": 86400.0 * rank,
            "case_share": 0.5 / rank,
        },
    )


def _cf(rule_id: str, share: float | None, params: dict, rtype: str) -> EvidenceItem:
    return EvidenceItem(
        id=f"CF-{rule_id}-seg-a",
        segment=SEG,
        type="conformance",
        summary=f"rule {rule_id}",
        metrics={
            "rule_id": rule_id,
            "rule_name": rule_id.lower(),
            "rule_type": rtype,
            "params": params,
            "completed_violation_share": share,
            "completed_violation_count": None if share is None else 7,
            "completed_evaluated_cases": None if share is None else 100,
            "violation_count": 9,
            "evaluated_cases": 200,
            "violation_share": 0.045,
            "activities_missing_from_segment": [],
        },
    )


def _store(rules: list[EvidenceItem]) -> EvidenceStore:
    items = [
        _bn(1, "A", "B"),
        _bn(2, "B", "C"),
        _bn(4, "D", "E"),  # ranks need not be contiguous in store order
        _bn(3, "C", "D"),
        EvidenceItem(
            id="AW-01-seg-a", segment=SEG, type="activity", summary="w", metrics={"activity": "B"}
        ),
        EvidenceItem(
            id="AW-02-seg-a", segment=SEG, type="activity", summary="w", metrics={"activity": "Inv"}
        ),
        EvidenceItem(id="HO-00-seg-a", segment=SEG, type="resource", summary="h", metrics={}),
        EvidenceItem(
            id="HO-01-seg-a",
            segment=SEG,
            type="resource",
            summary="h",
            metrics={"source": "A", "target": "B"},
        ),
        EvidenceItem(
            id="ST-01-seg-a",
            segment=SEG,
            type="resource",
            summary="s",
            metrics={"source": "A", "target": "B"},
        ),
        EvidenceItem(
            id="ST-02-seg-a",
            segment=SEG,
            type="resource",
            summary="s",
            metrics={"source": "B", "target": "A"},
        ),
        EvidenceItem(
            id="CM-00-seg-a",
            segment=SEG,
            type="conformance",
            summary="not performed",
            metrics={"performed": False},
        ),
        *rules,
    ]
    return EvidenceStore(items=items, evidence_schema_version=EVIDENCE_SCHEMA_VERSION)


MUST = "activity_must_occur"
ORDER = "activity_a_before_activity_b"


def test_key_takes_top_bottlenecks_with_related_ids() -> None:
    key = segment_key(_store([]), SEG, SETTINGS)
    assert [b.evidence_id for b in key.bottlenecks] == ["BN-01-seg-a", "BN-02-seg-a", "BN-03-seg-a"]
    first = key.bottlenecks[0]
    assert (first.source, first.target, first.median_wait) == ("A", "B", "1.0 days")
    assert first.activities == ["A", "B"]
    # AW of the target, HO/ST on the same edge (not the reverse edge, not HO-00).
    assert first.related_evidence_ids == ["AW-01-seg-a", "HO-01-seg-a", "ST-01-seg-a"]
    assert key.bottlenecks[1].related_evidence_ids == []  # B -> C: no AW of C, no edge items
    assert key.key_contents == "bottlenecks only (no conformance rule applies to this segment)"


def test_threshold_is_strict_and_every_rate_is_recorded() -> None:
    rules = [
        _cf("R-01", 0.01, {"a": "Inv", "b": "C"}, ORDER),  # exactly 1%: out
        _cf("R-02", 0.0101, {"activity": "Inv"}, MUST),  # just above: in
        _cf("R-03", None, {"activity": "X"}, MUST),  # no completion definition: out
    ]
    key = segment_key(_store(rules), SEG, SETTINGS)
    assert [c.evidence_id for c in key.conformance_items] == ["CF-R-02-seg-a"]
    assert key.conformance_items[0].activities == ["Inv"]
    assert key.conformance_items[0].related_evidence_ids == ["AW-02-seg-a"]
    assert key.key_contents == "bottlenecks and conformance rules"
    rates = {r.rule_id: r for r in key.conformance_rates}
    assert set(rates) == {"R-01", "R-02", "R-03"}  # CM items are not rules
    assert not rates["R-01"].in_key and "at or below 1%" in rates["R-01"].reason
    assert rates["R-02"].in_key and rates["R-02"].completed_violation_share == 0.0101
    assert not rates["R-03"].in_key and "no case-completion" in rates["R-03"].reason


def test_no_rule_above_threshold_means_bottlenecks_only_explicitly() -> None:
    key = segment_key(_store([_cf("R-04", 0.003, {"activity": "Inv"}, MUST)]), SEG, SETTINGS)
    assert key.conformance_items == []
    assert key.key_contents == "bottlenecks only (no conformance rule above 1% of completed cases)"
    assert key.conformance_rates[0].completed_violation_share == 0.003


def test_fewer_bottlenecks_than_n_is_noted() -> None:
    store = EvidenceStore(items=[_bn(1, "A", "B")], evidence_schema_version=EVIDENCE_SCHEMA_VERSION)
    key = segment_key(store, SEG, SETTINGS)
    assert len(key.bottlenecks) == 1 and "only 1 bottleneck" in key.notes[0]


def test_key_is_written_once_read_only(tmp_path: Path) -> None:
    key = build_answer_key(
        _store([_cf("R-02", 0.05, {"activity": "Inv"}, MUST)]),
        [SEG],
        SETTINGS,
        campaign_id="c1",
        mining_run_id="r1",
        evidence_sha256="e" * 64,
        created_at="t",
    )
    json_path, csv_path, digest = write_answer_key(key, tmp_path / "c1")
    assert digest == sha256_file(json_path)
    assert not os.access(json_path, os.W_OK) and not os.access(csv_path, os.W_OK)
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["rule"]["conformance_completed_share_gt"] == 0.01
    assert saved["matching"]["strict"].startswith("the diagnosis cites")
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert [r["evidence_id"] for r in rows] == [
        "BN-01-seg-a",
        "BN-02-seg-a",
        "BN-03-seg-a",
        "CF-R-02-seg-a",
    ]
    assert rows[0]["activities"] == "A | B"
    with pytest.raises(FileExistsError, match="written once"):
        write_answer_key(key, tmp_path / "c1")


def test_eval_yaml_holds_the_methodology_rule() -> None:
    assert AnswerKeySettings.load() == SETTINGS
    s = CampaignSettings.load()
    assert (s.preset, s.repetitions, s.conditions) == ("bpi2019", 5, ["processpilot", "baseline"])


# --- campaign manifest ---------------------------------------------------------------

PRESET = {"name": "p", "redesign": {"protected_activities": ["X"]}}


def _frozen_run(root: Path) -> Path:
    source = root / "data" / "log.csv"
    source.parent.mkdir(parents=True)
    source.write_text("case,activity\n1,A\n", encoding="utf-8")
    run_dir = root / "outputs" / "runs" / "r1"
    (run_dir / "seg-a").mkdir(parents=True)
    for name in RUN_FILES:
        (run_dir / name).write_text("{}", encoding="utf-8")
    for name in SEGMENT_FILES:
        (run_dir / "seg-a" / name).write_text(name, encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "segments": {SEG: "seg-a"},
                "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
                "source": {"path": str(source), "size_bytes": source.stat().st_size, "preset": "p"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / MINING_RECORD).write_text(
        json.dumps(
            {
                "preset": "p",
                "preset_sha256": preset_sha256(PRESET),
                "git_commit": "abc",
                "cleaning_policy": {"drop_cases_with_out_of_range_timestamps": True},
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def _env(**overrides: object) -> Environment:
    base = {
        "git_commit": "def",
        "prompt_version": "p5-v1",
        "model_id": "google_genai:gemini-3.6-flash",
        "agent_settings": {"max_retries": 2},
        "model_kwargs": {"thinking_level": "medium"},
        "model_notes": [],
        "heuristics_catalogue_version": 2,
        "domain_knowledge": {"domain": "p2p", "version": 1},
        "library_versions": {"pm4py": "2.7.23.11"},
        "hardware": {"cpu": "x"},
        "rate_limit": {"min_seconds_between_calls": 20},
        "baseline": {"functions": []},
    }
    return Environment.model_validate({**base, **overrides})


@pytest.fixture
def campaign(tmp_path: Path) -> tuple[Path, Path, CampaignManifest]:
    run_dir = _frozen_run(tmp_path)
    campaign_dir = tmp_path / "outputs" / "eval" / "c1"
    campaign_dir.mkdir(parents=True)
    key = campaign_dir / "answer_key.json"
    key.write_text('{"k": 1}', encoding="utf-8")
    frozen = frozen_run_record(tmp_path, run_dir, "p", PRESET)
    settings = CampaignSettings(preset="p", repetitions=5, conditions=["processpilot", "baseline"])
    manifest = draft_campaign("c1", frozen, sha256_file(key), settings)
    manifest.write(campaign_dir)
    return tmp_path, campaign_dir, manifest


def test_draft_records_frozen_files_source_and_design(campaign) -> None:
    root, campaign_dir, manifest = campaign
    f = manifest.frozen
    assert manifest.status == "draft" and manifest.environment is None
    assert f.run_dir == "outputs/runs/r1" and f.mining_git_commit == "abc"
    assert set(f.files) == {*RUN_FILES, *(f"seg-a/{n}" for n in SEGMENT_FILES)}
    assert f.source.size_bytes > 0 and len(f.source.sha256) == 64
    assert manifest.design.segments == [SEG] and manifest.design.repetitions == 5
    assert CampaignManifest.read(campaign_dir) == manifest
    assert check_frozen(manifest, root, campaign_dir, PRESET) == []
    assert check_unchanged(manifest, root, campaign_dir, PRESET, _env()) == [
        "campaign c1 is draft, not sealed"
    ]


def test_draft_refuses_a_run_of_another_preset_or_a_changed_preset(tmp_path: Path) -> None:
    run_dir = _frozen_run(tmp_path)
    with pytest.raises(ValueError, match="not 'bpi2019'"):
        frozen_run_record(tmp_path, run_dir, "bpi2019", PRESET)
    with pytest.raises(ValueError, match="changed since"):
        frozen_run_record(tmp_path, run_dir, "p", {**PRESET, "extra": 1})


@pytest.mark.parametrize(
    ("target", "message"),
    [
        (
            "outputs/runs/r1/seg-a/evidence.json",
            "frozen file changed: outputs/runs/r1/seg-a/evidence.json",
        ),
        ("outputs/eval/c1/answer_key.json", "answer key changed"),
        ("data/log.csv", "source log changed"),
    ],
)
def test_check_frozen_detects_every_change(campaign, target: str, message: str) -> None:
    root, campaign_dir, manifest = campaign
    path = root / target
    path.write_text(path.read_text(encoding="utf-8") + "x", encoding="utf-8")
    assert any(message in p for p in check_frozen(manifest, root, campaign_dir, PRESET))


def test_check_frozen_detects_a_changed_preset_and_missing_files(campaign) -> None:
    root, campaign_dir, manifest = campaign
    assert check_frozen(manifest, root, campaign_dir, {**PRESET, "x": 1}) == [
        "preset 'p' changed (content hash differs)"
    ]
    (root / "outputs/runs/r1/seg-a/dfg.json").unlink()
    assert "frozen file missing: outputs/runs/r1/seg-a/dfg.json" in check_frozen(
        manifest, root, campaign_dir, PRESET
    )


def test_seal_records_the_environment(campaign) -> None:
    root, campaign_dir, manifest = campaign
    sealed = seal_campaign(
        manifest, root, campaign_dir, PRESET, _env(), GitState(commit="def", dirty=[]), "eval-v1"
    )
    assert sealed.status == "sealed" and sealed.git_tag == "eval-v1"
    assert sealed.environment.model_kwargs == {"thinking_level": "medium"}
    assert check_unchanged(sealed, root, campaign_dir, PRESET, _env()) == []
    changed = check_unchanged(sealed, root, campaign_dir, PRESET, _env(prompt_version="p6-v1"))
    assert changed == ["prompt_version changed since sealing: 'p5-v1' -> 'p6-v1'"]
    assert check_environment(sealed, _env(agent_settings={"max_retries": 3})) == [
        "agent_settings changed since sealing"
    ]
    with pytest.raises(RuntimeError, match="already sealed"):
        seal_campaign(
            sealed, root, campaign_dir, PRESET, _env(), GitState(commit="d", dirty=[]), "t"
        )


def test_seal_refuses_dirty_tree_changed_files_and_missing_settings(campaign) -> None:
    root, campaign_dir, manifest = campaign
    clean = GitState(commit="def", dirty=[])
    with pytest.raises(RuntimeError, match="uncommitted"):
        seal_campaign(
            manifest,
            root,
            campaign_dir,
            PRESET,
            _env(),
            GitState(commit="d", dirty=["src/x.py"]),
            "t",
        )
    with pytest.raises(RuntimeError, match="rate_limit"):
        seal_campaign(manifest, root, campaign_dir, PRESET, _env(rate_limit=None), clean, "t")
    with pytest.raises(RuntimeError, match="baseline"):
        seal_campaign(manifest, root, campaign_dir, PRESET, _env(baseline=None), clean, "t")
    with pytest.raises(RuntimeError, match="thinking_level"):
        seal_campaign(manifest, root, campaign_dir, PRESET, _env(model_kwargs={}), clean, "t")
    key = campaign_dir / "answer_key.json"
    key.write_text('{"k": 2}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="answer key changed"):
        seal_campaign(manifest, root, campaign_dir, PRESET, _env(), clean, "t")


def test_environment_problems_only_require_thinking_level_for_gemini() -> None:
    assert environment_problems(_env(model_id="anthropic:claude-opus-5-5", model_kwargs={})) == []
