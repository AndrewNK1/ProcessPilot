"""Data-quality validation. Reports problems; never fixes them silently.

WHY the strict separation between ``validate`` and ``apply_cleaning``: dropping
rows changes every downstream metric. For a thesis the removals have to be
defensible and reproducible, so the caller chooses an explicit policy and every
removal is recorded in the report that accompanies the results.

Checks: nulls in required columns; unparseable timestamps (kept apart from
missing ones via ``timestamp_raw``); timestamps outside a configured plausible
range (BPI 2019 contains 1948 and 2070 dates); single-event cases; duplicate
events; non-monotonic timestamps within a case in the log's row order (i.e.
the canonical sort guarantee is broken); timestamp ties within a case (these
make directly-follows order ambiguous, so they bound the trustworthiness of
every waiting-time number); activity cardinality; overall log size.

Each issue carries severity (info/warn/error), a count, a share of rows, and
example rows so an analyst can look at the actual data behind the warning.
"""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from processpilot.schema import TIMESTAMP_RAW

Severity = Literal["info", "warn", "error"]

_MAX_EXAMPLE_ROWS = 5


class DataQualityIssue(BaseModel):
    """One finding. Code-generated, never an LLM guess about data quality.

    ``count`` is in the unit the message names (rows, cases, or activities);
    ``share`` is always the share of event rows affected, so issues can be
    compared against each other on one scale.
    """

    code: str
    severity: Severity
    message: str
    count: int
    share: float
    example_rows: list[dict[str, Any]] = []


class CleaningAction(BaseModel):
    """One removal actually applied by ``apply_cleaning``, logged for the record."""

    code: str
    rows_removed: int
    cases_removed: int


class DataQualityReport(BaseModel):
    """Every issue found, plus a record of any cleaning actually applied."""

    issue_count: int = 0
    event_count: int = 0
    case_count: int = 0
    issues: list[DataQualityIssue] = []
    cleaning_log: list[CleaningAction] = []

    def to_frame(self) -> pd.DataFrame:
        """One row per issue, for display in a notebook."""
        if not self.issues:
            return pd.DataFrame(
                columns=["code", "severity", "message", "count", "share", "example_rows"]
            )
        return pd.DataFrame([issue.model_dump() for issue in self.issues])


def _example_rows(df: pd.DataFrame, mask: pd.Series, columns: list[str]) -> list[dict[str, Any]]:
    cols = [c for c in columns if c in df.columns]
    sample = df.loc[mask, cols].head(_MAX_EXAMPLE_ROWS)
    return sample.astype(object).where(sample.notna(), None).to_dict(orient="records")


def validate(
    df: pd.DataFrame,
    *,
    plausible_timestamp_min: pd.Timestamp | str | None = None,
    plausible_timestamp_max: pd.Timestamp | str | None = None,
    max_distinct_activities_warn: int = 200,
    min_events_per_case_warn: int = 2,
) -> DataQualityReport:
    """Build a ``DataQualityReport`` for a canonical event log. Fixes nothing.

    Thresholds are parameters, not constants, because they come from a
    dataset's preset YAML -- a different log may have a different plausible
    date range or a different activity-cardinality tolerance.
    """
    issues: list[DataQualityIssue] = []
    n_rows = len(df)
    display_cols = ["case_id", "activity", "timestamp"]

    # A NaT timestamp whose source value survives in ``timestamp_raw`` was
    # present but unconvertible -- that is "unparseable", not "missing".
    had_raw_value = (
        df[TIMESTAMP_RAW].notna()
        if TIMESTAMP_RAW in df.columns
        else pd.Series(False, index=df.index)
    )

    # --- nulls in required columns -------------------------------------
    for col in ("case_id", "activity", "timestamp"):
        if col not in df.columns:
            continue
        null_mask = df[col].isna()
        if col == "timestamp":
            null_mask &= ~had_raw_value
        if null_mask.any():
            issues.append(
                DataQualityIssue(
                    code=f"null_{col}",
                    severity="error",
                    message=f"{int(null_mask.sum())} row(s) have a null '{col}'.",
                    count=int(null_mask.sum()),
                    share=float(null_mask.mean()),
                    example_rows=_example_rows(df, null_mask, display_cols),
                )
            )

    # --- unparseable timestamps -----------------------------------------
    if "timestamp" in df.columns:
        ts = df["timestamp"]
        if pd.api.types.is_datetime64_any_dtype(ts):
            unparseable_mask = ts.isna() & had_raw_value
        else:
            parsed = pd.to_datetime(ts, errors="coerce")
            unparseable_mask = parsed.isna() & ts.notna()
        if unparseable_mask.any():
            issues.append(
                DataQualityIssue(
                    code="unparseable_timestamp",
                    severity="error",
                    message=(
                        f"{int(unparseable_mask.sum())} timestamp(s) could not be parsed or "
                        "lie outside the representable range (1677-2262)."
                    ),
                    count=int(unparseable_mask.sum()),
                    share=float(unparseable_mask.mean()),
                    example_rows=_example_rows(
                        df, unparseable_mask, [*display_cols, TIMESTAMP_RAW]
                    ),
                )
            )

        # --- out-of-plausible-range timestamps --------------------------
        if plausible_timestamp_min is not None or plausible_timestamp_max is not None:
            valid_ts = ts.where(~unparseable_mask)
            lo = pd.Timestamp(plausible_timestamp_min) if plausible_timestamp_min else None
            hi = pd.Timestamp(plausible_timestamp_max) if plausible_timestamp_max else None
            out_mask = pd.Series(False, index=df.index)
            if lo is not None:
                out_mask |= valid_ts < lo
            if hi is not None:
                out_mask |= valid_ts > hi
            if out_mask.any():
                issues.append(
                    DataQualityIssue(
                        code="timestamp_out_of_plausible_range",
                        severity="error",
                        message=(
                            f"{int(out_mask.sum())} timestamp(s) fall outside the plausible "
                            f"range [{lo}, {hi}] (e.g. BPI 2019's junk 1948/2070 dates)."
                        ),
                        count=int(out_mask.sum()),
                        share=float(out_mask.mean()),
                        example_rows=_example_rows(df, out_mask, display_cols),
                    )
                )

    # --- single-event cases ---------------------------------------------
    if "case_id" in df.columns:
        case_sizes = df.groupby("case_id").size()
        small_cases = case_sizes[case_sizes < min_events_per_case_warn]
        if len(small_cases) > 0:
            mask = df["case_id"].isin(small_cases.index)
            issues.append(
                DataQualityIssue(
                    code="single_event_case",
                    severity="warn",
                    message=(
                        f"{len(small_cases)} case(s) ({int(mask.sum())} row(s)) have fewer "
                        f"than {min_events_per_case_warn} event(s)."
                    ),
                    count=int(len(small_cases)),
                    share=float(mask.mean()),
                    example_rows=_example_rows(df, mask, display_cols),
                )
            )

    # --- duplicate events -------------------------------------------------
    dup_cols = [c for c in ("case_id", "activity", "timestamp", "resource") if c in df.columns]
    if dup_cols:
        dup_mask = df.duplicated(subset=dup_cols, keep=False)
        if dup_mask.any():
            issues.append(
                DataQualityIssue(
                    code="duplicate_event",
                    severity="warn",
                    message=f"{int(dup_mask.sum())} row(s) are exact duplicates on {dup_cols}.",
                    count=int(dup_mask.sum()),
                    share=float(dup_mask.mean()),
                    example_rows=_example_rows(df, dup_mask, display_cols),
                )
            )

    # --- non-monotonic timestamps within a case, in the given row order ---
    # Checked on the rows exactly as passed, NOT re-sorted: re-sorting first
    # would make this check unable to ever fire. It guards the canonical
    # guarantee that every event follows its predecessor in time.
    if {"case_id", "timestamp"}.issubset(df.columns):
        same_case_given = (df["case_id"] == df["case_id"].shift(1)).fillna(False)
        earlier_given = (df["timestamp"] < df["timestamp"].shift(1)).fillna(False)
        non_monotonic_mask = (same_case_given & earlier_given).astype(bool)
        if non_monotonic_mask.any():
            issues.append(
                DataQualityIssue(
                    code="non_monotonic_timestamp",
                    severity="error",
                    message=(
                        f"{int(non_monotonic_mask.sum())} row(s) are earlier than the previous "
                        "event of the same case in row order: the log is not sorted by "
                        "(case_id, timestamp), so it did not come through apply_mapping."
                    ),
                    count=int(non_monotonic_mask.sum()),
                    share=float(non_monotonic_mask.mean()),
                    example_rows=_example_rows(df, non_monotonic_mask, display_cols),
                )
            )

        sorted_df = df.sort_values(["case_id", "timestamp"], kind="stable")
        same_case = (sorted_df["case_id"] == sorted_df["case_id"].shift(1)).fillna(False)
        same_timestamp_as_prev = (sorted_df["timestamp"] == sorted_df["timestamp"].shift(1)).fillna(
            False
        )
        tie_mask = (
            (same_case & same_timestamp_as_prev).astype(bool).reindex(df.index, fill_value=False)
        )
        if tie_mask.any():
            issues.append(
                DataQualityIssue(
                    code="timestamp_tie_within_case",
                    severity="info",
                    message=(
                        f"{int(tie_mask.sum())} row(s) share an exact timestamp with another "
                        "event in the same case, making directly-follows order ambiguous there."
                    ),
                    count=int(tie_mask.sum()),
                    share=float(tie_mask.mean()),
                    example_rows=_example_rows(df, tie_mask, display_cols),
                )
            )

    # --- activity cardinality --------------------------------------------
    if "activity" in df.columns:
        n_activities = df["activity"].nunique()
        if n_activities > max_distinct_activities_warn:
            issues.append(
                DataQualityIssue(
                    code="high_activity_cardinality",
                    severity="warn",
                    message=(
                        f"{n_activities} distinct activities exceeds the sanity threshold "
                        f"of {max_distinct_activities_warn}; labels may not be normalised."
                    ),
                    count=int(n_activities),
                    share=float(n_activities / max(n_rows, 1)),
                )
            )

    # --- overall size (informational) ------------------------------------
    case_count = int(df["case_id"].nunique()) if "case_id" in df.columns else 0
    issues.append(
        DataQualityIssue(
            code="log_size",
            severity="info",
            message=f"Log has {n_rows} event(s) across {case_count} case(s).",
            count=n_rows,
            share=1.0,
        )
    )

    return DataQualityReport(
        issue_count=len(issues),
        event_count=n_rows,
        case_count=case_count,
        issues=issues,
    )


def apply_cleaning(
    df: pd.DataFrame,
    report: DataQualityReport,
    *,
    drop_cases_with_out_of_range_timestamps: bool = False,
    drop_single_event_cases: bool = False,
    drop_exact_duplicate_events: bool = False,
    plausible_timestamp_min: pd.Timestamp | str | None = None,
    plausible_timestamp_max: pd.Timestamp | str | None = None,
    min_events_per_case_warn: int = 2,
) -> tuple[pd.DataFrame, DataQualityReport]:
    """Apply an explicit, caller-chosen cleaning policy and log every removal.

    Returns a new dataframe and a new report with ``cleaning_log`` populated.
    Nothing is removed unless the corresponding flag is ``True`` -- the
    default calling ``validate()`` and discarding nothing is always the
    starting point.
    """
    out = df
    cleaning_log = list(report.cleaning_log)

    if drop_cases_with_out_of_range_timestamps and "timestamp" in out.columns:
        lo = pd.Timestamp(plausible_timestamp_min) if plausible_timestamp_min else None
        hi = pd.Timestamp(plausible_timestamp_max) if plausible_timestamp_max else None
        out_mask = pd.Series(False, index=out.index)
        if lo is not None:
            out_mask |= out["timestamp"] < lo
        if hi is not None:
            out_mask |= out["timestamp"] > hi
        bad_cases = out.loc[out_mask, "case_id"].unique()
        if len(bad_cases) > 0:
            case_mask = out["case_id"].isin(bad_cases)
            cleaning_log.append(
                CleaningAction(
                    code="drop_cases_with_out_of_range_timestamps",
                    rows_removed=int(case_mask.sum()),
                    cases_removed=int(len(bad_cases)),
                )
            )
            out = out.loc[~case_mask].reset_index(drop=True)

    if drop_single_event_cases and "case_id" in out.columns:
        case_sizes = out.groupby("case_id").size()
        small_cases = case_sizes[case_sizes < min_events_per_case_warn].index
        if len(small_cases) > 0:
            case_mask = out["case_id"].isin(small_cases)
            cleaning_log.append(
                CleaningAction(
                    code="drop_single_event_cases",
                    rows_removed=int(case_mask.sum()),
                    cases_removed=int(len(small_cases)),
                )
            )
            out = out.loc[~case_mask].reset_index(drop=True)

    if drop_exact_duplicate_events:
        dup_cols = [c for c in ("case_id", "activity", "timestamp", "resource") if c in out.columns]
        dup_mask = out.duplicated(subset=dup_cols, keep="first")
        if dup_mask.any():
            affected_cases = (
                out.loc[dup_mask, "case_id"].nunique() if "case_id" in out.columns else 0
            )
            cleaning_log.append(
                CleaningAction(
                    code="drop_exact_duplicate_events",
                    rows_removed=int(dup_mask.sum()),
                    cases_removed=int(affected_cases),
                )
            )
            out = out.loc[~dup_mask].reset_index(drop=True)

    new_report = report.model_copy(update={"cleaning_log": cleaning_log, "event_count": len(out)})
    if "case_id" in out.columns:
        new_report.case_count = int(out["case_id"].nunique())
    return out, new_report
