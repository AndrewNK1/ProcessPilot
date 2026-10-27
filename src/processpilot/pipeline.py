"""Ingest -> validate -> clean -> mine, as functions the API and notebooks share.

WHY a module for steps that are each one call already: the order and the
arguments are where mistakes hide (which thresholds go to ``validate``, which
to ``apply_cleaning``, loading reference models before ``mine_segment``).
Notebook 02 did this by hand; the API must do exactly the same, so both call
these functions and there is one definition of "a run".

Nothing here decides for the analyst: the cleaning policy is always passed
in. ``recommend_cleaning`` gives ProcessPilot's data-based recommendation per
option, with its reason, for the analyst to accept or override.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from processpilot.config import load_config_yaml
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


# --- cleaning recommendation ---------------------------------------------------


class RecommendationSettings(BaseModel):
    """``configs/cleaning.yaml``: where ProcessPilot draws its lines."""

    max_event_share_removed: float = Field(ge=0.0, le=1.0)
    single_event_case_share_to_remove: float = Field(ge=0.0, le=1.0)

    @classmethod
    def load(cls) -> RecommendationSettings:
        """Read the ``recommendation`` block of ``configs/cleaning.yaml``."""
        return cls(**load_config_yaml("cleaning.yaml")["recommendation"])


class CleaningAdvice(BaseModel):
    """ProcessPilot's view on one cleaning option, with what it would remove."""

    option: str
    recommended: bool
    reason: str
    rows_removed: int
    cases_removed: int
    event_share_removed: float


class CleaningRecommendation(BaseModel):
    """One piece of advice per option of ``CleaningPolicy``."""

    advice: list[CleaningAdvice]

    def policy(self) -> CleaningPolicy:
        """The recommended options as a policy."""
        return CleaningPolicy(**{a.option: a.recommended for a in self.advice})


def _pct(share: float) -> str:
    return f"{share * 100:.1f}%"


def recommend_cleaning(
    canonical: pd.DataFrame,
    report: DataQualityReport,
    preset: dict[str, Any],
    settings: RecommendationSettings,
) -> CleaningRecommendation:
    """Recommend each cleaning option for this dataset, with a reason. Removes nothing.

    WHY a dry run per option instead of reading the report's issue counts:
    the options remove whole cases, so the report's event counts understate
    what a removal costs. Each option is applied alone to the log and its
    cleaning-log entry says exactly how many events and cases would go.

    The rules are ProcessPilot's position, fixed in code and thresholds in
    ``configs/cleaning.yaml``: remove data only when keeping it would distort
    waits or throughput; keep real behaviour, even when it is inconvenient;
    and refuse large removals, which point to a wrong mapping, timestamp
    format or plausible range rather than to bad data. The reason text is
    written by code from the counts, never by a language model.
    """
    events = max(report.event_count, 1)
    cases = max(report.case_count, 1)
    limit = settings.max_event_share_removed
    v = _validation_kwargs(preset)
    min_events = v.get("min_events_per_case_warn", 2)
    collapse = MiningSettings.from_preset(preset).preprocessing.collapse_same_instant_repeats

    def dry_run(option: str) -> tuple[int, int, float]:
        _, after = clean_log(canonical, report, CleaningPolicy(**{option: True}), preset)
        if len(after.cleaning_log) == len(report.cleaning_log):
            return 0, 0, 0.0
        action = after.cleaning_log[-1]
        return action.rows_removed, action.cases_removed, action.rows_removed / events

    advice = []

    option = "drop_cases_with_missing_values"
    rows, gone, share = dry_run(option)
    if rows == 0:
        on, reason = False, "No case has a missing case id, activity or timestamp."
    elif share <= limit:
        on, reason = (
            True,
            (
                f"{gone:,} case(s) ({_pct(share)} of events) have a missing or unparseable case "
                "id, activity or timestamp. Their waits would be measured across the gap, so "
                "removing the whole case is safer than keeping it."
            ),
        )
    else:
        on, reason = (
            False,
            (
                f"This would remove {_pct(share)} of events, above the {_pct(limit)} limit. That "
                "many gaps usually means the column mapping or timestamp format is wrong: fix "
                "step 1 instead of removing data."
            ),
        )
    advice.append(
        CleaningAdvice(
            option=option,
            recommended=on,
            reason=reason,
            rows_removed=rows,
            cases_removed=gone,
            event_share_removed=share,
        )
    )

    option = "drop_cases_with_out_of_range_timestamps"
    rows, gone, share = dry_run(option)
    lo, hi = v.get("plausible_timestamp_min"), v.get("plausible_timestamp_max")
    if lo is None and hi is None:
        on, reason = (
            False,
            (
                "The preset sets no plausible date range, so no timestamp can be judged out of "
                "range."
            ),
        )
    elif rows == 0:
        on, reason = False, f"Every timestamp lies in the plausible range ({lo} to {hi})."
    elif share <= limit:
        on, reason = (
            True,
            (
                f"{gone:,} case(s) ({_pct(share)} of events) contain dates outside {lo} to {hi}, "
                "typically placeholder dates from the source system. One such date distorts "
                "every wait and the throughput time of its case."
            ),
        )
    else:
        on, reason = (
            False,
            (
                f"This would remove {_pct(share)} of events, above the {_pct(limit)} limit. The "
                "plausible range in the preset is probably too narrow; adjust it instead."
            ),
        )
    advice.append(
        CleaningAdvice(
            option=option,
            recommended=on,
            reason=reason,
            rows_removed=rows,
            cases_removed=gone,
            event_share_removed=share,
        )
    )

    option = "drop_single_event_cases"
    rows, gone, share = dry_run(option)
    case_share = gone / cases
    if gone == 0:
        on, reason = False, f"Every case has at least {min_events} events."
    elif case_share > settings.single_event_case_share_to_remove:
        on, reason = (
            True,
            (
                f"{gone:,} case(s) ({_pct(case_share)} of cases) have fewer than {min_events} "
                "events. Each counts as zero throughput time, so this many would pull the "
                "throughput statistics towards zero. The removal is logged with its count."
            ),
        )
    else:
        on, reason = (
            False,
            (
                f"{gone:,} case(s) ({_pct(case_share)} of cases) have fewer than {min_events} "
                "events. Keep them: they are real cases, often still open when the log was "
                "extracted, and removing them would hide work that never progressed. They add "
                "no transitions, and their effect on throughput statistics is small."
            ),
        )
    advice.append(
        CleaningAdvice(
            option=option,
            recommended=on,
            reason=reason,
            rows_removed=rows,
            cases_removed=gone,
            event_share_removed=share,
        )
    )

    option = "drop_exact_duplicate_events"
    rows, gone, share = dry_run(option)
    if rows == 0:
        on, reason = False, "There are no exact duplicate events."
    elif collapse:
        on, reason = (
            False,
            (
                f"{rows:,} event(s) ({_pct(share)}) repeat another event exactly. Keep them: "
                "mining already counts same-case, same-activity, same-instant events once for "
                "flows, variants and rework, and keeping them preserves the true workload per "
                "resource (e.g. several line items posted together)."
            ),
        )
    else:
        on, reason = (
            True,
            (
                f"{rows:,} event(s) ({_pct(share)}) repeat another event exactly. The preset "
                "does not collapse same-instant repeats, so they would appear as zero-wait loops "
                "and phantom rework."
            ),
        )
    advice.append(
        CleaningAdvice(
            option=option,
            recommended=on,
            reason=reason,
            rows_removed=rows,
            cases_removed=gone,
            event_share_removed=share,
        )
    )
    return CleaningRecommendation(advice=advice)


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
