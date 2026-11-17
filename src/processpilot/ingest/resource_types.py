"""Resource types: which resource values are people, batch jobs, or no one.

WHY this exists: an ERP log's resource column mixes people with system
accounts and placeholders. In BPI 2019, ``batch_##`` accounts are batch jobs
and ``NONE`` means no user was recorded (mostly vendor-side events). Counting
them as people makes a batch job look like the busiest clerk and a vendor
step look like a handover between colleagues. Every resource finding
(workload, handovers, the Phase 7 manual-work proxy) therefore classifies
first, with one shared function.

The classifier is configured per dataset in the preset's ``resource_types``
block; nothing dataset-specific lives here. A rule is either a regular
expression (a string, matched against the whole value) or a list of exact
values. Empty values (null or blank) are always ``unassigned``: there is no
user to count. Values that match no rule get ``default_type``. Without a
block, every recorded value is ``human`` by default, and the breakdown says
that this was assumed, not configured.

A value matching rules of two types is a configuration error and fails
loudly: picking one silently would decide a thesis number by rule order.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

ResourceType = Literal["human", "batch", "unassigned"]
# Fixed order: evidence numbers RT items by position here (RT-01 = human), so
# an RT id means the same type in every run.
RESOURCE_TYPES: tuple[ResourceType, ...] = ("human", "batch", "unassigned")

# How a value got its type; reported so a defaulted value is never mistaken
# for a configured one.
Basis = Literal["pattern", "values", "default", "empty"]
_BASES: tuple[Basis, ...] = ("pattern", "values", "default", "empty")

_MAX_EXAMPLES = 5
_MAX_AMBIGUOUS_LISTED = 10

Rule = str | list[str] | None


class ResourceTypeSettings(BaseModel):
    """The preset's ``resource_types`` block."""

    model_config = ConfigDict(extra="forbid")

    human: Rule = None
    batch: Rule = None
    unassigned: Rule = None
    default_type: ResourceType = "human"

    @field_validator("human", "batch", "unassigned")
    @classmethod
    def _regex_compiles(cls, rule: Rule) -> Rule:
        if isinstance(rule, str):
            try:
                re.compile(rule)
            except re.error as exc:  # not a ValueError, so pydantic would not wrap it
                raise ValueError(f"invalid regular expression {rule!r}: {exc}") from exc
        return rule

    @property
    def configured(self) -> bool:
        """Whether any rule is set (else every value falls to the default)."""
        return any(getattr(self, t) is not None for t in RESOURCE_TYPES)

    @classmethod
    def from_preset(cls, preset: dict[str, Any]) -> ResourceTypeSettings:
        """The block from a full preset dict; defaults when it is absent."""
        return cls.model_validate(preset.get("resource_types") or {})

    def describe(self) -> str:
        """One clause for evidence text, e.g. how types were decided."""
        if not self.configured:
            return (
                "no resource-type rules are configured, so every recorded resource "
                f"is treated as {self.default_type}"
            )
        parts = []
        for t in RESOURCE_TYPES:
            rule = getattr(self, t)
            if isinstance(rule, str):
                parts.append(f"{t} = values matching {rule}")
            elif isinstance(rule, list):
                parts.append(f"{t} = {', '.join(repr(v) for v in rule)}")
        return "; ".join(parts) + f"; anything else is {self.default_type}"


class ResourceClassification(BaseModel):
    """Type and basis of every distinct resource value (empty values excluded)."""

    types: dict[str, ResourceType]
    basis: dict[str, Basis]


def _is_empty(series: pd.Series) -> pd.Series:
    return series.isna() | (series.astype("string").str.strip() == "")


def _matches(rule: Rule, value: str) -> bool:
    if rule is None:
        return False
    if isinstance(rule, str):
        return re.fullmatch(rule, value) is not None
    return value in rule


def classify_values(values: list[str], settings: ResourceTypeSettings) -> ResourceClassification:
    """Type of each distinct non-empty value. Raises on a value two types claim."""
    types: dict[str, ResourceType] = {}
    basis: dict[str, Basis] = {}
    ambiguous: dict[str, list[str]] = {}
    for value in values:
        hits = [t for t in RESOURCE_TYPES if _matches(getattr(settings, t), value)]
        if len(hits) > 1:
            ambiguous[value] = hits
        elif hits:
            types[value] = hits[0]
            basis[value] = "pattern" if isinstance(getattr(settings, hits[0]), str) else "values"
        else:
            types[value] = settings.default_type
            basis[value] = "default"
    if ambiguous:
        listed = sorted(ambiguous.items())[:_MAX_AMBIGUOUS_LISTED]
        detail = "; ".join(f"{v!r} matches {', '.join(t)}" for v, t in listed)
        raise ValueError(
            f"resource_types: {len(ambiguous)} resource value(s) match rules of more than "
            f"one type ({detail}); make the rules exclusive in the preset"
        )
    return ResourceClassification(types=types, basis=basis)


def classify_resources(
    resource: pd.Series, settings: ResourceTypeSettings
) -> tuple[pd.Series, pd.Series]:
    """Per row: resource type and the basis it was decided on.

    Classifies each distinct value once and maps back, so a 1.6 M-row log
    costs one pass per distinct value (~600 in BPI 2019), not per row.
    """
    empty = _is_empty(resource)
    values = sorted(resource.loc[~empty].astype(str).unique())
    c = classify_values(values, settings)
    as_str = resource.astype("string")
    types = as_str.map(c.types).astype(object)
    basis = as_str.map(c.basis).astype(object)
    types = types.where(~empty, "unassigned")
    basis = basis.where(~empty, "empty")
    return types.rename("resource_type"), basis.rename("resource_type_basis")


class ResourceTypeCount(BaseModel):
    """One type's share of the log."""

    type: ResourceType
    event_count: int
    event_share: float
    case_count: int
    case_share: float
    distinct_resources: int
    # Most frequent values first; empty values are shown as "(empty)".
    example_values: list[str]
    events_by_basis: dict[str, int]


class ResourceTypeBreakdown(BaseModel):
    """Data-quality view of the classification: every type, zero or not."""

    settings: ResourceTypeSettings
    configured: bool
    rule_text: str
    event_count: int
    case_count: int
    types: list[ResourceTypeCount] = Field(default_factory=list)
    defaulted_event_count: int
    defaulted_values: list[str] = Field(default_factory=list)


def resource_type_breakdown(
    df: pd.DataFrame, settings: ResourceTypeSettings
) -> ResourceTypeBreakdown | None:
    """Events, cases and example values per type; ``None`` without a resource column."""
    if "resource" not in df.columns:
        return None
    types, basis = classify_resources(df["resource"], settings)
    n_events = len(df)
    n_cases = int(df["case_id"].nunique())
    shown = df["resource"].astype("string").where(~_is_empty(df["resource"]), "(empty)")
    counts = []
    for t in RESOURCE_TYPES:
        mask = types == t
        values = shown.loc[mask].value_counts(sort=True)
        # value_counts ties keep no stable order; sort by (-count, value).
        ordered = sorted(values.items(), key=lambda kv: (-kv[1], str(kv[0])))
        cases = int(df.loc[mask, "case_id"].nunique())
        by_basis = basis.loc[mask].value_counts()
        counts.append(
            ResourceTypeCount(
                type=t,
                event_count=int(mask.sum()),
                event_share=float(mask.sum()) / n_events if n_events else 0.0,
                case_count=cases,
                case_share=cases / n_cases if n_cases else 0.0,
                distinct_resources=len(values),
                example_values=[str(v) for v, _ in ordered[:_MAX_EXAMPLES]],
                events_by_basis={b: int(by_basis.get(b, 0)) for b in _BASES if by_basis.get(b)},
            )
        )
    defaulted = basis == "default"
    return ResourceTypeBreakdown(
        settings=settings,
        configured=settings.configured,
        rule_text=settings.describe(),
        event_count=n_events,
        case_count=n_cases,
        types=counts,
        defaulted_event_count=int(defaulted.sum()),
        defaulted_values=sorted(df.loc[defaulted, "resource"].astype(str).unique())[:_MAX_EXAMPLES],
    )
