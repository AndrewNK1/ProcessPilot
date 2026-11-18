"""Builds a ``CampaignRunner`` for the three ways a campaign is run.

- **real**: the sealed campaign, the real model and clock, the global ledger;
  refuses unless ``check_unchanged`` reports nothing.
- **dry run**: the same frozen run and grid in an isolated namespace
  (``<campaign>/dry-run/``, SQLite id ``<campaign>-dry``, purpose ``dev``, its
  own ledger) with the scripted dry-run model (zero API calls) and a simulated
  clock that jumps over waits and quota days, so the whole campaign runs in
  minutes and shows how many quota days it needs. Checks the frozen files;
  does not require a sealed campaign.
- **smoke**: one ProcessPilot run of one segment on the frozen run, purpose
  ``dev``, real model and global ledger, in ``<campaign>/smoke/``; not part of
  the campaign grid.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from processpilot.agent.catalogue import load_agent_settings
from processpilot.agent.dry_run import DRY_RUN_MODEL_ID, DryRunLLM
from processpilot.agent.graph import create_llm, load_as_is_tree, model_id_from_env
from processpilot.agent.ratelimit import (
    QuotaLedger,
    QuotaStopped,
    RateLimitSettings,
    SimulatedClock,
    SystemClock,
    budget,
)
from processpilot.agent.tools import EvidenceTools
from processpilot.api.db import Database
from processpilot.config import load_preset_yaml
from processpilot.evaluation.campaign import (
    CampaignManifest,
    check_frozen,
    check_unchanged,
    current_environment,
)
from processpilot.evaluation.runner import Attempt, CampaignPlan, CampaignRunner
from processpilot.mining.evidence import EvidenceStore, segment_slug
from processpilot.process_tree import labels

DRY_SUFFIX = "-dry"
DRY_DIR = "dry-run"
SMOKE_DIR = "smoke"


def _plan(
    root: Path,
    manifest: CampaignManifest,
    campaign_id: str,
    campaign_dir: Path,
    model_id: str,
    purpose: str,
) -> CampaignPlan:
    return CampaignPlan(
        campaign_id=campaign_id,
        campaign_dir=campaign_dir,
        run_dir=root / manifest.frozen.run_dir,
        preset=load_preset_yaml(manifest.frozen.preset),
        segments=list(manifest.design.segments),
        repetitions=manifest.design.repetitions,
        conditions=list(manifest.design.conditions),
        model_id=model_id,
        purpose=purpose,  # type: ignore[arg-type]
    )


def real_runner(root: Path, campaign_dir: Path, db: Database) -> CampaignRunner:
    """The sealed campaign (a draft still gets a runner, for ``--status``)."""
    manifest = CampaignManifest.read(campaign_dir)
    settings = load_agent_settings()
    model_id = (
        manifest.environment.model_id if manifest.environment else model_id_from_env(settings)
    )
    plan = _plan(root, manifest, manifest.campaign_id, campaign_dir, model_id, "evaluation")

    def preflight() -> list[str]:
        env = current_environment(root, model_id_from_env(settings), plan.preset)
        return check_unchanged(manifest, root, campaign_dir, plan.preset, env)

    rate = RateLimitSettings.load()
    return CampaignRunner(
        plan,
        db,
        rate,
        QuotaLedger(rate.ledger_path()),
        SystemClock(),
        lambda _segment: create_llm(model_id, settings),
        preflight,
        agent_settings=settings,
    )


def dry_runner(root: Path, campaign_dir: Path, db: Database) -> CampaignRunner:
    """The whole grid with the scripted model, a simulated clock and its own ledger."""
    manifest = CampaignManifest.read(campaign_dir)
    dry_id = manifest.campaign_id + DRY_SUFFIX
    plan = _plan(root, manifest, dry_id, campaign_dir / DRY_DIR, DRY_RUN_MODEL_ID, "dev")
    if db.campaign(dry_id) is None:
        db.add_campaign(dry_id, manifest.model_dump(mode="json"), "dry run of a draft", "draft")
    store = EvidenceStore.from_json(plan.run_dir / "evidence.json")

    def factory(segment: str) -> DryRunLLM:
        tree = load_as_is_tree(plan.run_dir, segment)
        activities = sorted(labels(tree)) if tree is not None else []
        return DryRunLLM.for_segment(EvidenceTools(store, segment), activities)

    rate = RateLimitSettings.load()
    return CampaignRunner(
        plan,
        db,
        rate,
        QuotaLedger(plan.campaign_dir / "quota_ledger.jsonl"),
        SimulatedClock(datetime.now(UTC)),
        factory,
        lambda: check_frozen(manifest, root, campaign_dir, plan.preset),
        simulate_days=True,
    )


def smoke_run(root: Path, campaign_dir: Path, db: Database, segment: str) -> Attempt:
    """One dev run of ``segment`` on the frozen run with the real model (asks nothing)."""
    manifest = CampaignManifest.read(campaign_dir)
    if segment not in manifest.design.segments:
        raise ValueError(f"unknown segment {segment!r}; one of {manifest.design.segments}")
    settings = load_agent_settings()
    model_id = model_id_from_env(settings)
    plan = _plan(root, manifest, manifest.campaign_id, campaign_dir / SMOKE_DIR, model_id, "dev")
    problems = check_frozen(manifest, root, campaign_dir, plan.preset)
    if problems:
        raise RuntimeError("frozen run changed:\n- " + "\n- ".join(problems))
    rate = RateLimitSettings.load()
    ledger = QuotaLedger(rate.ledger_path())
    clock = SystemClock()
    b = budget(ledger, rate, plan.provider, clock.now())
    need = rate.worst_case_calls["processpilot"]
    if b.remaining < need:
        raise QuotaStopped(
            f"today's budget has {b.remaining} requests left; a run may need {need}",
            b.reset,
            b.reset_shown,
        )
    runner = CampaignRunner(
        plan,
        db,
        rate,
        ledger,
        clock,
        lambda _segment: create_llm(model_id, settings),
        lambda: [],
        agent_settings=settings,
    )
    slot_dir = plan.campaign_dir / segment_slug(segment)
    agent_run_id = runner._new_agent_run_id(slot_dir, segment, "-smoke")
    return runner.run_agent(
        segment,
        slot_dir,
        agent_run_id,
        1,
        labels={"smoke_of": manifest.campaign_id},
        in_campaign=False,
    )
