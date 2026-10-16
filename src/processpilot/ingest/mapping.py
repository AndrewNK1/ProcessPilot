"""Column mapping: raw columns -> canonical schema.

WHY suggestions are ranked and never auto-applied: a wrong case-id column
produces a plausible-looking but entirely fictional process model. The target
user is a business analyst, not a data scientist, so the system must show its
reasoning (name pattern + data profile + confidence) and let a human confirm.

XES input is a special case: the standard attributes (``concept:name``,
``time:timestamp``, ``org:resource``, ``case:concept:name``) are unambiguous and
are mapped with confidence 1.0.

Timestamp policy (Phase 0 decision, recorded per-dataset in
``timestamp_timezone_policy``): tz-aware timestamps are converted to UTC and
then made tz-naive, so waiting-time deltas stay correct across DST boundaries.
A tz-naive source timestamp is left exactly as parsed -- there is nothing to
convert, and guessing a timezone would be worse than not guessing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, Field, model_validator

from processpilot.schema import CANONICAL_COLUMNS, sort_canonical

Role = Literal["case_id", "activity", "timestamp", "resource", "segment_by"]

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


class ColumnMapping(BaseModel):
    """The only mapping downstream code accepts. Never guessed silently."""

    case_id: str
    activity: str
    timestamp: str
    resource: str | None = None
    segment_by: str | None = None
    timestamp_format: str | None = None
    timestamp_timezone_policy: Literal["utc", "local", "none"] = "utc"

    @model_validator(mode="after")
    def _roles_point_at_distinct_columns(self) -> ColumnMapping:
        used = [self.case_id, self.activity, self.timestamp]
        if self.resource is not None:
            used.append(self.resource)
        if self.segment_by is not None:
            used.append(self.segment_by)
        if len(used) != len(set(used)):
            raise ValueError(f"mapping roles must point at distinct columns, got {used}")
        return self


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


def _timestamp_parse_rate(series: pd.Series) -> float:
    if pd.api.types.is_datetime64_any_dtype(series):
        return 1.0
    parsed = pd.to_datetime(series, errors="coerce")
    non_null = series.notna().sum()
    if non_null == 0:
        return 0.0
    return float(parsed.notna().sum()) / float(non_null)


def _cardinality_ratio(series: pd.Series) -> float:
    n = len(series)
    if n == 0:
        return 0.0
    return series.nunique(dropna=True) / n


def suggest_mapping(df: pd.DataFrame) -> dict[Role, list[MappingCandidate]]:
    """Rank candidate columns per role using name patterns + data profiling.

    Returns every role's candidates sorted by confidence, descending. Never
    picks one for the caller -- that decision is made by the analyst (or by
    ``load_preset`` for a known dataset), never by this function.
    """
    if "case:concept:name" in df.columns:
        # XES standard attributes present: map with full confidence and skip
        # profiling, since these names are a fixed specification, not a guess.
        result: dict[Role, list[MappingCandidate]] = {}
        for role, column in _XES_STANDARD_MAP.items():
            if column in df.columns:
                result[role] = [
                    MappingCandidate(
                        role=role, column=column, confidence=1.0, reason="XES standard attribute"
                    )
                ]
        for role in ("case_id", "activity", "timestamp", "resource", "segment_by"):
            result.setdefault(role, [])
        _add_profiled_candidates(df, result, exclude_role="segment_by")
        return result

    result = {role: [] for role in ("case_id", "activity", "timestamp", "resource", "segment_by")}
    _add_profiled_candidates(df, result)
    return result


def _add_profiled_candidates(
    df: pd.DataFrame, result: dict[Role, list[MappingCandidate]], exclude_role: Role | None = None
) -> None:
    n_rows = len(df)
    for role in ("case_id", "activity", "timestamp", "resource", "segment_by"):
        if role == exclude_role:
            continue
        already = {c.column for c in result[role]}
        candidates: list[MappingCandidate] = list(result[role])
        for column in df.columns:
            if column in already:
                continue
            name_score = _name_score(column, role)
            if role == "timestamp":
                profile_score = _timestamp_parse_rate(df[column])
                reason = f"name pattern={name_score:.0%}, timestamp-parse rate={profile_score:.0%}"
            elif role == "case_id":
                ratio = _cardinality_ratio(df[column])
                # A case id repeats across events of the same case, so it sits
                # strictly between "unique per row" and "a handful of values".
                profile_score = 1.0 - abs(ratio - 0.3) if n_rows > 0 else 0.0
                profile_score = max(0.0, min(1.0, profile_score))
                reason = f"name pattern={name_score:.0%}, cardinality ratio={ratio:.3f}"
            elif role == "activity":
                ratio = _cardinality_ratio(df[column])
                # Activities repeat heavily: low cardinality ratio is expected.
                profile_score = 1.0 - min(ratio, 1.0)
                reason = f"name pattern={name_score:.0%}, cardinality ratio={ratio:.3f}"
            else:  # resource, segment_by: low-to-moderate cardinality expected
                ratio = _cardinality_ratio(df[column])
                profile_score = 1.0 - min(ratio, 1.0)
                reason = f"name pattern={name_score:.0%}, cardinality ratio={ratio:.3f}"
            confidence = 0.5 * name_score + 0.5 * profile_score
            if confidence > 0.0:
                candidates.append(
                    MappingCandidate(role=role, column=column, confidence=confidence, reason=reason)
                )
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        result[role] = candidates


def load_preset(path: Path | str) -> tuple[ColumnMapping, dict]:
    """Load a dataset preset YAML, returning its ``ColumnMapping`` and the raw dict.

    The raw dict is returned too because presets carry more than a mapping
    (validation thresholds, conformance rules, heuristics constraints) that
    other modules need; this function is the single place that parses the
    YAML so every caller sees the same structure.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    mapping_block = dict(raw["mapping"])
    mapping_block["timestamp_timezone_policy"] = raw.get("timestamp_timezone_policy", "utc")
    return ColumnMapping(**mapping_block), raw


def apply_mapping(df: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    """Produce the canonical dataframe from raw rows plus a confirmed mapping.

    Every source column survives: the mapped roles are renamed to their
    canonical names, and everything else (including unmapped ``case:``/event
    attributes) is kept under an ``attr:<original name>`` prefix so no
    information is silently dropped.
    """
    rename = {
        mapping.case_id: "case_id",
        mapping.activity: "activity",
        mapping.timestamp: "timestamp",
    }
    if mapping.resource is not None:
        rename[mapping.resource] = "resource"
    if mapping.segment_by is not None:
        rename[mapping.segment_by] = "segment"

    mapped_originals = set(rename.keys())
    out = df.rename(columns=rename)
    out = out.rename(
        columns={c: f"attr:{c}" for c in df.columns if c not in mapped_originals}
    )

    # pandas's nullable "string" dtype is used (not plain ``str``/``astype(str)``)
    # specifically so a null case_id/activity stays null instead of becoming the
    # literal text "nan" or "None" -- which would silently defeat the null checks
    # in ingest.validate.
    out["case_id"] = out["case_id"].astype("string")
    out["activity"] = out["activity"].astype("string")

    ts = out["timestamp"]
    if mapping.timestamp_format is not None:
        ts = pd.to_datetime(ts, format=mapping.timestamp_format, errors="coerce")
    elif not pd.api.types.is_datetime64_any_dtype(ts):
        ts = pd.to_datetime(ts, errors="coerce")

    if mapping.timestamp_timezone_policy == "utc" and getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    elif mapping.timestamp_timezone_policy == "local" and getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_localize(None)
    # "none": tz-naive already, or caller explicitly opted out of conversion.
    out["timestamp"] = ts

    if "resource" in out.columns:
        out["resource"] = out["resource"].astype("string")
    if "segment" in out.columns:
        out["segment"] = out["segment"].astype("string")

    ordered = [c for c in CANONICAL_COLUMNS if c in out.columns]
    other = [c for c in out.columns if c not in ordered]
    out = out[ordered + other]

    return sort_canonical(out)


def split_by_segment(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Split a canonical dataframe by its ``segment`` column.

    Returns ``{"<all>": df}`` when there is no ``segment`` column, so callers
    can always iterate the result the same way regardless of whether
    segmenting is configured.
    """
    if "segment" not in df.columns:
        return {"<all>": df}
    segments: dict[str, pd.DataFrame] = {}
    for value, group in df.groupby("segment", dropna=False):
        key = "<missing>" if pd.isna(value) else str(value)
        segments[key] = group.reset_index(drop=True)
    return segments
