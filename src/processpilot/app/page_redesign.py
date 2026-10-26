"""Page 3: run the agent on a segment, read its diagnosis and proposals, decide.

What the analyst needs to judge a proposal sits next to it: the evidence it
cites (with the code-written summaries), the exact edit operations, the
soundness verdict, the replay ESTIMATE with its assumptions, and the as-is
and to-be models side by side. The decision goes to the API, which logs it
and resumes the paused LangGraph run; ProcessPilot never executes a change.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from processpilot.app.bpmn import bpmn_viewer
from processpilot.app.client import ApiError
from processpilot.app.ui import (
    Keys,
    api_error,
    client,
    fmt_duration,
    fmt_share,
    pick_run,
    pick_segment,
    settings,
    wait_for_job,
)

_STATUS_TEXT = {
    "running": "working",
    "awaiting_review": "waiting for your decision",
    "approved": "approved",
    "rejected": "rejected",
    "failed": "failed",
}


def render() -> None:
    """The Redesign page."""
    st.title("Redesign")
    st.caption(
        "The agent interprets the evidence and proposes redesigns from a fixed heuristic "
        "catalogue. You approve or reject; nothing is ever executed."
    )
    try:
        run = pick_run()
        if run is None:
            return
        if run["preset"] is None:
            st.warning("Name this run's preset on the Evidence page first.")
            return
        segment = pick_segment(run)
        if segment is None:
            return
        run_id, slug = run["run_id"], segment["slug"]
        evidence = {i["id"]: i for i in client().evidence(run_id, slug)}
        _start_form(run_id, slug)
        agent_run_id = _pick_agent_run(run_id, slug)
        if agent_run_id is None:
            return
        detail = client().agent_run(run_id, slug, agent_run_id)
        _show_agent_run(run_id, slug, detail, evidence)
    except ApiError as exc:
        api_error(exc)


# --- starting a run ------------------------------------------------------------------


def _start_form(run_id: str, slug: str) -> None:
    model = client().segment_model(run_id, slug)
    defaults = model["default_constraints"] or {"text": [], "protected_activities": []}
    default_model = client().health()["model_id"] or ""
    with st.expander("Start a new agent run", expanded=not client().agent_runs(run_id, slug)):
        if model["unmodelled_activities"]:
            st.caption(
                "Too rare to be in the as-is model (can be discussed, not edited): "
                + ", ".join(model["unmodelled_activities"])
            )
        with st.form("start_agent"):
            protected = st.multiselect(
                "Protected activities (never removed, moved, merged or parallelised)",
                model["activities"],
                default=[a for a in defaults["protected_activities"] if a in model["activities"]],
            )
            text = st.text_area(
                "Constraints for the agent, one per line",
                "\n".join(defaults["text"]),
            )
            model_id = st.text_input(
                "Model (`mock` = rule-based baseline, no API key)",
                default_model,
                help="Any id init_chat_model accepts, e.g. anthropic:claude-opus-5-5.",
            )
            if st.form_submit_button("Run the agent", type="primary"):
                constraints = {
                    "text": [line.strip() for line in text.splitlines() if line.strip()],
                    "protected_activities": protected,
                }
                job = wait_for_job(
                    client().start_agent(run_id, slug, constraints, model_id.strip() or None),
                    "Agent run",
                )
                if job:
                    st.session_state[Keys.agent_run] = job["result"]["agent_run_id"]
                    st.rerun()


def _pick_agent_run(run_id: str, slug: str) -> str | None:
    runs = client().agent_runs(run_id, slug)
    if not runs:
        st.info("No agent run for this segment yet.")
        return None
    ids = [r["agent_run_id"] for r in runs]
    by_id = {r["agent_run_id"]: r for r in runs}
    current = st.session_state.get(Keys.agent_run)
    chosen = st.selectbox(
        "Agent run",
        ids,
        index=ids.index(current) if current in ids else 0,
        format_func=lambda i: (
            f"{i} · {_STATUS_TEXT.get(by_id[i]['status'], by_id[i]['status'])} · "
            f"model {by_id[i]['model_id']}"
        ),
    )
    st.session_state[Keys.agent_run] = chosen
    return chosen


# --- one agent run -------------------------------------------------------------------


def _show_agent_run(
    run_id: str, slug: str, detail: dict[str, Any], evidence: dict[str, dict[str, Any]]
) -> None:
    state = detail["state"]
    status = detail["status"]
    st.markdown(f"**Status:** {_STATUS_TEXT.get(status, status)} · model `{detail['model_id']}`")
    if detail["model_id"] == "mock":
        st.info("Rule-based mock output: no language model was involved.")
    if detail["busy"]:
        st.info("A background job is still working on this run.")
        if st.button("Refresh"):
            st.rerun()
        return
    if state is None:
        st.warning("No checkpoint for this run yet.")
        return
    if state["error"]:
        st.error(state["error"])
    if state["warnings"]:
        with st.expander(f"{len(state['warnings'])} validation warning(s) kept after retries"):
            for w in state["warnings"]:
                st.write(f"- `{w['code']}` {w['path']}: {w['message']}")

    if state["diagnosis"]:
        st.subheader("Diagnosis")
        for n, h in enumerate(state["diagnosis"]["hypotheses"], start=1):
            with st.container(border=True):
                st.markdown(f"**H{n}** ({h['confidence']} confidence): {h['description']}")
                _cited(h["evidence_ids"], evidence)
                st.caption("Alternative explanations: " + " · ".join(h["alternative_explanations"]))

    if detail["proposals"]:
        st.subheader(f"Proposals (revision {state['revision_count']})")
        for p in detail["proposals"]:
            _proposal(p, evidence)
        _compare_models(run_id, slug, detail)

    if detail["awaiting_review"]:
        _decision_form(run_id, slug, detail)
    if detail["decisions"]:
        st.subheader("Decisions")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "round": d["round"],
                        "decision": d["decision"],
                        "comment": d["comment"],
                        "analyst": d["analyst"],
                        "proposals": "; ".join(d["proposal_titles"]),
                        "at (UTC)": d["decided_at"],
                    }
                    for d in detail["decisions"]
                ]
            ),
            hide_index=True,
        )


def _cited(ids: list[str], evidence: dict[str, dict[str, Any]]) -> None:
    with st.expander("Cited evidence: " + ", ".join(ids)):
        for evidence_id in ids:
            item = evidence.get(evidence_id)
            st.write(f"**{evidence_id}**: {item['summary'] if item else '(not in this segment)'}")


def _proposal(p: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> None:
    proposal, validation = p["proposal"], p["validation"]
    with st.container(border=True):
        st.markdown(
            f"**P{p['index'] + 1}. {proposal['title']}** · heuristic `{proposal['heuristic_id']}`"
        )
        st.markdown("**Changes:** " + "; ".join(p["operations_text"]))
        st.markdown(f"**Rationale:** {proposal['rationale']}")
        st.markdown(f"**Expected effect:** {proposal['expected_effect']}")
        left, right = st.columns(2)
        left.markdown("**Risks**\n" + "".join(f"\n- {r}" for r in proposal["risks"]))
        right.markdown("**Assumptions**\n" + "".join(f"\n- {a}" for a in proposal["assumptions"]))
        _cited(proposal["evidence_ids"], evidence)
        if p["edit"] and p["edit"]["status"] != "applied":
            st.error(f"Edits could not be applied: {p['edit']['message']}")
        if validation is None:
            return
        if validation["soundness"]:
            s = validation["soundness"]
            verdict = "sound" if s["sound"] else "NOT sound"
            st.caption(f"Soundness: {verdict} ({s['method']})")
        estimate = validation["estimate"]
        if estimate:
            _estimate(estimate)
        elif validation["message"]:
            st.caption(validation["message"])


def _estimate(e: dict[str, Any]) -> None:
    st.markdown("**ESTIMATE** (replay of observed waits, not a simulation)")
    cols = st.columns(4)
    cols[0].metric("Median throughput now", fmt_duration(e["as_is"]["median_seconds"]))
    cols[1].metric(
        "Estimated median",
        fmt_duration(e["to_be"]["median_seconds"]),
        delta=fmt_share(e["median_change_share"]) if e["median_change_share"] is not None else None,
        delta_color="inverse",
    )
    cols[2].metric(
        "P90 now → estimated",
        f"{fmt_duration(e['as_is']['p90_seconds'])} → {fmt_duration(e['to_be']['p90_seconds'])}",
    )
    cols[3].metric("Cases affected", fmt_share(e["cases_affected_share"]))
    with st.expander("Estimate assumptions and limits"):
        for a in e["assumptions"]:
            st.write(f"- {a}")
        for n in e["not_estimated"]:
            st.write(f"- Not estimated: {n}")


def _compare_models(run_id: str, slug: str, detail: dict[str, Any]) -> None:
    applied = [p for p in detail["proposals"] if p["edit"] and p["edit"]["status"] == "applied"]
    if not applied:
        return
    st.subheader("As-is vs to-be")
    index = st.selectbox(
        "Proposal",
        [p["index"] for p in applied],
        format_func=lambda i: f"P{i + 1}. {detail['proposals'][i]['proposal']['title']}",
    )
    height = settings()["bpmn_height_px"]
    left, right = st.columns(2)
    with left:
        st.caption("As-is")
        bpmn_viewer(client().as_is_bpmn(run_id, slug), height, key=f"cmp-asis-{run_id}-{slug}")
    with right:
        st.caption(f"To-be, proposal {index + 1}")
        xml = client().to_be_bpmn(run_id, slug, detail["agent_run_id"], index)
        bpmn_viewer(xml, height, key=f"cmp-tobe-{detail['agent_run_id']}-{index}")
    diff = detail["proposals"][index]["edit"]["diff"]
    if diff and diff["changes"]:
        st.caption("Model changes: " + "; ".join(diff["changes"]))


def _decision_form(run_id: str, slug: str, detail: dict[str, Any]) -> None:
    st.subheader(f"Your decision (round {detail['review_round']})")
    loops_left = detail["state"]["revision_count"] < detail["max_revision_loops"]
    with st.form("decision", clear_on_submit=True):
        decision = st.radio("Decision", ["approve", "reject"], horizontal=True)
        comment = st.text_area(
            "Comment",
            help=(
                "Rejecting with a comment asks the agent for revised proposals"
                + ("." if loops_left else " (no revisions left: this run ends).")
                + " Rejecting without a comment ends the run."
            ),
        )
        analyst = st.text_input(
            "Your name (for the decision log)", st.session_state.get(Keys.analyst, "")
        )
        if st.form_submit_button("Submit decision", type="primary"):
            st.session_state[Keys.analyst] = analyst
            job_id = client().decide(
                run_id,
                slug,
                detail["agent_run_id"],
                decision,
                comment.strip() or None,
                analyst.strip() or None,
            )
            if wait_for_job(job_id, "Recording the decision and resuming the agent"):
                st.rerun()
