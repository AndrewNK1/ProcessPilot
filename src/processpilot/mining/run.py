"""Per-segment mining orchestration and artefact writing.

WHY split ``mine_segment`` (pure) from ``write_run`` (I/O): every number is
produced by a function that can be tested on a 10-case log without touching
the filesystem; only the final step writes files. Artefact layout (Phase 0
decision): ``outputs/runs/<run_id>/<segment-slug>/``, plus a run-level
``evidence.json`` and ``manifest.json`` recording settings and library versions
so any result can be traced back to how it was produced.
"""

from __future__ import annotations

import json
import platform
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pandas as pd

from processpilot.mining.bottlenecks import rank_bottlenecks
from processpilot.mining.conformance import check_rules
from processpilot.mining.discovery import DFGResult, compute_dfg, discover_bpmn
from processpilot.mining.evidence import EvidenceStore, build_segment_evidence, segment_slug
from processpilot.mining.kpis import compute_kpis
from processpilot.mining.prepare import collapse_same_instant_repeats
from processpilot.mining.resources import analyse_resources
from processpilot.mining.rework import analyse_rework
from processpilot.mining.settings import MiningSettings
from processpilot.mining.variants import analyse_variants

_VERSIONED_PACKAGES = ("processpilot", "pm4py", "pandas", "numpy", "pyarrow", "pydantic")


@dataclass(frozen=True)
class SegmentMiningResult:
    """Everything mined for one segment; the BPMN is a pm4py object (or None)."""

    segment: str
    evidence: EvidenceStore
    dfg: DFGResult
    bpmn: Any | None


def mine_segment(
    raw_df: pd.DataFrame, segment: str, settings: MiningSettings, *, discover_model: bool = True
) -> SegmentMiningResult:
    """Run every analysis on one segment's canonical log. No I/O.

    ``discover_model=False`` skips the inductive miner, which is the slow step
    and irrelevant to most unit tests.
    """
    if settings.preprocessing.collapse_same_instant_repeats:
        mining_df, collapsed = collapse_same_instant_repeats(raw_df)
    else:
        mining_df, collapsed = raw_df, 0

    a = settings.analysis
    dfg = compute_dfg(mining_df)
    evidence = build_segment_evidence(
        segment=segment,
        kpis=compute_kpis(mining_df, raw_event_count=len(raw_df), collapsed=collapsed),
        bottlenecks=rank_bottlenecks(dfg, a.top_n_bottlenecks),
        variants=analyse_variants(mining_df, a.top_n_variants, a.max_example_cases),
        rework=analyse_rework(mining_df, a.top_n_rework, a.max_example_cases),
        resources=analyse_resources(raw_df, mining_df, a.top_n_resources, a.top_n_handovers),
        conformance=check_rules(
            mining_df,
            segment,
            settings.conformance_rules,
            settings.case_completion.end_activities_for(segment),
            a.max_example_cases,
        ),
    )
    bpmn = (
        discover_bpmn(mining_df, settings.discovery.inductive_miner_noise_threshold)
        if discover_model
        else None
    )
    return SegmentMiningResult(segment=segment, evidence=evidence, dfg=dfg, bpmn=bpmn)


def library_versions() -> dict[str, str]:
    """Installed versions of the packages that determine every number."""
    versions = {"python": platform.python_version()}
    for name in _VERSIONED_PACKAGES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "not installed"
    return versions


def new_run_id(now: datetime | None = None) -> str:
    """Sortable, filesystem-safe run id, e.g. ``20261016T143005Z``."""
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def write_run(
    results: list[SegmentMiningResult],
    run_dir: Path,
    settings: MiningSettings,
    source_description: dict[str, Any] | None = None,
) -> EvidenceStore:
    """Write per-segment artefacts and the run-level evidence + manifest.

    Returns the merged store; raises if two segments slugify to the same
    folder name, since their evidence ids would collide too.

    BPMN layout: pm4py's auto-layout shells out to Graphviz ``dot``. Without
    it the model is still complete and re-readable, but every shape sits at
    (0, 0); the manifest records which happened so a degenerate diagram is
    never mistaken for a layout bug.
    """
    import pm4py

    auto_layout = shutil.which("dot") is not None

    slugs = [segment_slug(r.segment) for r in results]
    if len(set(slugs)) != len(slugs):
        raise ValueError(f"segment names collide after slugifying: {slugs}")

    merged = EvidenceStore()
    for result, slug in zip(results, slugs, strict=True):
        seg_dir = run_dir / slug
        seg_dir.mkdir(parents=True, exist_ok=True)
        view, hidden = result.dfg.filtered(settings.discovery.dfg_min_edge_frequency_share)
        _write_json(seg_dir / "dfg.json", result.dfg.model_dump())
        _write_json(
            seg_dir / "dfg_view.json",
            {**view.model_dump(), "hidden_edge_count": hidden},
        )
        result.evidence.to_json(seg_dir / "evidence.json")
        if result.bpmn is not None:
            pm4py.write_bpmn(result.bpmn, str(seg_dir / "as_is.bpmn"), auto_layout=auto_layout)
        merged = merged.merged(result.evidence)

    merged.to_json(run_dir / "evidence.json")
    _write_json(
        run_dir / "manifest.json",
        {
            "run_id": run_dir.name,
            "created_at": datetime.now(UTC).isoformat(),
            "segments": {r.segment: s for r, s in zip(results, slugs, strict=True)},
            "evidence_count": len(merged.items),
            "bpmn_layout": "graphviz" if auto_layout else "none (graphviz 'dot' not on PATH)",
            "settings": settings.model_dump(),
            "source": source_description or {},
            "library_versions": library_versions(),
        },
    )
    return merged


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
