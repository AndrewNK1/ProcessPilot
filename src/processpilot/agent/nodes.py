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
from dataclasses import dataclass
from typing import Any, TypeVar

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
from processpilot.mining.run import library_versions

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
)
_NOT_IMPLEMENTED = "not_implemented_phase4"


@dataclass(frozen=True)
class AgentContext:
    """Everything a node needs that is not state: inputs, model, logger."""

    run_id: str
    segment: str
    tools: EvidenceTools
    activities: list[str]
    catalogue: HeuristicCatalogue
    domain: DomainKnowledge | None
    settings: AgentSettings
    llm: StructuredLLM
    logger: RunLogger

    def validation_context(self, state: AgentState) -> ValidationContext:
        """Checks are bound to this segment and the run's constraints."""
        return ValidationContext(
            segment=self.segment,
            store=self.tools.segment_store(),
            activities=frozenset(self.activities),
            catalogue=self.catalogue,
            domain=self.domain,
            protected_activities=frozenset(state.constraints.protected_activities),
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
                ctx.segment, ctx.activities, render_digest(ctx.tools), ctx.domain
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


def apply_edits(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Pure code: write ``to_be.bpmn`` per proposal. STUB until Phase 4."""
    assert state.proposals is not None
    results = [
        EditResult(
            proposal_index=i,
            status=_NOT_IMPLEMENTED,
            message="BPMN editing is implemented in Phase 4; proposal passed through unchanged.",
        )
        for i in range(len(state.proposals.proposals))
    ]
    ctx.logger.event("node", node="apply_edits", status=_NOT_IMPLEMENTED)
    return {"edit_results": results}


def validate(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Pure code: soundness + replay ESTIMATE per proposal. STUB until Phase 4."""
    assert state.proposals is not None
    results = [
        ProposalValidation(
            proposal_index=i,
            status=_NOT_IMPLEMENTED,
            message="Soundness check and impact ESTIMATE are implemented in Phase 4.",
        )
        for i in range(len(state.proposals.proposals))
    ]
    ctx.logger.event("node", node="validate", status=_NOT_IMPLEMENTED)
    return {"validation_results": results, "status": "awaiting_review"}


def review_payload(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """What the analyst sees at the interrupt (JSON-serialisable)."""
    return {
        "segment": state.segment,
        "round": len(state.review_history) + 1,
        "revision_count": state.revision_count,
        "max_revision_loops": ctx.settings.max_revision_loops,
        "diagnosis": state.diagnosis.model_dump() if state.diagnosis else None,
        "proposals": state.proposals.model_dump() if state.proposals else None,
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
