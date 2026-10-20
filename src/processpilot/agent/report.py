"""Readable views of an agent run, for the notebook now and the UI in Phase 5.

Pure formatting over the state and the run log; nothing here calls a model or
recomputes a number.
"""

from __future__ import annotations

from typing import Any

from processpilot.agent.runlog import RunLogger
from processpilot.agent.state import Diagnosis, RedesignSet, ValidationIssue, existing_activities


def _op_text(op: Any) -> str:
    match op.op:
        case "add_activity":
            return f"add '{op.name}' after '{op.after}'"
        case "merge_activities":
            return f"merge {', '.join(repr(a) for a in op.activities)} into '{op.merged_name}'"
        case "reorder_activities":
            return f"move '{op.activity}' to directly after '{op.after}'"
        case _:
            names = ", ".join(repr(a) for a in existing_activities(op))
            return f"{op.op.replace('_', ' ')} {names}"


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
        out += [f"- edit: {_op_text(op)}" for op in p.edit_operations]
        out.append(f"- evidence: {', '.join(p.evidence_ids)}")
        if p.domain_fact_ids:
            out.append(f"- domain facts: {', '.join(p.domain_fact_ids)}")
        out.append(f"- rationale: {p.rationale}")
        out.append(f"- expected effect (qualitative): {p.expected_effect}")
        out += [f"- risk: {r}" for r in p.risks]
        out += [f"- assumption: {a}" for a in p.assumptions]
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
