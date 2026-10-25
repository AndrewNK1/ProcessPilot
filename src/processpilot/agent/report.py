"""Readable views of an agent run, for the notebook now and the UI in Phase 5.

Pure formatting over the state and the run log; nothing here calls a model or
recomputes a number.
"""

from __future__ import annotations

from typing import Any

from processpilot.agent.runlog import RunLogger
from processpilot.agent.state import (
    Diagnosis,
    EditResult,
    ProposalValidation,
    RedesignSet,
    ValidationIssue,
)
from processpilot.mining.evidence import format_duration
from processpilot.redesign.operations import describe


def diagnosis_markdown(diagnosis: Diagnosis) -> str:
    """Ranked hypotheses with their evidence and alternatives."""
    out = []
    for rank, h in enumerate(diagnosis.hypotheses, start=1):
        out.append(f"**H{rank} ({h.confidence} confidence).** {h.description}")
        out.append(f"- evidence: {', '.join(h.evidence_ids)}")
        if h.domain_fact_ids:
            out.append(f"- domain facts: {', '.join(h.domain_fact_ids)}")
        out += [f"- alternative: {a}" for a in h.alternative_explanations]
        out.append("")
    return "\n".join(out)


def proposals_markdown(redesign: RedesignSet) -> str:
    """Each proposal with heuristic, operations, evidence, effect, risks, assumptions."""
    out = []
    for i, p in enumerate(redesign.proposals, start=1):
        out.append(f"### P{i}. {p.title}  \n`{p.heuristic_id}`")
        out += [f"- edit: {describe(op)}" for op in p.edit_operations]
        out.append(f"- evidence: {', '.join(p.evidence_ids)}")
        if p.domain_fact_ids:
            out.append(f"- domain facts: {', '.join(p.domain_fact_ids)}")
        out.append(f"- rationale: {p.rationale}")
        out.append(f"- expected effect (qualitative): {p.expected_effect}")
        out += [f"- risk: {r}" for r in p.risks]
        out += [f"- assumption: {a}" for a in p.assumptions]
        out.append("")
    return "\n".join(out)


def outcomes_markdown(
    edit_results: list[EditResult], validation_results: list[ProposalValidation]
) -> str:
    """Per proposal: whether it applied, the model diff, soundness and the ESTIMATE."""
    checks = {v.proposal_index: v for v in validation_results}
    out = []
    for edit in edit_results:
        out.append(f"### P{edit.proposal_index + 1}: edits {edit.status}")
        if edit.status != "applied":
            out += [f"- {edit.message}", ""]
            continue
        if edit.diff is not None:
            d = edit.diff
            for label, names in (
                ("added", d.added),
                ("checks added", d.checks_added),
                ("removed", d.removed),
                ("automated", d.automated),
            ):
                if names:
                    out.append(f"- {label}: {', '.join(names)}")
        out.append(f"- to-be model: `{edit.to_be_bpmn_path}`")
        v = checks.get(edit.proposal_index)
        if v is not None and v.soundness is not None:
            out.append(
                f"- soundness: {'sound' if v.soundness.sound else 'NOT sound'} "
                f"({v.soundness.method})"
            )
        if v is not None and v.estimate is not None:
            e = v.estimate
            share = f" ({e.median_change_share:+.1%})" if e.median_change_share is not None else ""
            out.append(
                f"- **ESTIMATE** median throughput {format_duration(e.as_is.median_seconds)} "
                f"-> {format_duration(e.to_be.median_seconds)}{share}; "
                f"{e.cases_affected:,} of {e.case_count:,} cases affected"
            )
            out += [f"  - assumption: {a}" for a in e.assumptions]
            out += [f"  - not estimated: {n}" for n in e.not_estimated]
        out.append("")
    return "\n".join(out)


def warnings_markdown(warnings: list[ValidationIssue]) -> str:
    """Issues that survived the retries (only unsupported numbers can)."""
    if not warnings:
        return "**Warnings:** none."
    lines = [f"- **{w.code}** `{w.path}`: {w.message}" for w in warnings]
    return "\n".join(["**Warnings:**", "", *lines])


def validation_log(logger: RunLogger) -> list[dict[str, Any]]:
    """One row per LLM call: node, attempt, issues found, tokens, cost."""
    rows = []
    for e in logger.events():
        if e["kind"] != "llm_call":
            continue
        rows.append(
            {
                "node": e["node"],
                "attempt": e["attempt"],
                "issues": len(e["issues"]),
                "issue_codes": ", ".join(sorted({i["code"] for i in e["issues"]})),
                "stop_reason": e.get("stop_reason"),
                "input_tokens": e["usage"].get("input_tokens"),
                "output_tokens": e["usage"].get("output_tokens"),
                "cost_usd": e.get("cost_usd"),
            }
        )
    return rows


def issue_details(logger: RunLogger) -> list[dict[str, Any]]:
    """Every individual validation issue, with the call it was found in."""
    return [
        {"node": e["node"], "attempt": e["attempt"], **issue}
        for e in logger.events()
        if e["kind"] == "llm_call"
        for issue in e["issues"]
    ]
