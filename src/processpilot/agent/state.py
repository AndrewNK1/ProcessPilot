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

The edit-operation vocabulary is final for Phase 4: ``redesign/bpmn_edit.py``
will consume these models unchanged.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

Confidence = Literal["low", "medium", "high"]
OperationName = Literal[
    "remove_activity",
    "parallelize_activities",
    "reorder_activities",
    "add_activity",
    "merge_activities",
]
OPERATION_NAMES: tuple[str, ...] = OperationName.__args__  # type: ignore[attr-defined]


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
# Activity semantics, fixed now so Phase 4 can implement them:
# - names in ``activity`` / ``activities`` / ``after`` must exist in the
#   segment's as-is process;
# - ``AddActivity.name`` must NOT exist yet; ``MergeActivities.merged_name``
#   may be new or equal one of the merged activities.


class RemoveActivity(BaseModel):
    """Delete ``activity``; its predecessors connect to its successors."""

    op: Literal["remove_activity"] = "remove_activity"
    activity: str


class ParallelizeActivities(BaseModel):
    """Run ``activities`` (currently sequential) concurrently."""

    op: Literal["parallelize_activities"] = "parallelize_activities"
    activities: list[str] = Field(min_length=2)


class ReorderActivities(BaseModel):
    """Move ``activity`` so that it directly follows ``after``."""

    op: Literal["reorder_activities"] = "reorder_activities"
    activity: str
    after: str


class AddActivity(BaseModel):
    """Insert a new activity ``name`` directly after the existing ``after``."""

    op: Literal["add_activity"] = "add_activity"
    name: str
    after: str


class MergeActivities(BaseModel):
    """Replace ``activities`` with one composite activity ``merged_name``."""

    op: Literal["merge_activities"] = "merge_activities"
    activities: list[str] = Field(min_length=2)
    merged_name: str


EditOperation = Annotated[
    RemoveActivity | ParallelizeActivities | ReorderActivities | AddActivity | MergeActivities,
    Field(discriminator="op"),
]


def existing_activities(op: EditOperation) -> list[str]:
    """Activities ``op`` refers to that must already exist in the as-is process."""
    match op:
        case RemoveActivity():
            return [op.activity]
        case ParallelizeActivities() | MergeActivities():
            return list(op.activities)
        case ReorderActivities():
            return [op.activity, op.after]
        case AddActivity():
            return [op.after]
    raise TypeError(f"unknown edit operation: {op!r}")


def modified_activities(op: EditOperation) -> list[str]:
    """Activities ``op`` removes, moves or rewrites.

    WHY anchors are excluded: inserting a step *after* a protected activity
    leaves it intact, so ``AddActivity.after`` and ``ReorderActivities.after``
    do not count as touching it. Everything else does.
    """
    match op:
        case RemoveActivity() | ReorderActivities():
            return [op.activity]
        case ParallelizeActivities() | MergeActivities():
            return list(op.activities)
        case AddActivity():
            return []
    raise TypeError(f"unknown edit operation: {op!r}")


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


ProposalStatus = Literal["not_implemented_phase4", "applied", "failed"]


class EditResult(BaseModel):
    """Outcome of ``apply_edits`` for one proposal (Phase 4 fills the BPMN path)."""

    proposal_index: int
    status: ProposalStatus
    to_be_bpmn_path: str | None = None
    message: str | None = None


class ProposalValidation(BaseModel):
    """Outcome of ``validate`` for one proposal. ``estimate`` is an ESTIMATE."""

    proposal_index: int
    status: ProposalStatus
    sound: bool | None = None
    estimate: dict[str, float] | None = None
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
    RemoveActivity,
    ParallelizeActivities,
    ReorderActivities,
    AddActivity,
    MergeActivities,
    EditResult,
    ProposalValidation,
    ReviewRecord,
    ValidationIssue,
)
