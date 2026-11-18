"""The evaluation steps the scripts in ``scripts/eval/`` run, as testable functions.

1. ``mine_frozen_run``: mine the evaluation log once, exactly as the app does
   (preset dataset -> validate -> the preset's suggested cleaning -> mine), and
   register the run as ``purpose='evaluation'`` with its preset.
2. ``build_key``: write the answer key for a campaign from that run only.
3. ``create_draft``: write the draft campaign manifest around run and key.
4. ``seal``: seal the draft (after Phases 5-6), re-checking every hash.

WHY the frozen run is not mined through the app's job queue: a second
``Workspace`` in another process would mark the running app's jobs as
interrupted. The functions called are the same ones the app's mining job
calls, with the same arguments.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from processpilot.api.db import Database
from processpilot.config import load_preset_yaml, resolve_source_path
from processpilot.evaluation.answer_key import (
    ANSWER_KEY_JSON,
    AnswerKeySettings,
    build_answer_key,
    sha256_file,
    write_answer_key,
)
from processpilot.evaluation.campaign import (
    MINING_RECORD,
    CampaignManifest,
    CampaignSettings,
    current_environment,
    draft_campaign,
    frozen_run_record,
    git_state,
    preset_sha256,
    require_clean,
    seal_campaign,
    utc_now,
)
from processpilot.ingest.mapping import load_preset
from processpilot.mining.evidence import EvidenceStore
from processpilot.mining.run import new_run_id
from processpilot.pipeline import (
    clean_log,
    load_canonical,
    mine_log,
    suggested_cleaning_policy,
    validate_log,
)
from processpilot.sources.file_source import FileSource

Progress = Callable[[str], None]


def mine_frozen_run(
    root: Path, preset_name: str, runs_dir: Path, db: Database, progress: Progress = print
) -> str:
    """Mine the evaluation run; returns its id. Refuses a dirty tree or a foreign preset."""
    expected = CampaignSettings.load().preset
    if preset_name != expected:
        raise ValueError(
            f"the evaluation is configured for preset {expected!r} (configs/eval.yaml), "
            f"not {preset_name!r}"
        )
    git = git_state(root)
    require_clean(git, "mine the frozen evaluation run")
    mapping, preset = load_preset(preset_name)
    source = FileSource(resolve_source_path(preset))
    progress(f"reading {source.describe().get('name')} with preset {preset_name}")
    canonical = load_canonical(source, mapping)
    report = validate_log(canonical, preset)
    policy = suggested_cleaning_policy(preset)
    progress(f"cleaning with the preset's suggested policy: {policy.model_dump()}")
    clean, quality = clean_log(canonical, report, policy, preset)
    run_id = new_run_id()
    if (runs_dir / run_id).exists() or db.run(run_id) is not None:
        raise RuntimeError(f"run id {run_id} is taken; retry in a second")
    db.add_run(
        run_id,
        "disk",
        "mining",
        preset=preset_name,
        cleaning=policy.model_dump(),
        purpose="evaluation",
    )
    try:
        store = mine_log(
            clean,
            preset,
            runs_dir / run_id,
            source.describe(),
            quality,
            progress,
            preset_name=preset_name,
        )
    except Exception:
        db.update_run(run_id, status="failed")
        raise
    (runs_dir / run_id / MINING_RECORD).write_text(
        json.dumps(
            {
                "purpose": "evaluation",
                "preset": preset_name,
                "preset_sha256": preset_sha256(load_preset_yaml(preset_name)),
                "cleaning_policy": policy.model_dump(),
                "git_commit": git.commit,
                "events_after_cleaning": int(len(clean)),
                "cases_after_cleaning": int(clean["case_id"].nunique()),
                "mined_at": utc_now(),
                "written_by": "scripts/eval/mine_frozen_run.py",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    db.update_run(run_id, status="mined", evidence_count=len(store.items))
    return run_id


def build_key(campaign_dir: Path, campaign_id: str, run_dir: Path) -> tuple[Path, str]:
    """Write the answer key from the frozen run's evidence; returns (path, SHA-256)."""
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    evidence = run_dir / "evidence.json"
    key = build_answer_key(
        EvidenceStore.from_json(evidence),
        list(manifest["segments"]),
        AnswerKeySettings.load(),
        campaign_id=campaign_id,
        mining_run_id=run_dir.name,
        evidence_sha256=sha256_file(evidence),
        created_at=utc_now(),
    )
    json_path, _, digest = write_answer_key(key, campaign_dir)
    return json_path, digest


def create_draft(
    root: Path, campaign_dir: Path, campaign_id: str, run_dir: Path, db: Database
) -> CampaignManifest:
    """Draft manifest for a key already built from ``run_dir``; registers the campaign."""
    if (campaign_dir / "manifest.json").exists():
        raise FileExistsError(f"campaign {campaign_id} already has a manifest")
    require_clean(git_state(root), f"draft campaign {campaign_id}")
    settings = CampaignSettings.load()
    preset = load_preset_yaml(settings.preset)
    key_path = campaign_dir / ANSWER_KEY_JSON
    key = json.loads(key_path.read_text(encoding="utf-8"))
    if key["mining_run_id"] != run_dir.name or key["campaign_id"] != campaign_id:
        raise ValueError(
            f"{key_path} was built for run {key['mining_run_id']} / campaign "
            f"{key['campaign_id']}, not {run_dir.name} / {campaign_id}"
        )
    if key["evidence_sha256"] != sha256_file(run_dir / "evidence.json"):
        raise ValueError(f"run {run_dir.name}'s evidence changed after the key was built")
    frozen = frozen_run_record(root, run_dir, settings.preset, preset)
    manifest = draft_campaign(campaign_id, frozen, sha256_file(key_path), settings)
    digest = manifest.write(campaign_dir)
    db.add_campaign(campaign_id, manifest.model_dump(mode="json"), digest, "draft")
    db.update_run(run_dir.name, campaign_id=campaign_id)
    return manifest


def seal(
    root: Path, campaign_dir: Path, model_id: str, db: Database, git_tag: str
) -> CampaignManifest:
    """Seal a draft (Phase 9 start); the caller creates the git tag afterwards."""
    draft = CampaignManifest.read(campaign_dir)
    preset = load_preset_yaml(draft.frozen.preset)
    sealed = seal_campaign(
        draft,
        root,
        campaign_dir,
        preset,
        current_environment(root, model_id, preset),
        git_state(root),
        git_tag,
    )
    digest = sealed.write(campaign_dir)
    db.update_campaign(sealed.campaign_id, sealed.model_dump(mode="json"), digest, "sealed")
    return sealed
