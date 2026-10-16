"""Typed mining settings, parsed once from a dataset preset.

WHY a model instead of passing the preset dict around: every threshold that
shapes a finding must be visible, validated, and recorded with the run. A typo
in a YAML key should fail at load time, not silently fall back to a default
deep inside a mining function.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

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
    max_example_cases: int = Field(default=5, gt=0)


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

    @classmethod
    def from_preset(cls, preset: dict[str, Any]) -> MiningSettings:
        """Pick the mining-relevant blocks out of a full preset dict."""
        return cls(
            discovery=preset["discovery"],
            analysis=preset["analysis"],
            preprocessing=preset.get("preprocessing") or {},
            case_completion=preset.get("case_completion") or {},
            conformance_rules=preset.get("conformance_rules") or [],
        )
