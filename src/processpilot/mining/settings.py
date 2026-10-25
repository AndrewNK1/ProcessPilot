"""Typed mining settings, parsed once from a dataset preset.

WHY a model instead of passing the preset dict around: every threshold that
shapes a finding must be visible, validated, and recorded with the run. A typo
in a YAML key should fail at load time, not silently fall back to a default
deep inside a mining function.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ConformanceRuleType = Literal["activity_a_before_activity_b", "activity_must_occur"]


class DiscoverySettings(BaseModel):
    """Process-model discovery knobs."""

    inductive_miner_noise_threshold: float = Field(ge=0.0, le=1.0)
    # Edges below this share of cases are left out of the exported DFG view
    # (the count left out is recorded); bottleneck ranking still sees all edges.
    dfg_min_edge_frequency_share: float = Field(ge=0.0, le=1.0)


class AnalysisSettings(BaseModel):
    """How many ranked items per evidence type are kept."""

    top_n_bottlenecks: int = Field(gt=0)
    top_n_variants: int = Field(gt=0)
    top_n_rework: int = Field(gt=0)
    top_n_resources: int = Field(gt=0)
    top_n_handovers: int = Field(gt=0)
    top_n_activity_waits: int = Field(gt=0)
    top_n_model_deviations: int = Field(gt=0)
    max_example_cases: int = Field(default=5, gt=0)


VariantFilterMode = Literal["none", "top_k", "coverage", "min_case_share"]


class VariantFilterSettings(BaseModel):
    """Which variants to keep, and for which part of the analysis.

    WHY ``apply_to`` defaults to ``discovery``: filtering exists to make the
    as-is model readable. Applied to everything, it would also hide rare cases
    from the bottleneck, rework and conformance findings -- exactly the cases
    an analyst may need to see -- so that is an explicit opt-in.

    Modes: ``top_k`` keeps the ``value`` most frequent variants; ``coverage``
    keeps the most frequent variants until they cover ``value`` (0-1] of
    cases; ``min_case_share`` keeps variants followed by at least ``value`` of
    cases. Ties follow the variant ranking (case count, then sequence).
    """

    mode: VariantFilterMode = "none"
    value: float | None = None
    apply_to: Literal["discovery", "all"] = "discovery"

    @model_validator(mode="after")
    def _value_fits_mode(self) -> VariantFilterSettings:
        if self.mode == "none":
            if self.value is not None:
                raise ValueError("variant filter mode 'none' takes no value")
            return self
        if self.value is None:
            raise ValueError(f"variant filter mode {self.mode!r} needs a value")
        if self.mode == "top_k" and (self.value < 1 or not float(self.value).is_integer()):
            raise ValueError("top_k needs a whole number >= 1")
        if self.mode in ("coverage", "min_case_share") and not 0 < self.value <= 1:
            raise ValueError(f"{self.mode} needs a share in (0, 1]")
        return self


class ReferenceModelSettings(BaseModel):
    """Reference (to-be-followed) BPMN models, as paths relative to the project root.

    WHY optional and per segment: a public log rarely ships with its intended
    process, and segments of one log follow different processes. When none is
    configured, the evidence says so instead of staying silent.
    """

    default: str | None = None
    per_segment: dict[str, str | None] = Field(default_factory=dict)

    def path_for(self, segment: str) -> str | None:
        """Segment-specific path (``None`` disables it), else the default."""
        return self.per_segment.get(segment, self.default)


class PreprocessingSettings(BaseModel):
    """Mining-time view of the log; never changes the validated canonical log."""

    # Same case + same activity + same instant = one batch posting, not a
    # repetition. Collapsing stops it from creating zero-wait self-loops and
    # phantom rework. The number collapsed is reported as KPI evidence.
    collapse_same_instant_repeats: bool = True


class CaseCompletionSettings(BaseModel):
    """Which last activities mean "this case had finished when the log was cut".

    WHY: an "activity must occur" rule cannot distinguish a breakdown from a
    case that was simply still running. Violations are therefore reported for
    all cases and for completed cases only.
    """

    end_activities: list[str] = Field(default_factory=list)
    per_segment: dict[str, list[str]] = Field(default_factory=dict)

    def end_activities_for(self, segment: str) -> list[str]:
        """Segment-specific end activities, falling back to the default list."""
        return self.per_segment.get(segment, self.end_activities)


class ConformanceRule(BaseModel):
    """One declarative rule from the preset."""

    id: str
    name: str
    type: ConformanceRuleType
    description: str
    params: dict[str, str]
    applies_to_segments: list[str] | None = None

    def applies_to(self, segment: str) -> bool:
        """``None`` means every segment, including the unsegmented ``<all>``."""
        return self.applies_to_segments is None or segment in self.applies_to_segments


class MiningSettings(BaseModel):
    """Everything the mining engine needs from a preset."""

    discovery: DiscoverySettings
    analysis: AnalysisSettings
    preprocessing: PreprocessingSettings = Field(default_factory=PreprocessingSettings)
    case_completion: CaseCompletionSettings = Field(default_factory=CaseCompletionSettings)
    conformance_rules: list[ConformanceRule] = Field(default_factory=list)
    variant_filter: VariantFilterSettings = Field(default_factory=VariantFilterSettings)
    reference_models: ReferenceModelSettings = Field(default_factory=ReferenceModelSettings)

    @classmethod
    def from_preset(cls, preset: dict[str, Any]) -> MiningSettings:
        """Pick the mining-relevant blocks out of a full preset dict."""
        return cls(
            discovery=preset["discovery"],
            analysis=preset["analysis"],
            preprocessing=preset.get("preprocessing") or {},
            case_completion=preset.get("case_completion") or {},
            conformance_rules=preset.get("conformance_rules") or [],
            variant_filter=preset.get("variant_filter") or {},
            reference_models=preset.get("reference_models") or {},
        )
