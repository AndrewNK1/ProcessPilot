"""Graph nodes.

WHY the evidence-ID validator sits in the node and not in the prompt: prompts
are advisory, validators are not. Every LLM output goes through
``_call_validated``: parse -> validate in code -> on any issue, retry with the
issues fed back (max ``max_retries``). When retries run out the node fails and
the run is finalised as "failed" with the reason in the log -- with one
exception decided by the user: if the only remaining issues are unsupported
numbers, the output is kept and the issues are attached as visible warnings.

Nodes: ``load_evidence``, ``diagnose``, ``redesign``, ``apply_edits``,
``validate``, ``human_review`` (LangGraph ``interrupt()``), ``finalize``.
``load_evidence``, ``apply_edits``, ``validate`` and ``finalize`` make no LLM
call. Every node takes the state plus an ``AgentContext`` (bound by the graph)
and returns a partial state update.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd
from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from processpilot.agent.catalogue import AgentSettings, DomainKnowledge, HeuristicCatalogue
from processpilot.agent.llm import (
    ChatTurn,
    StructuredLLM,
    bare_model_name,
    cost_usd,
    model_fingerprint,
)
from processpilot.agent.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    diagnose_prompt,
    redesign_prompt,
    render_digest,
    retry_prompt,
)
from processpilot.agent.runlog import RunLogger, utc_now
from processpilot.agent.state import (
    AgentState,
    Diagnosis,
    EditResult,
    EvidenceRef,
    ProposalValidation,
    RedesignSet,
    ReviewDecision,
    ReviewRecord,
    ValidationIssue,
)
from processpilot.agent.tools import EvidenceTools
from processpilot.agent.validation import (
    ValidationContext,
    validate_diagnosis,
    validate_redesign,
)
from processpilot.mining.run import CASE_WAITS_FILE, library_versions
from processpilot.process_tree import TreeNode
from processpilot.redesign.bpmn_edit import apply_proposal, write_to_be
from processpilot.redesign.operations import EditError
from processpilot.redesign.simulate import estimate_impact
from processpilot.redesign.soundness import check_soundness

M = TypeVar("M", bound=BaseModel)

_AGENT_PACKAGES = (
    "processpilot",
    "pydantic",
    "langgraph",
    "langgraph-checkpoint",
    "langgraph-checkpoint-sqlite",
    "langchain",
    "langchain-core",
    "langchain-anthropic",
    "anthropic",
    "langchain-ollama",
    "ollama",
    "pm4py",
    "pulp",
)


@dataclass(frozen=True)
class AgentContext:
    """Everything a node needs that is not state: inputs, model, logger.

    ``activities`` are the as-is model's (what an edit may name);
    ``observed_activities`` are everything mining saw, including activities
    the model left out as rare. ``segment_dir`` holds the mined artefacts the
    redesign steps read (``as_is_tree.json``, ``case_waits.parquet``).
    """

    run_id: str
    segment: str
    tools: EvidenceTools
    activities: list[str]
    catalogue: HeuristicCatalogue
    domain: DomainKnowledge | None
    settings: AgentSettings
    llm: StructuredLLM
    logger: RunLogger
    segment_dir: Path | None = None
    as_is_tree: TreeNode | None = None
    observed_activities: list[str] = field(default_factory=list)

    @property
    def unmodelled_activities(self) -> list[str]:
        """Observed in the log but not part of the as-is model."""
        return sorted(set(self.observed_activities) - set(self.activities))

    def validation_context(self, state: AgentState) -> ValidationContext:
        """Checks are bound to this segment and the run's constraints."""
        return ValidationContext(
            segment=self.segment,
            store=self.tools.segment_store(),
            activities=frozenset(self.activities),
            catalogue=self.catalogue,
            domain=self.domain,
            protected_activities=frozenset(state.constraints.protected_activities),
            as_is_tree=self.as_is_tree,
            observed_activities=frozenset(self.observed_activities),
        )


class NodeFailure(Exception):
    """An LLM step could not produce a valid output within its retries."""

    def __init__(self, node: str, issues: list[ValidationIssue], detail: str = "") -> None:
        """Keep the issues for the log and the state."""
        self.node = node
        self.issues = issues
        listed = "; ".join(f"[{i.code}] {i.path}: {i.message}" for i in issues)
        super().__init__(f"{node} failed: {detail or listed}")


def _schema_issues(error: ValidationError | str) -> list[ValidationIssue]:
    if isinstance(error, str):
        return [ValidationIssue(code="schema_error", message=error)]
    return [
        ValidationIssue(
            code="schema_error",
            path=".".join(str(p) for p in e["loc"]),
            message=e["msg"],
        )
        for e in error.errors()
    ]


def _call_validated(
    ctx: AgentContext,
    node: str,
    turns: list[ChatTurn],
    schema: type[M],
    check: Callable[[M], list[ValidationIssue]],
) -> tuple[M, list[ValidationIssue]]:
    """Call the model until ``check`` passes; returns (output, warnings)."""
    turns = list(turns)
    price = ctx.settings.pricing_usd_per_mtok.get(bare_model_name(ctx.llm.model_id))
    for attempt in range(ctx.settings.max_retries + 1):
        started = utc_now()
        try:
            resp = ctx.llm.invoke(turns, schema)
        except Exception as exc:  # transport/auth errors: the SDK already retried
            ctx.logger.event("llm_error", node=node, attempt=attempt, error=repr(exc))
            raise NodeFailure(node, [], detail=f"model call raised {exc!r}") from exc

        parsed: M | None = None
        if resp.parse_error is not None:
            issues = _schema_issues(resp.parse_error)
        else:
            try:
                parsed = schema.model_validate(resp.parsed)
                issues = check(parsed)
            except ValidationError as exc:
                issues = _schema_issues(exc)

        ctx.logger.event(
            "llm_call",
            node=node,
            attempt=attempt,
            prompt_version=PROMPT_VERSION,
            model_id=ctx.llm.model_id,
            served_model=resp.served_model,
            started_at=started,
            finished_at=utc_now(),
            prompt=[t.model_dump() for t in turns],
            raw_text=resp.raw_text,
            raw_content=resp.raw_content,
            stop_reason=resp.stop_reason,
            parsed=resp.parsed,
            issues=[i.model_dump() for i in issues],
            usage=resp.usage.model_dump(),
            cost_usd=cost_usd(resp.usage, price),
        )
        if not issues and parsed is not None:
            return parsed, []

        if attempt == ctx.settings.max_retries:
            if parsed is not None and all(i.code == "unsupported_number" for i in issues):
                ctx.logger.event(
                    "accepted_with_warnings", node=node, issues=[i.model_dump() for i in issues]
                )
                return parsed, issues
            ctx.logger.event("node_failed", node=node, issues=[i.model_dump() for i in issues])
            raise NodeFailure(node, issues)

        ctx.logger.event("retry", node=node, attempt=attempt + 1, issue_count=len(issues))
        turns += [
            ChatTurn(role="assistant", content=resp.raw_text or "(no output)"),
            ChatTurn(role="user", content=retry_prompt(issues)),
        ]
    raise AssertionError("unreachable")  # pragma: no cover


def _failed(exc: NodeFailure) -> dict[str, Any]:
    return {"status": "failed", "error": str(exc)}


# --- nodes ---------------------------------------------------------------------


def load_evidence(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Index the segment's evidence and record the run's inputs. No LLM."""
    items = ctx.tools.all_items()
    index = [EvidenceRef(id=i.id, type=i.type, summary=i.summary) for i in items]
    missing_protected = [
        a for a in state.constraints.protected_activities if a not in ctx.activities
    ]
    ctx.logger.write_json(
        "inputs.json",
        {
            "run_id": state.run_id,
            "agent_run_id": state.agent_run_id,
            "segment": state.segment,
            "model_id": ctx.llm.model_id,
            "model_fingerprint": model_fingerprint(ctx.llm.model_id),
            "model_settings": ctx.settings.model.model_dump(),
            "max_retries": ctx.settings.max_retries,
            "max_revision_loops": ctx.settings.max_revision_loops,
            "prompt_version": PROMPT_VERSION,
            "system_prompt": SYSTEM_PROMPT,
            "constraints": state.constraints.model_dump(),
            "protected_activities_not_in_segment": missing_protected,
            "evidence_ids": [r.id for r in index],
            "activities": ctx.activities,
            "heuristics_catalogue_version": ctx.catalogue.version,
            "domain_knowledge": None
            if ctx.domain is None
            else {"domain": ctx.domain.domain, "version": ctx.domain.version},
            "library_versions": library_versions(_AGENT_PACKAGES),
            "started_at": utc_now(),
        },
    )
    ctx.logger.event("node", node="load_evidence", evidence_count=len(index))
    return {"evidence_index": index, "status": "running"}


def diagnose(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """LLM: root-cause hypotheses from the evidence digest."""
    vctx = ctx.validation_context(state)
    turns = [
        ChatTurn(role="system", content=SYSTEM_PROMPT),
        ChatTurn(
            role="user",
            content=diagnose_prompt(
                ctx.segment,
                ctx.activities,
                render_digest(ctx.tools),
                ctx.domain,
                ctx.unmodelled_activities,
            ),
        ),
    ]
    try:
        diagnosis, warnings = _call_validated(
            ctx, "diagnose", turns, Diagnosis, lambda d: validate_diagnosis(d, vctx)
        )
    except NodeFailure as exc:
        return _failed(exc)
    ctx.logger.write_json("diagnosis.json", diagnosis.model_dump())
    return {"diagnosis": diagnosis, "warnings": [*state.warnings, *warnings]}


def redesign(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """LLM: 2-3 catalogue-bound proposals; on revision, with the analyst's comment."""
    assert state.diagnosis is not None, "redesign requires a diagnosis"
    vctx = ctx.validation_context(state)
    revising = state.revision_count > 0 and bool(state.review_history)
    turns = [
        ChatTurn(role="system", content=SYSTEM_PROMPT),
        ChatTurn(
            role="user",
            content=redesign_prompt(
                ctx.segment,
                ctx.activities,
                render_digest(ctx.tools),
                ctx.domain,
                state.diagnosis,
                ctx.catalogue.editable(),
                state.constraints,
                previous=state.proposals if revising else None,
                analyst_comment=state.review_history[-1].comment if revising else None,
                unmodelled=ctx.unmodelled_activities,
            ),
        ),
    ]
    try:
        proposals, warnings = _call_validated(
            ctx, "redesign", turns, RedesignSet, lambda r: validate_redesign(r, vctx)
        )
    except NodeFailure as exc:
        return _failed(exc)
    ctx.logger.write_json(f"proposals_r{state.revision_count}.json", proposals.model_dump())
    return {"proposals": proposals, "warnings": [*state.warnings, *warnings]}


def _to_be_tree(state: AgentState, ctx: AgentContext, index: int) -> TreeNode:
    """Re-derive a proposal's to-be tree (deterministic, so not stored in state)."""
    assert state.proposals is not None and ctx.as_is_tree is not None
    ops = state.proposals.proposals[index].edit_operations
    to_be, _ = apply_proposal(
        ctx.as_is_tree, ops, frozenset(state.constraints.protected_activities)
    )
    return to_be


def apply_edits(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Pure code: apply each proposal to the as-is model, write its to-be BPMN + PNG.

    Validation already trial-applied every proposal, so a failure here means
    the as-is model is missing or changed; it is recorded per proposal rather
    than raised, and ``validate`` skips that proposal.
    """
    assert state.proposals is not None
    results: list[EditResult] = []
    for i, proposal in enumerate(state.proposals.proposals):
        if ctx.as_is_tree is None:
            results.append(
                EditResult(
                    proposal_index=i,
                    status="failed",
                    message="no as-is model (as_is_tree.json) for this segment; re-run mining",
                )
            )
            continue
        try:
            to_be, diff = apply_proposal(
                ctx.as_is_tree,
                proposal.edit_operations,
                frozenset(state.constraints.protected_activities),
            )
        except EditError as exc:
            results.append(EditResult(proposal_index=i, status="failed", message=str(exc)))
            continue
        stem = f"to_be_r{state.revision_count}_p{i + 1}"
        bpmn_path, png_path = write_to_be(to_be, ctx.logger.directory, stem)
        results.append(
            EditResult(
                proposal_index=i,
                status="applied",
                to_be_bpmn_path=str(bpmn_path),
                to_be_png_path=str(png_path) if png_path else None,
                diff=diff,
            )
        )
    ctx.logger.event(
        "node", node="apply_edits", results=[r.model_dump(exclude={"diff"}) for r in results]
    )
    return {"edit_results": results}


def validate(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Pure code: soundness + replay ESTIMATE for every applied proposal."""
    assert state.proposals is not None
    waits = _case_waits(ctx)
    rs = ctx.settings.redesign
    results: list[ProposalValidation] = []
    for edit in state.edit_results:
        i = edit.proposal_index
        if edit.status != "applied":
            results.append(
                ProposalValidation(
                    proposal_index=i, status="skipped", message="edits were not applied"
                )
            )
            continue
        soundness = check_soundness(_to_be_tree(state, ctx, i), rs.woflan_max_transitions)
        estimate = (
            estimate_impact(waits, state.proposals.proposals[i].edit_operations, rs.estimate)
            if waits is not None
            else None
        )
        results.append(
            ProposalValidation(
                proposal_index=i,
                status="validated" if soundness.sound else "failed",
                soundness=soundness,
                estimate=estimate,
                message=None if waits is not None else "no case_waits.parquet; no estimate",
            )
        )
    ctx.logger.event("node", node="validate", results=[r.model_dump(mode="json") for r in results])
    return {"validation_results": results, "status": "awaiting_review"}


def _case_waits(ctx: AgentContext) -> pd.DataFrame | None:
    path = ctx.segment_dir / CASE_WAITS_FILE if ctx.segment_dir else None
    return pd.read_parquet(path) if path is not None and path.is_file() else None


def review_payload(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """What the analyst sees at the interrupt (JSON-serialisable)."""
    return {
        "segment": state.segment,
        "round": len(state.review_history) + 1,
        "revision_count": state.revision_count,
        "max_revision_loops": ctx.settings.max_revision_loops,
        "diagnosis": state.diagnosis.model_dump() if state.diagnosis else None,
        "proposals": state.proposals.model_dump() if state.proposals else None,
        "edit_results": [r.model_dump(mode="json") for r in state.edit_results],
        "validation_results": [r.model_dump(mode="json") for r in state.validation_results],
        "warnings": [w.model_dump() for w in state.warnings],
        "expected_resume": {"decision": "approve | reject", "comment": "str | None"},
    }


def human_review(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Pause for the analyst. Everything after ``interrupt`` runs on resume only.

    LangGraph re-executes this node from the top when resumed, so nothing
    before ``interrupt()`` may have side effects.
    """
    decision = ReviewDecision.model_validate(interrupt(review_payload(state, ctx)))
    comment = (decision.comment or "").strip() or None
    record = ReviewRecord(
        round=len(state.review_history) + 1,
        decision=decision.decision,
        comment=comment,
        proposal_titles=[p.title for p in state.proposals.proposals] if state.proposals else [],
        decided_at=utc_now(),
    )
    update: dict[str, Any] = {"review_history": [*state.review_history, record]}
    if decision.decision == "approve":
        update["status"] = "approved"
    elif comment is not None and state.revision_count < ctx.settings.max_revision_loops:
        update["status"] = "running"
        update["revision_count"] = state.revision_count + 1
    else:
        update["status"] = "rejected"
    ctx.logger.event("review", **record.model_dump(), next_status=update["status"])
    return update


def finalize(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Write ``summary.json``. Status is already final (approved/rejected/failed)."""
    ctx.logger.write_json(
        "summary.json",
        {
            "run_id": state.run_id,
            "agent_run_id": state.agent_run_id,
            "segment": state.segment,
            "status": state.status,
            "error": state.error,
            "model_id": ctx.llm.model_id,
            "prompt_version": PROMPT_VERSION,
            "revision_count": state.revision_count,
            "review_history": [r.model_dump() for r in state.review_history],
            "warnings": [w.model_dump() for w in state.warnings],
            "diagnosis": state.diagnosis.model_dump() if state.diagnosis else None,
            "proposals": state.proposals.model_dump() if state.proposals else None,
            "edit_results": [r.model_dump() for r in state.edit_results],
            "validation_results": [r.model_dump() for r in state.validation_results],
            "usage": ctx.logger.usage_totals(),
            "finished_at": utc_now(),
        },
    )
    ctx.logger.event("node", node="finalize", status=state.status)
    return {}


def route_after_llm(state: AgentState) -> str:
    """Failed LLM steps go straight to ``finalize``."""
    return "failed" if state.status == "failed" else "ok"


def route_after_review(state: AgentState) -> str:
    """Approve/final reject -> finalize; reject with comment -> redesign."""
    return "redesign" if state.status == "running" else "finalize"
