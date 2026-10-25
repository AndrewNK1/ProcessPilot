"""Typed LangGraph state and the structured outputs the LLM must produce.

WHY pydantic state rather than a loose dict: the state carries the analyst's
constraints and the evidence IDs that proposals must cite. Both are things the
graph has to enforce, and enforcement needs a schema.

Two groups of models live here:

- **LLM output schemas** (``Diagnosis``, ``RedesignSet`` and their parts). The
  model is asked for exactly these via structured output. Field constraints
  (``min_length`` etc.) are checked by pydantic when the output is parsed;
  providers' JSON-schema modes silently drop some of them, so pydantic -- not
  the provider -- is the authority. Everything that needs the evidence store
  or the catalogue (unknown IDs, protected activities, ...) is checked in
  ``agent/validation.py``.
- **Graph state** (``AgentState``) and its bookkeeping records.

The edit-operation vocabulary lives in ``redesign/operations.py``, which
executes it; it is re-exported here because it is part of the LLM's schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from processpilot.redesign.operations import (  # noqa: F401 -- re-exported vocabulary
    OPERATION_NAMES,
    OPERATION_TYPES,
    AddActivity,
    AddCheck,
    AutomateActivity,
    EditOperation,
    MergeActivities,
    ModelDiff,
    OperationName,
    ParallelizeActivities,
    RemoveActivity,
    ReorderActivities,
    existing_activities,
    modified_activities,
)
from processpilot.redesign.simulate import ImpactEstimate
from processpilot.redesign.soundness import SoundnessResult

Confidence = Literal["low", "medium", "high"]


# --- LLM output: diagnosis ---------------------------------------------------


class Hypothesis(BaseModel):
    """One root-cause hypothesis, grounded in cited evidence."""

    description: str = Field(description="The suspected root cause, citing evidence by ID.")
    evidence_ids: list[str] = Field(
        min_length=1, description="Evidence IDs that support this hypothesis."
    )
    confidence: Confidence
    alternative_explanations: list[str] = Field(
        min_length=1,
        description="Other explanations that would produce the same evidence.",
    )
    domain_fact_ids: list[str] = Field(
        default_factory=list,
        description="IDs of domain facts used in the reasoning (not evidence).",
    )


class Diagnosis(BaseModel):
    """Ranked root-cause hypotheses for one segment (most important first)."""

    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=5)


# --- LLM output: redesign ----------------------------------------------------
#
# The edit operations are defined in ``redesign/operations.py`` (the package
# that executes them) and re-exported here: names in ``activity`` /
# ``activities`` / ``after`` must exist in the segment's as-is model; ``name``
# of ``add_activity``/``add_check`` must not exist yet; ``merged_name`` may be
# new or equal one of the merged activities.


class RedesignProposal(BaseModel):
    """One redesign, expressed as catalogue heuristic + typed edit operations."""

    title: str
    heuristic_id: str = Field(description="ID of a heuristic from the catalogue.")
    edit_operations: list[EditOperation] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    rationale: str
    expected_effect: str = Field(
        description="Qualitative expected effect. No numbers that are not in the evidence."
    )
    risks: list[str]
    assumptions: list[str]
    domain_fact_ids: list[str] = Field(default_factory=list)


class RedesignSet(BaseModel):
    """The 2-3 alternative proposals presented to the analyst."""

    proposals: list[RedesignProposal] = Field(min_length=2, max_length=3)


# --- inputs and bookkeeping --------------------------------------------------


class Constraints(BaseModel):
    """Analyst constraints: free text for the prompt, a machine-checked subset."""

    text: list[str] = Field(default_factory=list)
    protected_activities: list[str] = Field(default_factory=list)


class EvidenceRef(BaseModel):
    """What the state keeps of an evidence item: enough to cite, not the metrics."""

    id: str
    type: str
    summary: str


class ValidationIssue(BaseModel):
    """One problem found in an LLM output by code."""

    code: Literal[
        "schema_error",
        "unknown_evidence_id",
        "unknown_domain_fact_id",
        "unknown_heuristic",
        "heuristic_not_editable",
        "unknown_activity",
        "activity_already_exists",
        "protected_activity",
        "edit_not_applicable",
        "unsupported_number",
    ]
    message: str
    path: str = ""


class ReviewDecision(BaseModel):
    """The resume payload of ``human_review``."""

    decision: Literal["approve", "reject"]
    comment: str | None = None


class ReviewRecord(BaseModel):
    """One analyst decision, with the proposals it was about."""

    round: int
    decision: Literal["approve", "reject"]
    comment: str | None
    proposal_titles: list[str]
    decided_at: str


class EditResult(BaseModel):
    """Outcome of ``apply_edits`` for one proposal."""

    proposal_index: int
    status: Literal["applied", "failed"]
    to_be_bpmn_path: str | None = None
    to_be_png_path: str | None = None
    diff: ModelDiff | None = None
    message: str | None = None


class ProposalValidation(BaseModel):
    """Outcome of ``validate`` for one proposal. ``estimate`` is an ESTIMATE."""

    proposal_index: int
    status: Literal["validated", "skipped", "failed"]
    soundness: SoundnessResult | None = None
    estimate: ImpactEstimate | None = None
    message: str | None = None


RunStatus = Literal["running", "awaiting_review", "approved", "rejected", "failed"]


class AgentState(BaseModel):
    """Graph state for one segment's agent run."""

    run_id: str
    agent_run_id: str
    segment: str
    constraints: Constraints = Field(default_factory=Constraints)
    evidence_index: list[EvidenceRef] = Field(default_factory=list)
    diagnosis: Diagnosis | None = None
    proposals: RedesignSet | None = None
    edit_results: list[EditResult] = Field(default_factory=list)
    validation_results: list[ProposalValidation] = Field(default_factory=list)
    review_history: list[ReviewRecord] = Field(default_factory=list)
    revision_count: int = 0
    warnings: list[ValidationIssue] = Field(default_factory=list)
    status: RunStatus = "running"
    error: str | None = None


# Every class that can appear inside a checkpoint; the SQLite checkpointer
# only deserialises these (explicit allowlist instead of "any importable type").
CHECKPOINT_TYPES: tuple[type, ...] = (
    AgentState,
    Constraints,
    EvidenceRef,
    Diagnosis,
    Hypothesis,
    RedesignSet,
    RedesignProposal,
    *OPERATION_TYPES,
    EditResult,
    ModelDiff,
    ProposalValidation,
    SoundnessResult,
    ImpactEstimate,
    ReviewRecord,
    ValidationIssue,
)
