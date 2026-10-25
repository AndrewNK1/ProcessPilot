"""Column mapping: raw columns -> canonical schema.

WHY suggestions are ranked and never auto-applied: a wrong case-id column
produces a plausible-looking but entirely fictional process model. The target
user is a business analyst, not a data scientist, so the system must show its
reasoning (name pattern + data profile + confidence) and let a human confirm.

XES input is a special case: the standard attributes (``concept:name``,
``time:timestamp``, ``org:resource``, ``case:concept:name``) are unambiguous and
are mapped with confidence 1.0. Only the roles XES has no standard for (notably
``segment_by``) are profiled.

Timestamp policy (Phase 0 decision, recorded per-dataset in
``timestamp_timezone_policy``): tz-aware timestamps are converted to UTC and
then made tz-naive, so waiting-time deltas stay correct across DST boundaries.
Under ``utc``, text timestamps carrying *different* offsets (normal around a
DST switch) are reconciled the same way. A tz-naive source timestamp is left
exactly as parsed -- there is nothing to convert, and guessing a timezone would
be worse than not guessing.

Timestamps that cannot be converted become NaT, but never silently: their
original value is kept in ``timestamp_raw`` so ``validate`` reports them as
unparseable rather than as missing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from processpilot.config import load_preset_yaml
from processpilot.ingest.validate import segment_mixed_cases
from processpilot.schema import (
    CANONICAL_COLUMNS,
    TIMESTAMP_DTYPE,
    TIMESTAMP_RAW,
    sort_canonical,
)

Role = Literal["case_id", "activity", "timestamp", "resource", "segment_by"]
TimezonePolicy = Literal["utc", "local", "none"]
_ROLES: tuple[Role, ...] = ("case_id", "activity", "timestamp", "resource", "segment_by")

# Name-pattern heuristics per role. Checked case-insensitively, substring match.
_NAME_PATTERNS: dict[Role, tuple[str, ...]] = {
    "case_id": ("case", "order", "po_number", "po number", "purchase order", "trace"),
    "activity": ("activity", "step", "event", "task", "concept:name"),
    "timestamp": ("time", "date", "timestamp"),
    "resource": ("user", "resource", "org:resource", "employee", "owner"),
    "segment_by": ("category", "segment", "type", "class"),
}

# XES standard attributes map unambiguously -- no profiling needed.
_XES_STANDARD_MAP: dict[Role, str] = {
    "case_id": "case:concept:name",
    "activity": "concept:name",
    "timestamp": "time:timestamp",
    "resource": "org:resource",
}


class SuggestionHeuristics(BaseModel):
    """Tunable weights for ``suggest_mapping``; defaults, never hidden constants.

    WHY a model: these numbers shape what an analyst is shown first, so they
    are part of the method and must be visible and overridable, not buried in
    an expression.
    """

    name_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    profile_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    # Typical events-per-case in event logs is ~3-10, i.e. a distinct-case-id
    # ratio of ~0.1-0.3; candidates are scored by distance from this target.
    case_id_target_cardinality: float = Field(default=0.3, gt=0.0, lt=1.0)
    # Timestamp parse-rate profiling parses this many sampled rows, not the
    # full column: parsing every column of a 1.6M-row log takes minutes.
    timestamp_profile_sample_rows: int = Field(default=10_000, gt=0)
    sample_seed: int = 0


class ColumnMapping(BaseModel):
    """The only mapping downstream code accepts. Never guessed silently."""

    case_id: str
    activity: str
    timestamp: str
    resource: str | None = None
    segment_by: str | None = None
    timestamp_format: str | None = None
    timestamp_timezone_policy: TimezonePolicy = "utc"

    @model_validator(mode="after")
    def _roles_point_at_distinct_columns(self) -> ColumnMapping:
        used = self.mapped_columns()
        if len(used) != len(set(used)):
            raise ValueError(f"mapping roles must point at distinct columns, got {used}")
        return self

    def mapped_columns(self) -> list[str]:
        """Source column names this mapping uses, in role order."""
        used = [self.case_id, self.activity, self.timestamp]
        if self.resource is not None:
            used.append(self.resource)
        if self.segment_by is not None:
            used.append(self.segment_by)
        return used


class MappingCandidate(BaseModel):
    """One ranked suggestion for a single role."""

    role: Role
    column: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


def _name_score(column: str, role: Role) -> float:
    lowered = column.lower()
    patterns = _NAME_PATTERNS[role]
    return 1.0 if any(re.search(re.escape(p), lowered) for p in patterns) else 0.0


def _timestamp_parse_rate(series: pd.Series, heuristics: SuggestionHeuristics) -> float:
    if pd.api.types.is_datetime64_any_dtype(series):
        return 1.0
    # Numbers and booleans are never treated as timestamps: pandas would read an
    # integer like an order number as nanoseconds since 1970 and "parse" 100%.
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        return 0.0
    non_null = series.dropna()
    if non_null.empty:
        return 0.0
    if len(non_null) > heuristics.timestamp_profile_sample_rows:
        non_null = non_null.sample(
            heuristics.timestamp_profile_sample_rows, random_state=heuristics.sample_seed
        )
    # format="mixed" parses element-wise: right for *measuring* parseability of
    # an unknown column, and avoids pandas' format-inference warning.
    parsed = pd.to_datetime(non_null, errors="coerce", format="mixed", utc=True)
    return float(parsed.notna().mean())


def _cardinality_ratio(series: pd.Series) -> float:
    n = len(series)
    if n == 0:
        return 0.0
    return series.nunique(dropna=True) / n


def suggest_mapping(
    df: pd.DataFrame, heuristics: SuggestionHeuristics | None = None
) -> dict[Role, list[MappingCandidate]]:
    """Rank candidate columns per role using name patterns + data profiling.

    Returns every role's candidates sorted by confidence, descending. Never
    picks one for the caller -- that decision is made by the analyst (or by
    ``load_preset`` for a known dataset), never by this function.
    """
    heuristics = heuristics or SuggestionHeuristics()
    result: dict[Role, list[MappingCandidate]] = {role: [] for role in _ROLES}

    claimed: set[str] = set()
    if "case:concept:name" in df.columns:
        # XES standard attributes present: map them with full confidence, since
        # these names are a fixed specification, not a guess.
        for role, column in _XES_STANDARD_MAP.items():
            if column in df.columns:
                result[role] = [
                    MappingCandidate(
                        role=role, column=column, confidence=1.0, reason="XES standard attribute"
                    )
                ]
                claimed.add(column)

    # Profile only the roles still without a certain answer, and never offer a
    # column already claimed by a standard attribute for a different role.
    roles_to_profile = [role for role in _ROLES if not result[role]]
    for role in roles_to_profile:
        result[role] = _profiled_candidates(df, role, claimed, heuristics)
    return result


def _profiled_candidates(
    df: pd.DataFrame, role: Role, excluded: set[str], heuristics: SuggestionHeuristics
) -> list[MappingCandidate]:
    candidates: list[MappingCandidate] = []
    for column in df.columns:
        if column in excluded:
            continue
        name_score = _name_score(column, role)
        if role == "timestamp":
            profile_score = _timestamp_parse_rate(df[column], heuristics)
            reason = f"name pattern={name_score:.0%}, timestamp-parse rate={profile_score:.0%}"
        else:
            ratio = _cardinality_ratio(df[column])
            if role == "case_id":
                # A case id repeats across events of the same case, so it sits
                # between "unique per row" and "a handful of values".
                target = heuristics.case_id_target_cardinality
                profile_score = max(0.0, min(1.0, 1.0 - abs(ratio - target)))
            else:
                # activity, resource, segment_by: values repeat heavily.
                profile_score = 1.0 - min(ratio, 1.0)
            reason = f"name pattern={name_score:.0%}, cardinality ratio={ratio:.3f}"
        confidence = heuristics.name_weight * name_score + heuristics.profile_weight * profile_score
        confidence = max(0.0, min(1.0, confidence))
        if confidence > 0.0:
            candidates.append(
                MappingCandidate(role=role, column=column, confidence=confidence, reason=reason)
            )
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


def load_preset(name_or_path: Path | str) -> tuple[ColumnMapping, dict[str, Any]]:
    """Load a dataset preset, returning its ``ColumnMapping`` and the raw dict.

    Accepts a preset name (``"bpi2019"``) or a YAML path. The raw dict is
    returned too because presets carry more than a mapping (validation
    thresholds, conformance rules, heuristics constraints) that other modules
    need.
    """
    raw = load_preset_yaml(name_or_path)
    mapping_block = dict(raw["mapping"])
    mapping_block["timestamp_timezone_policy"] = raw.get("timestamp_timezone_policy", "utc")
    return ColumnMapping(**mapping_block), raw


def _to_canonical_timestamps(raw: pd.Series, fmt: str | None, policy: TimezonePolicy) -> pd.Series:
    """Convert a source timestamp column to tz-naive ``datetime64[ns]``.

    Values that cannot be converted become NaT; the caller detects them by
    comparing against the source column, so nothing is lost silently.
    """
    if pd.api.types.is_datetime64_any_dtype(raw):
        ts = raw
    else:
        kwargs: dict[str, Any] = {"errors": "coerce"}
        if fmt is not None:
            kwargs["format"] = fmt
        if policy == "utc":
            # utc=True is what reconciles mixed offsets (e.g. +01:00 and +02:00
            # across a DST switch); naive strings are read as already-UTC.
            ts = pd.to_datetime(raw, utc=True, **kwargs)
        else:
            try:
                ts = pd.to_datetime(raw, **kwargs)
            except ValueError as exc:
                raise ValueError(
                    "timestamp column mixes UTC offsets; only "
                    "timestamp_timezone_policy='utc' can reconcile them"
                ) from exc

    if getattr(ts.dt, "tz", None) is not None:
        if policy == "utc":
            ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
        elif policy == "local":
            ts = ts.dt.tz_localize(None)
        else:
            raise ValueError(
                "timestamp_timezone_policy='none' requires tz-naive source timestamps; "
                "this column is tz-aware -- use 'utc' or 'local'"
            )

    # datetime64[ns] only spans 1677-2262. ERP sentinel dates such as
    # 9999-12-31 fall outside it; they become NaT here (and are reported as
    # unparseable by validate) instead of crashing the cast.
    out_of_ns_range = ts.notna() & ((ts < pd.Timestamp.min) | (ts > pd.Timestamp.max))
    return ts.where(~out_of_ns_range).astype(TIMESTAMP_DTYPE)


def apply_mapping(df: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    """Produce the canonical dataframe from raw rows plus a confirmed mapping.

    Every source column survives: the mapped roles are renamed to their
    canonical names, and everything else (including unmapped ``case:``/event
    attributes) is kept under an ``attr:<original name>`` prefix so no
    information is silently dropped.
    """
    missing = [c for c in mapping.mapped_columns() if c not in df.columns]
    if missing:
        raise ValueError(
            f"mapped column(s) not found in the source data: {missing}; "
            f"available columns: {list(df.columns)}"
        )

    role_rename = {
        mapping.case_id: "case_id",
        mapping.activity: "activity",
        mapping.timestamp: "timestamp",
    }
    if mapping.resource is not None:
        role_rename[mapping.resource] = "resource"
    if mapping.segment_by is not None:
        role_rename[mapping.segment_by] = "segment"
    # One rename over the *original* names, so an unmapped source column that
    # happens to be called e.g. "case_id" becomes "attr:case_id" without
    # colliding with the mapped role.
    rename = {c: role_rename.get(c, f"attr:{c}") for c in df.columns}
    out = df.rename(columns=rename)

    # pandas's nullable "string" dtype is used (not plain ``str``/``astype(str)``)
    # specifically so a null case_id/activity stays null instead of becoming the
    # literal text "nan" or "None" -- which would silently defeat the null checks
    # in ingest.validate.
    out["case_id"] = out["case_id"].astype("string")
    out["activity"] = out["activity"].astype("string")

    raw_ts = out["timestamp"]
    ts = _to_canonical_timestamps(
        raw_ts, mapping.timestamp_format, mapping.timestamp_timezone_policy
    )
    failed = raw_ts.notna() & ts.isna()
    out["timestamp"] = ts
    if failed.any():
        out[TIMESTAMP_RAW] = raw_ts.astype("string").where(failed)

    if "resource" in out.columns:
        out["resource"] = out["resource"].astype("string")
    if "segment" in out.columns:
        out["segment"] = out["segment"].astype("string")

    ordered = [c for c in (*CANONICAL_COLUMNS, TIMESTAMP_RAW) if c in out.columns]
    other = [c for c in out.columns if c not in ordered]
    out = out[ordered + other]

    return sort_canonical(out)


def split_by_segment(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Split a canonical dataframe by its ``segment`` column.

    Returns ``{"<all>": df}`` when there is no ``segment`` column, so callers
    can always iterate the result the same way regardless of whether
    segmenting is configured. Raises if a case has more than one segment
    value: splitting it would cut its trace apart (``validate`` reports the
    same condition as ``segment_varies_within_case``).
    """
    if "segment" not in df.columns:
        return {"<all>": df}
    mixed = segment_mixed_cases(df)
    if mixed:
        raise ValueError(
            f"{len(mixed)} case(s) have more than one segment value (e.g. {mixed[:3]}); "
            "segment_by must name a case-level attribute"
        )
    segments: dict[str, pd.DataFrame] = {}
    for value, group in df.groupby("segment", dropna=False):
        key = "<missing>" if pd.isna(value) else str(value)
        segments[key] = group.reset_index(drop=True)
    return segments
