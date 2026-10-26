"""Ingest -> validate -> clean -> mine, as functions the API and notebooks share.

WHY a module for steps that are each one call already: the order and the
arguments are where mistakes hide (which thresholds go to ``validate``, which
to ``apply_cleaning``, loading reference models before ``mine_segment``).
Notebook 02 did this by hand; the API must do exactly the same, so both call
these functions and there is one definition of "a run".

Nothing here decides for the analyst: the cleaning policy is always passed
in, and the preset's suggested policy is only offered as the default.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

from processpilot.ingest.mapping import ColumnMapping, apply_mapping, split_by_segment
from processpilot.ingest.validate import DataQualityReport, apply_cleaning, validate
from processpilot.mining.evidence import EvidenceStore
from processpilot.mining.reference import load_reference_model
from processpilot.mining.run import mine_segment, write_run
from processpilot.mining.settings import MiningSettings
from processpilot.sources.base import DataSource

DATA_QUALITY_FILE = "data_quality.json"

Progress = Callable[[str], None]


class CleaningPolicy(BaseModel):
    """The four explicit cleaning switches of ``apply_cleaning``; all off by default."""

    drop_cases_with_missing_values: bool = False
    drop_cases_with_out_of_range_timestamps: bool = False
    drop_single_event_cases: bool = False
    drop_exact_duplicate_events: bool = False


def suggested_cleaning_policy(preset: dict[str, Any]) -> CleaningPolicy:
    """The preset's ``suggested_cleaning_policy``, or nothing removed."""
    return CleaningPolicy(**(preset.get("suggested_cleaning_policy") or {}))


def _validation_kwargs(preset: dict[str, Any]) -> dict[str, Any]:
    return dict(preset.get("validation") or {})


def load_canonical(source: DataSource, mapping: ColumnMapping) -> pd.DataFrame:
    """Raw rows from the source, mapped to the canonical schema."""
    return apply_mapping(source.fetch(), mapping)


def validate_log(canonical: pd.DataFrame, preset: dict[str, Any]) -> DataQualityReport:
    """``validate`` with the preset's thresholds. Fixes nothing."""
    return validate(canonical, **_validation_kwargs(preset))


def clean_log(
    canonical: pd.DataFrame,
    report: DataQualityReport,
    policy: CleaningPolicy,
    preset: dict[str, Any],
) -> tuple[pd.DataFrame, DataQualityReport]:
    """``apply_cleaning`` with the analyst's policy and the preset's bounds."""
    v = _validation_kwargs(preset)
    bounds = {
        k: v[k]
        for k in ("plausible_timestamp_min", "plausible_timestamp_max", "min_events_per_case_warn")
        if k in v
    }
    return apply_cleaning(canonical, report, **policy.model_dump(), **bounds)


def mine_log(
    clean: pd.DataFrame,
    preset: dict[str, Any],
    run_dir: Path,
    source_description: dict[str, Any] | None = None,
    quality: DataQualityReport | None = None,
    progress: Progress | None = None,
) -> EvidenceStore:
    """Mine every segment and write the run folder; returns the merged evidence.

    Reference models configured in the preset are loaded here, because
    ``mine_segment`` refuses to run without one that is configured.
    ``quality`` (the report after cleaning, with its cleaning log) is written
    next to the evidence so a run records what was removed before mining.
    """
    settings = MiningSettings.from_preset(preset)
    segments = split_by_segment(clean)
    results = []
    for i, (name, seg_df) in enumerate(segments.items(), start=1):
        if progress:
            cases = seg_df["case_id"].nunique()
            progress(f"mining segment {i}/{len(segments)}: {name} ({cases} cases)")
        results.append(
            mine_segment(
                seg_df, name, settings, reference_model=load_reference_model(settings, name)
            )
        )
    if progress:
        progress("writing run artefacts")
    store = write_run(results, run_dir, settings, source_description)
    if quality is not None:
        (run_dir / DATA_QUALITY_FILE).write_text(
            json.dumps(quality.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return store
