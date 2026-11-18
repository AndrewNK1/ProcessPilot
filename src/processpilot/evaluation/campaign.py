"""Campaign manifest: one file every evaluation run points to.

WHY a draft and a seal (user decision 2026-11-18): the answer key depends
only on the frozen mining run, so it is fixed now, before any LLM run; but the
code that runs the campaign (rate limiter, runner, baseline) is still being
written. A campaign is therefore

1. **drafted** (``draft_campaign``): frozen mining run id, the SHA-256 of
   every file the agent and the scorers read from it, the source log's size
   and SHA-256, the preset name and content hash, the mining commit, the
   design (segments, repetitions, conditions) and the answer key's SHA-256;
2. **sealed** (``seal_campaign``) once Phases 5-6 are committed: re-checks
   every frozen hash and the answer key (refuses on any difference), requires
   a clean ``src/``/``configs/`` tree, and records the environment (commit,
   prompt version, model id, all agent settings incl. the provider kwargs such
   as Gemini's thinking_level, catalogue/domain versions, library versions,
   hardware, rate-limit and baseline settings).

``check_unchanged`` lists every difference between a manifest and the current
files/environment; the campaign runner refuses to start a run unless the
campaign is sealed and the list is empty.

Nothing here calls an LLM or changes the frozen run.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from processpilot.agent.catalogue import (
    load_agent_settings,
    load_domain_knowledge,
    load_heuristics,
)
from processpilot.agent.nodes import AGENT_PACKAGES
from processpilot.agent.prompts import PROMPT_VERSION
from processpilot.config import load_config_yaml
from processpilot.evaluation.answer_key import ANSWER_KEY_CSV, ANSWER_KEY_JSON, sha256_file
from processpilot.mining.evidence import EVIDENCE_SCHEMA_VERSION
from processpilot.mining.run import hardware_info, library_versions

CAMPAIGN_MANIFEST = "manifest.json"
MINING_RECORD = "eval_mining.json"
# Files of the frozen run the agent, the baseline or the scorers read.
RUN_FILES = ("manifest.json", "evidence.json", "data_quality.json", MINING_RECORD)
SEGMENT_FILES = (
    "evidence.json",
    "dfg.json",
    "as_is_tree.json",
    "case_waits.parquet",
    "as_is.bpmn",
    "activity_waits.json",
)
# Paths whose uncommitted changes would make a recorded commit meaningless.
TRACKED_FOR_CLEANLINESS = ("src", "configs")


class CampaignSettings(BaseModel):
    """``campaign`` block of ``configs/eval.yaml``."""

    preset: str
    repetitions: int = Field(gt=0)
    conditions: list[Literal["processpilot", "baseline"]]

    @classmethod
    def load(cls) -> CampaignSettings:
        """Read the block from ``configs/eval.yaml``."""
        return cls.model_validate(load_config_yaml("eval.yaml")["campaign"])


class GitState(BaseModel):
    """HEAD and the uncommitted paths under ``src/`` and ``configs/``."""

    commit: str
    dirty: list[str]


def git_state(root: Path) -> GitState:
    """Read HEAD and ``git status`` for the tracked paths (fails loudly outside git)."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout

    commit = git("rev-parse", "HEAD").strip()
    dirty = [
        line[3:].strip()
        for line in git("status", "--porcelain", "--", *TRACKED_FOR_CLEANLINESS).splitlines()
        if line.strip()
    ]
    return GitState(commit=commit, dirty=dirty)


def require_clean(state: GitState, action: str) -> None:
    """Refuse ``action`` when ``src/`` or ``configs/`` have uncommitted changes."""
    if state.dirty:
        raise RuntimeError(
            f"cannot {action}: uncommitted changes in {', '.join(TRACKED_FOR_CLEANLINESS)}: "
            f"{state.dirty[:10]}{' ...' if len(state.dirty) > 10 else ''}; commit them first, so "
            "the recorded commit is the code that ran"
        )


def preset_sha256(preset: dict[str, Any]) -> str:
    """Content hash of a resolved preset (key order and YAML formatting do not matter)."""
    canonical = json.dumps(preset, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def frozen_file_hashes(run_dir: Path, segments: dict[str, str]) -> dict[str, str]:
    """SHA-256 of every frozen file, keyed by its path relative to the run (posix)."""
    rel = [*RUN_FILES, *(f"{slug}/{name}" for slug in segments.values() for name in SEGMENT_FILES)]
    missing = [r for r in rel if not (run_dir / r).is_file()]
    if missing:
        raise FileNotFoundError(f"frozen run {run_dir.name} lacks {missing}")
    return {r: sha256_file(run_dir / r) for r in rel}


class SourceRecord(BaseModel):
    """The raw log the frozen run was mined from (the baseline re-reads it)."""

    path: str
    size_bytes: int
    sha256: str


class FrozenRun(BaseModel):
    """What the draft fixes: the mining run and everything derived from it."""

    mining_run_id: str
    run_dir: str  # relative to the project root
    preset: str
    preset_sha256: str
    evidence_schema_version: int
    mining_git_commit: str
    cleaning_policy: dict[str, Any]
    source: SourceRecord
    segments: dict[str, str]  # name -> slug, manifest order
    files: dict[str, str]


class AnswerKeyRecord(BaseModel):
    """Where the answer key is and what it hashes to."""

    json_path: str  # relative to the campaign folder
    csv_path: str
    sha256: str


class Design(BaseModel):
    """Fixed by the methodology: what is run, how often."""

    segments: list[str]
    repetitions: int
    conditions: list[str]


class Environment(BaseModel):
    """What sealing records and every run re-checks."""

    git_commit: str
    prompt_version: str
    model_id: str
    agent_settings: dict[str, Any]
    model_kwargs: dict[str, Any] | None
    model_notes: list[str]
    heuristics_catalogue_version: int
    domain_knowledge: dict[str, Any] | None
    library_versions: dict[str, str]
    hardware: dict[str, Any]
    rate_limit: dict[str, Any] | None
    baseline: dict[str, Any] | None


class CampaignManifest(BaseModel):
    """``outputs/eval/<campaign_id>/manifest.json``."""

    campaign_id: str
    status: Literal["draft", "sealed"]
    created_at: str
    sealed_at: str | None = None
    git_tag: str | None = None
    frozen: FrozenRun
    answer_key: AnswerKeyRecord
    design: Design
    environment: Environment | None = None

    def write(self, campaign_dir: Path) -> str:
        """Write the manifest; returns its SHA-256."""
        path = campaign_dir / CAMPAIGN_MANIFEST
        path.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return sha256_file(path)

    @classmethod
    def read(cls, campaign_dir: Path) -> CampaignManifest:
        """Read ``manifest.json`` from a campaign folder."""
        raw = (campaign_dir / CAMPAIGN_MANIFEST).read_text(encoding="utf-8")
        return cls.model_validate_json(raw)


def utc_now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def frozen_run_record(
    root: Path, run_dir: Path, preset_name: str, preset: dict[str, Any]
) -> FrozenRun:
    """Describe a mined run for a draft; refuses a run of another preset or schema."""
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    mining = json.loads((run_dir / MINING_RECORD).read_text(encoding="utf-8"))
    recorded = (manifest.get("source") or {}).get("preset")
    if recorded != preset_name or mining.get("preset") != preset_name:
        raise ValueError(
            f"run {run_dir.name} was mined with preset {recorded!r}, not {preset_name!r}"
        )
    schema = manifest.get("evidence_schema_version", 1)
    if schema != EVIDENCE_SCHEMA_VERSION:
        raise ValueError(
            f"run {run_dir.name} has evidence schema {schema}; this code writes "
            f"{EVIDENCE_SCHEMA_VERSION}; mine the frozen run again"
        )
    current = preset_sha256(preset)
    if mining.get("preset_sha256") != current:
        raise ValueError(
            f"preset {preset_name!r} changed since run {run_dir.name} was mined "
            "(content hash differs); mine the frozen run again"
        )
    src = manifest["source"]
    source_path = Path(src["path"])
    size = source_path.stat().st_size
    if size != src.get("size_bytes"):
        raise ValueError(f"source {source_path} changed size since mining")
    segments = dict(manifest["segments"])
    return FrozenRun(
        mining_run_id=run_dir.name,
        run_dir=run_dir.resolve().relative_to(root.resolve()).as_posix(),
        preset=preset_name,
        preset_sha256=current,
        evidence_schema_version=schema,
        mining_git_commit=mining["git_commit"],
        cleaning_policy=mining["cleaning_policy"],
        source=SourceRecord(
            path=str(source_path), size_bytes=size, sha256=sha256_file(source_path)
        ),
        segments=segments,
        files=frozen_file_hashes(run_dir, segments),
    )


def check_frozen(
    manifest: CampaignManifest,
    root: Path,
    campaign_dir: Path,
    preset: dict[str, Any],
    *,
    include_source: bool = True,
) -> list[str]:
    """Differences between the draft's frozen record and the files now on disk."""
    problems: list[str] = []
    f = manifest.frozen
    run_dir = root / f.run_dir
    for rel, expected in f.files.items():
        path = run_dir / rel
        if not path.is_file():
            problems.append(f"frozen file missing: {f.run_dir}/{rel}")
        elif sha256_file(path) != expected:
            problems.append(f"frozen file changed: {f.run_dir}/{rel}")
    if preset_sha256(preset) != f.preset_sha256:
        problems.append(f"preset {f.preset!r} changed (content hash differs)")
    key = campaign_dir / manifest.answer_key.json_path
    if not key.is_file():
        problems.append(f"answer key missing: {key.name}")
    elif sha256_file(key) != manifest.answer_key.sha256:
        problems.append("answer key changed (SHA-256 differs)")
    if include_source:
        src = Path(f.source.path)
        if not src.is_file():
            problems.append(f"source log missing: {src}")
        elif src.stat().st_size != f.source.size_bytes or sha256_file(src) != f.source.sha256:
            problems.append(f"source log changed: {src}")
    return problems


def current_environment(root: Path, model_id: str, preset: dict[str, Any]) -> Environment:
    """Collect everything a sealed campaign records (reads configs; no LLM)."""
    settings = load_agent_settings()
    domain = load_domain_knowledge(preset.get("domain_knowledge"))
    eval_cfg = load_config_yaml("eval.yaml")
    return Environment(
        git_commit=git_state(root).commit,
        prompt_version=PROMPT_VERSION,
        model_id=model_id,
        agent_settings=settings.model_dump(mode="json"),
        model_kwargs=settings.model.logged_kwargs_for(model_id),
        model_notes=settings.model.notes_for(model_id),
        heuristics_catalogue_version=load_heuristics().version,
        domain_knowledge=None
        if domain is None
        else {"domain": domain.domain, "version": domain.version},
        library_versions=library_versions(AGENT_PACKAGES),
        hardware=hardware_info(),
        rate_limit=eval_cfg.get("rate_limit"),
        baseline=eval_cfg.get("baseline"),
    )


def environment_problems(env: Environment) -> list[str]:
    """Reasons an environment cannot be sealed (missing settings)."""
    problems = []
    if env.rate_limit is None:
        problems.append("configs/eval.yaml has no rate_limit block (evaluation Phase 5)")
    if env.baseline is None:
        problems.append("configs/eval.yaml has no baseline block (evaluation Phase 6)")
    if env.model_id.startswith("google_genai:") and "thinking_level" not in (
        env.model_kwargs or {}
    ):
        problems.append("Gemini thinking_level is not pinned in configs/agent.yaml")
    return problems


def check_environment(manifest: CampaignManifest, env: Environment) -> list[str]:
    """Fields of the sealed environment that differ now (empty for a draft)."""
    if manifest.environment is None:
        return []
    sealed = manifest.environment.model_dump(mode="json")
    now = env.model_dump(mode="json")
    return [
        f"{key} changed since sealing: {sealed[key]!r} -> {now[key]!r}"
        if not isinstance(sealed[key], dict)
        else f"{key} changed since sealing"
        for key in sealed
        if sealed[key] != now[key]
    ]


def check_unchanged(
    manifest: CampaignManifest,
    root: Path,
    campaign_dir: Path,
    preset: dict[str, Any],
    env: Environment,
) -> list[str]:
    """Every reason the campaign may not continue; empty means it may."""
    problems = check_frozen(manifest, root, campaign_dir, preset)
    if manifest.status != "sealed":
        problems.append(f"campaign {manifest.campaign_id} is {manifest.status}, not sealed")
    return problems + check_environment(manifest, env)


def seal_campaign(
    manifest: CampaignManifest,
    root: Path,
    campaign_dir: Path,
    preset: dict[str, Any],
    env: Environment,
    git: GitState,
    git_tag: str,
) -> CampaignManifest:
    """The sealed manifest; refuses on any frozen/key difference or missing setting."""
    if manifest.status != "draft":
        raise RuntimeError(f"campaign {manifest.campaign_id} is already {manifest.status}")
    require_clean(git, f"seal campaign {manifest.campaign_id}")
    problems = check_frozen(manifest, root, campaign_dir, preset) + environment_problems(env)
    if problems:
        raise RuntimeError(
            f"cannot seal campaign {manifest.campaign_id}:\n- " + "\n- ".join(problems)
        )
    return manifest.model_copy(
        update={
            "status": "sealed",
            "sealed_at": utc_now(),
            "git_tag": git_tag,
            "environment": env,
        }
    )


def draft_campaign(
    campaign_id: str,
    frozen: FrozenRun,
    key_sha256: str,
    settings: CampaignSettings,
) -> CampaignManifest:
    """A draft manifest for an answer key already written to the campaign folder."""
    return CampaignManifest(
        campaign_id=campaign_id,
        status="draft",
        created_at=utc_now(),
        frozen=frozen,
        answer_key=AnswerKeyRecord(
            json_path=ANSWER_KEY_JSON, csv_path=ANSWER_KEY_CSV, sha256=key_sha256
        ),
        design=Design(
            segments=list(frozen.segments),
            repetitions=settings.repetitions,
            conditions=list(settings.conditions),
        ),
    )
