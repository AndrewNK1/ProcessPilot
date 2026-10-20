"""Prompt templates, versioned.

WHY prompts live in code and are logged per run: a thesis result that cannot be
reproduced is not a result. ``PROMPT_VERSION`` is written into the run log
alongside the model id, and the fully rendered prompts are logged too.

Prompts render the *evidence items*, the heuristics catalogue and the analyst's
constraints -- never raw event data. They are dataset-agnostic: segment names,
activity names, domain facts and constraints are all injected; nothing about a
particular dataset appears in this module.

``render_item`` is also what the number check in ``agent/validation.py``
reads: a number counts as "in the evidence" if it appears in the rendering of
a cited item, i.e. exactly what the model was shown.
"""

from __future__ import annotations

from processpilot.agent.catalogue import DomainKnowledge, Heuristic
from processpilot.agent.state import Constraints, Diagnosis, RedesignSet, ValidationIssue
from processpilot.agent.tools import EvidenceTools
from processpilot.mining.evidence import EvidenceItem

PROMPT_VERSION = "p3-v1"

SYSTEM_PROMPT = """\
You are ProcessPilot, a process-analysis assistant for business analysts. You \
interpret precomputed process-mining evidence for one segment of an event log. \
You never see raw event data and you never compute metrics.

Rules:
1. Use only the evidence provided in this conversation. Every claim must cite \
the evidence IDs that support it, written exactly as listed. Never cite an ID \
that is not listed.
2. Do not invent or derive numbers. Any number you write must appear in the \
evidence you cite for that claim. Do not add, subtract, average or convert \
figures; describe effects qualitatively instead.
3. For every hypothesis, consider alternative explanations that would produce \
the same evidence, including data artefacts (for example cases still open when \
the log was extracted) and legitimate business reasons.
4. Only propose redesigns that apply a heuristic from the provided catalogue, \
referenced by its exact ID.
5. Respect the analyst's constraints. Never remove, move, merge or parallelize \
a protected activity.
6. Domain facts are general knowledge, not findings. You may reference them by \
ID in domain_fact_ids, but they never replace evidence.
Use activity names exactly as written in the activity list."""

_TYPE_HEADINGS: tuple[tuple[str, str, str], ...] = (
    ("kpi", "Segment KPIs", ""),
    ("bottleneck", "Bottlenecks (waiting time per transition, ranked by impact)", ""),
    ("variant", "Variants (most frequent paths)", ""),
    ("rework", "Rework (repeated activities)", "No rework items were found in this segment."),
    ("resource", "Resources and handovers", ""),
    (
        "conformance",
        "Conformance rules",
        "No configured conformance rule applies to this segment. Absence of findings "
        "here is NOT evidence that the segment is compliant.",
    ),
)


def render_item(item: EvidenceItem) -> str:
    """One evidence item as the model sees it: id, type, summary, key context."""
    lines = [f"[{item.id}] ({item.type}) {item.summary}"]
    if item.type == "variant" and "activities" in item.metrics:
        lines.append("    path: " + " -> ".join(item.metrics["activities"]))
    if item.type == "conformance" and item.metrics.get("description"):
        lines.append(f"    rule: {item.metrics['description'].strip()}")
    return "\n".join(lines)


def render_digest(tools: EvidenceTools) -> str:
    """Every evidence item of the segment, grouped by type, in rank order."""
    by_type = {
        "kpi": tools.get_kpis(),
        "bottleneck": tools.get_top_bottlenecks(),
        "variant": tools.get_variants(),
        "rework": tools.get_rework(),
        "resource": tools.get_resources(),
        "conformance": tools.get_conformance(),
    }
    blocks = []
    for type_, heading, empty_note in _TYPE_HEADINGS:
        items = by_type[type_]
        body = "\n".join(render_item(i) for i in items) if items else (empty_note or "(none)")
        blocks.append(f"### {heading}\n{body}")
    return "\n\n".join(blocks)


def _render_activities(activities: list[str]) -> str:
    return "\n".join(f"- {a}" for a in activities)


def _render_domain(domain: DomainKnowledge | None) -> str:
    if domain is None:
        return "(no domain knowledge configured for this dataset)"
    facts = "\n".join(f"[{f.id}] {' '.join(f.statement.split())}" for f in domain.facts)
    return f"General {domain.domain} knowledge, not findings from this log:\n{facts}"


def _render_context(
    segment: str, activities: list[str], digest: str, domain: DomainKnowledge | None
) -> str:
    return (
        f"Segment: {segment}\n\n"
        f"## Activities in this segment's as-is process\n{_render_activities(activities)}\n\n"
        f"## Evidence\n{digest}\n\n"
        f"## Domain facts\n{_render_domain(domain)}"
    )


def diagnose_prompt(
    segment: str, activities: list[str], digest: str, domain: DomainKnowledge | None
) -> str:
    """User message for the ``diagnose`` step."""
    return (
        _render_context(segment, activities, digest, domain) + "\n\n## Task\n"
        "Diagnose the main root causes of long throughput times, waiting, rework or "
        "non-conformance in this segment. Return between 1 and 5 hypotheses, ranked "
        "with the most important first. For each hypothesis give the evidence IDs it "
        "rests on (at least one), your confidence (low, medium or high), and at least "
        "one alternative explanation."
    )


def _render_heuristic(h: Heuristic) -> str:
    signals = "; ".join(f"{a.evidence_type}: {a.signal}" for a in h.when_applicable)
    ops = ", ".join(h.typical_operations)
    return (
        f"[{h.id}] {h.name}: {' '.join(h.description.split())}\n"
        f"    applicable when: {signals}\n"
        f"    typical operations: {ops}\n"
        f"    risks: {h.risks}"
    )


_OPERATIONS_HELP = """\
- remove_activity(activity): delete an existing activity.
- parallelize_activities(activities): run two or more existing, currently \
sequential activities concurrently.
- reorder_activities(activity, after): move an existing activity so it directly \
follows the existing activity `after`.
- add_activity(name, after): insert a NEW activity `name` directly after the \
existing activity `after`.
- merge_activities(activities, merged_name): replace two or more existing \
activities by one composite activity."""


def _render_constraints(constraints: Constraints) -> str:
    text = "\n".join(f"- {c}" for c in constraints.text) or "- (none)"
    protected = ", ".join(constraints.protected_activities) or "(none)"
    return (
        f"{text}\n"
        f"Protected activities (must not be removed, moved, merged or parallelized; they "
        f"may be used as the `after` anchor of another operation): {protected}"
    )


def redesign_prompt(
    segment: str,
    activities: list[str],
    digest: str,
    domain: DomainKnowledge | None,
    diagnosis: Diagnosis,
    heuristics: list[Heuristic],
    constraints: Constraints,
    previous: RedesignSet | None = None,
    analyst_comment: str | None = None,
) -> str:
    """User message for the ``redesign`` step (also used for revisions)."""
    catalogue = "\n".join(_render_heuristic(h) for h in heuristics)
    parts = [
        _render_context(segment, activities, digest, domain),
        f"## Diagnosis (from the previous step)\n{diagnosis.model_dump_json(indent=2)}",
        f"## Heuristic catalogue (select only from these)\n{catalogue}",
        f"## Edit operations (the only way to express a change)\n{_OPERATIONS_HELP}",
        f"## Analyst constraints\n{_render_constraints(constraints)}",
    ]
    if previous is not None:
        parts.append(
            "## Analyst feedback on your previous proposals\n"
            f"Previous proposals:\n{previous.model_dump_json(indent=2)}\n\n"
            f"The analyst rejected them with this comment:\n{analyst_comment}\n"
            "Revise the proposals to address this feedback."
        )
    parts.append(
        "## Task\n"
        "Propose 2 or 3 alternative redesigns of this segment's process that address the "
        "diagnosis. Each proposal applies exactly one catalogue heuristic, expresses the "
        "change as one or more edit operations on the listed activities, cites the "
        "evidence IDs that motivate it, and states its rationale, qualitative expected "
        "effect, risks and assumptions."
    )
    return "\n\n".join(parts)


def retry_prompt(issues: list[ValidationIssue]) -> str:
    """Follow-up message after an output failed validation."""
    listed = "\n".join(f"- [{i.code}] {i.path + ': ' if i.path else ''}{i.message}" for i in issues)
    return (
        "Your previous output failed validation:\n"
        f"{listed}\n"
        "Return a complete, corrected output that fixes every listed problem and keeps "
        "everything else that was valid."
    )
