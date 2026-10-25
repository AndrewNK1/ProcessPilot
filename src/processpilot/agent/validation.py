"""Validation of LLM output, in code.

WHY in code and not in the prompt: prompts are advisory, validators are not.
Every structured output is checked here before it enters the graph state; a
failing output is retried with these issues fed back, and never silently
trimmed to its valid parts.

Checks: cited evidence IDs exist (in this segment), cited domain facts exist,
heuristic IDs exist in the catalogue and are BPMN-editable, activities named
in edit operations exist in the segment's as-is model, no operation modifies
a protected activity, every proposal can actually be applied to the as-is
model (trial run of ``redesign.operations``), and no free-text field contains
a number that does not appear in the evidence that claim cites.

The number check is deliberately conservative: a number "appears" in the
evidence if some number in the cited items' rendering rounds to it at the
precision the model wrote (``29`` is supported by ``29.3 days``; ``30`` is
not). Numbers inside IDs, activity names and the segment name are ignored.
False positives are possible (e.g. a rank written as a digit), which is why
the caller downgrades persisting number issues to warnings after the retries
(user decision, Phase 3) rather than failing the node.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from processpilot.agent.catalogue import DomainKnowledge, HeuristicCatalogue
from processpilot.agent.prompts import render_item
from processpilot.agent.state import (
    AddActivity,
    AddCheck,
    Diagnosis,
    EditOperation,
    MergeActivities,
    RedesignSet,
    ValidationIssue,
    existing_activities,
    modified_activities,
)
from processpilot.mining.evidence import EvidenceStore
from processpilot.process_tree import TreeNode
from processpilot.redesign.operations import EditError, apply_operations

# Tokens whose digits are not quantities: IDs (BN-01-x, D-03, H-TASK-...),
# identifiers with digits (user_602, P90, R-02), compound adjectives (3-way).
_ID_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z_]*[-_]?[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b")
_COMPOUND = re.compile(r"\b\d+-(?=[A-Za-z])")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])")
_FLOAT_TOLERANCE = 1e-9


@dataclass(frozen=True)
class ValidationContext:
    """Everything an LLM output is checked against, for one segment.

    ``activities`` are those of the as-is model (what an edit may name);
    ``observed_activities`` adds those mining saw but the model left out as
    rare -- they may still be mentioned in text. ``as_is_tree``, when given,
    is used to trial-apply every proposal so an operation that cannot be
    applied is sent back for a retry instead of failing later.
    """

    segment: str
    store: EvidenceStore
    activities: frozenset[str]
    catalogue: HeuristicCatalogue
    domain: DomainKnowledge | None
    protected_activities: frozenset[str]
    as_is_tree: TreeNode | None = None
    observed_activities: frozenset[str] = frozenset()

    @property
    def domain_ids(self) -> set[str]:
        """Valid domain fact ids (empty if no domain file)."""
        return self.domain.ids() if self.domain else set()

    def _strip_terms(self) -> list[str]:
        # Longest first, so "Record Invoice Receipt" goes before "Invoice".
        names = {*self.activities, *self.observed_activities, self.segment}
        return sorted(names, key=len, reverse=True)


def extract_numbers(text: str, strip_terms: list[str]) -> list[tuple[float, int, str]]:
    """``(value, decimals, literal)`` for each quantity-like number in ``text``."""
    for term in strip_terms:
        text = text.replace(term, " ")
    text = _ID_TOKEN.sub(" ", text)
    text = _COMPOUND.sub(" ", text)
    found = []
    for m in _NUMBER.finditer(text):
        whole, decimals = m.group(1).replace(",", ""), m.group(2) or ""
        value = float(f"{whole}.{decimals}" if decimals else whole)
        found.append((value, len(decimals), m.group(0)))
    return found


def _supported(value: float, decimals: int, allowed: list[float]) -> bool:
    return any(abs(round(a, decimals) - value) < _FLOAT_TOLERANCE for a in allowed)


def unsupported_numbers(
    texts: dict[str, str], cited_ids: list[str], ctx: ValidationContext
) -> list[ValidationIssue]:
    """Numbers in ``texts`` (path -> text) not found in the cited evidence."""
    strip = ctx._strip_terms()
    known = set(ctx.store.ids())
    allowed = [
        v
        for cid in cited_ids
        if cid in known
        for v, _, _ in extract_numbers(render_item(ctx.store.get(cid)), strip)
    ]
    issues = []
    for path, text in texts.items():
        for value, decimals, literal in extract_numbers(text, strip):
            if not _supported(value, decimals, allowed):
                issues.append(
                    ValidationIssue(
                        code="unsupported_number",
                        path=path,
                        message=(
                            f"the number {literal!r} does not appear in the cited evidence "
                            f"{cited_ids}; remove it or cite the evidence it comes from"
                        ),
                    )
                )
    return issues


def _id_issues(
    cited: list[str], domain_ids: list[str], path: str, ctx: ValidationContext
) -> list[ValidationIssue]:
    issues = [
        ValidationIssue(
            code="unknown_evidence_id",
            path=f"{path}.evidence_ids",
            message=f"{eid!r} is not an evidence ID of segment {ctx.segment!r}",
        )
        for eid in ctx.store.unknown_ids(cited)
    ]
    issues += [
        ValidationIssue(
            code="unknown_domain_fact_id",
            path=f"{path}.domain_fact_ids",
            message=f"{fid!r} is not a domain fact ID",
        )
        for fid in domain_ids
        if fid not in ctx.domain_ids
    ]
    return issues


def validate_diagnosis(diagnosis: Diagnosis, ctx: ValidationContext) -> list[ValidationIssue]:
    """Every problem in a parsed ``Diagnosis``; empty list means valid."""
    issues: list[ValidationIssue] = []
    for i, h in enumerate(diagnosis.hypotheses):
        path = f"hypotheses[{i}]"
        issues += _id_issues(h.evidence_ids, h.domain_fact_ids, path, ctx)
        texts = {f"{path}.description": h.description}
        texts |= {
            f"{path}.alternative_explanations[{k}]": t
            for k, t in enumerate(h.alternative_explanations)
        }
        issues += unsupported_numbers(texts, h.evidence_ids, ctx)
    return issues


def validate_redesign(redesign: RedesignSet, ctx: ValidationContext) -> list[ValidationIssue]:
    """Every problem in a parsed ``RedesignSet``; empty list means valid."""
    issues: list[ValidationIssue] = []
    editable = {h.id for h in ctx.catalogue.editable()}
    for i, p in enumerate(redesign.proposals):
        path = f"proposals[{i}]"
        issues += _id_issues(p.evidence_ids, p.domain_fact_ids, path, ctx)
        if p.heuristic_id not in ctx.catalogue.ids():
            issues.append(
                ValidationIssue(
                    code="unknown_heuristic",
                    path=f"{path}.heuristic_id",
                    message=f"{p.heuristic_id!r} is not in the heuristics catalogue",
                )
            )
        elif p.heuristic_id not in editable:
            issues.append(
                ValidationIssue(
                    code="heuristic_not_editable",
                    path=f"{path}.heuristic_id",
                    message=(
                        f"{p.heuristic_id!r} cannot be expressed as BPMN edits and may "
                        "not be selected for a redesign proposal"
                    ),
                )
            )
        op_issues = [
            issue
            for j, op in enumerate(p.edit_operations)
            for issue in _operation_issues(op, f"{path}.edit_operations[{j}]", ctx)
        ]
        issues += op_issues
        # Trial-apply only when every operation passed the per-operation checks,
        # so one mistake is not reported twice.
        if not op_issues and ctx.as_is_tree is not None:
            issues += _applicability_issues(p.edit_operations, path, ctx)
        texts = {
            f"{path}.title": p.title,
            f"{path}.rationale": p.rationale,
            f"{path}.expected_effect": p.expected_effect,
        }
        texts |= {f"{path}.risks[{k}]": t for k, t in enumerate(p.risks)}
        texts |= {f"{path}.assumptions[{k}]": t for k, t in enumerate(p.assumptions)}
        issues += unsupported_numbers(texts, p.evidence_ids, ctx)
    return issues


def _applicability_issues(
    ops: list[EditOperation], path: str, ctx: ValidationContext
) -> list[ValidationIssue]:
    assert ctx.as_is_tree is not None
    try:
        apply_operations(ctx.as_is_tree, ops, ctx.protected_activities)
    except EditError as exc:
        return [
            ValidationIssue(
                code="edit_not_applicable",
                path=f"{path}.edit_operations[{exc.index}]",
                message=f"cannot be applied to the as-is model: {exc.reason}",
            )
        ]
    return []


def _operation_issues(op: object, path: str, ctx: ValidationContext) -> list[ValidationIssue]:
    issues = [
        ValidationIssue(
            code="unknown_activity",
            path=path,
            message=(
                f"{a!r} is not an activity of this segment's as-is model"
                + (
                    " (it occurs in the log but too rarely to be part of the model)"
                    if a in ctx.observed_activities
                    else ""
                )
            ),
        )
        for a in existing_activities(op)  # type: ignore[arg-type]
        if a not in ctx.activities
    ]
    if isinstance(op, AddActivity | AddCheck) and op.name in ctx.activities:
        issues.append(
            ValidationIssue(
                code="activity_already_exists",
                path=f"{path}.name",
                message=f"{op.op} must introduce a new activity; {op.name!r} exists",
            )
        )
    if (
        isinstance(op, MergeActivities)
        and op.merged_name in ctx.activities
        and op.merged_name not in op.activities
    ):
        issues.append(
            ValidationIssue(
                code="activity_already_exists",
                path=f"{path}.merged_name",
                message=(
                    f"merged_name {op.merged_name!r} is an existing activity that is not "
                    "one of the merged activities"
                ),
            )
        )
    issues += [
        ValidationIssue(
            code="protected_activity",
            path=path,
            message=(
                f"{a!r} is protected and may not be removed, moved, merged or parallelized "
                "(automating it or adding a step after it is allowed)"
            ),
        )
        for a in modified_activities(op)  # type: ignore[arg-type]
        if a in ctx.protected_activities
    ]
    return issues
