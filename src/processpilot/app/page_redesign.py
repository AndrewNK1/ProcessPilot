"""Page 3: run the agent on a segment, read its diagnosis and proposals, decide.

What the analyst needs to judge a proposal sits next to it: the evidence it
cites (with the code-written summaries) beside each claim, the exact edit
operations, the soundness verdict, the replay ESTIMATE drawn against today,
the rungs of the wait ladder it acts on, and the as-is and to-be models side
by side. The decision goes to the API, which logs it and resumes the paused
LangGraph run; ProcessPilot never executes a change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd
import streamlit as st

from processpilot.app import ladder, widgets
from processpilot.app.bpmn import bpmn_viewer
from processpilot.app.client import ApiError
from processpilot.app.ui import (
    Keys,
    api_error,
    client,
    fmt_duration,
    fmt_share,
    page,
    page_header,
    pick_run,
    pick_segment,
    settings,
    wait_for_job,
)
from processpilot.process_tree import AUTOMATED_SUFFIX

_STATUS_TEXT = {
    "running": "working",
    "awaiting_review": "waiting for your decision",
    "approved": "approved",
    "rejected": "rejected",
    "failed": "failed",
}
_STATUS_BADGE = {
    "running": ("gray", ":material/hourglass_top:"),
    "awaiting_review": ("orange", ":material/rate_review:"),
    "approved": ("green", ":material/check_circle:"),
    "rejected": ("gray", ":material/cancel:"),
    "failed": ("red", ":material/error:"),
}
_CONFIDENCE = {"high": "green", "medium": "orange", "low": "gray"}
_DRAFT = "pp_decision_draft"
_MISSING = "pp_decision_missing"


def render() -> None:
    """The Redesign page."""
    header = st.container()
    try:
        scope_segment, scope_run = st.columns([5, 2], vertical_alignment="bottom")
        with scope_run:
            run = pick_run()
        if run is None:
            with header:
                page_header("Redesign")
            return
        if run["preset"] is None:
            with header:
                page_header("Redesign")
            st.warning(
                "Name the preset this run was mined with on the Evidence page first: the agent "
                "needs its constraints.",
                icon=":material/tune:",
            )
            st.page_link(page("evidence"), label="Go to Evidence", icon=":material/arrow_forward:")
            return
        with scope_segment:
            segment = pick_segment(run)
        if segment is None:
            return
        run_id, slug = run["run_id"], segment["slug"]
        evidence = {i["id"]: i for i in client().evidence(run_id, slug)}
        runs = client().agent_runs(run_id, slug)
        agent_run_id = _pick_agent_run(runs)
        detail = client().agent_run(run_id, slug, agent_run_id) if agent_run_id else None
        with header:
            page_header(f"Redesign · {segment['name']}", _subtitle(detail))
        _start_form(run_id, slug, expanded=not runs)
        if detail is None:
            return
        _show_agent_run(run_id, slug, detail, evidence)
    except ApiError as exc:
        api_error(exc)


def _subtitle(detail: dict[str, Any] | None) -> str:
    if detail is None:
        return (
            "The agent reads the mined evidence and proposes redesigns from a fixed heuristic "
            "catalogue. You approve or reject; nothing is ever executed."
        )
    count = len(detail["proposals"])
    if detail["awaiting_review"]:
        return (
            f"{count} proposal(s) are waiting for your decision (round {detail['review_round']}). "
            "Nothing is executed whatever you decide."
        )
    return f"This agent run is {_STATUS_TEXT.get(detail['status'], detail['status'])}."


# --- starting a run ------------------------------------------------------------------


def _start_form(run_id: str, slug: str, expanded: bool) -> None:
    model = client().segment_model(run_id, slug)
    defaults = model["default_constraints"] or {"text": [], "protected_activities": []}
    default_model = client().health()["model_id"] or ""
    with st.expander("Start a new agent run", expanded=expanded, icon=":material/play_circle:"):
        if model["unmodelled_activities"]:
            st.caption(
                "Too rare to be in the as-is model (can be discussed, not edited): "
                + ", ".join(model["unmodelled_activities"])
            )
        with st.form("start_agent", border=False):
            protected = st.multiselect(
                "Protected activities",
                model["activities"],
                default=[a for a in defaults["protected_activities"] if a in model["activities"]],
                help="Never removed, moved, merged or parallelised. They may still be automated.",
            )
            text = st.text_area(
                "Constraints for the agent, one per line",
                "\n".join(defaults["text"]),
            )
            model_id = st.text_input(
                "Model",
                default_model,
                help="`mock` is the rule-based baseline (no API key). Any id init_chat_model "
                "accepts works, e.g. anthropic:claude-opus-5-5.",
            )
            if st.form_submit_button("Run the agent", type="primary", icon=":material/play_arrow:"):
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


def _pick_agent_run(runs: list[dict[str, Any]]) -> str | None:
    if not runs:
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
    color, icon = _STATUS_BADGE.get(status, ("gray", None))
    with st.container(horizontal=True, vertical_alignment="center"):
        st.badge(_STATUS_TEXT.get(status, status).capitalize(), color=color, icon=icon)
        st.caption(f"Model `{detail['model_id']}` · started {detail['created_at'][:16]} UTC")
    if detail["model_id"] == "mock":
        st.caption(
            ":material/smart_toy: Rule-based mock output: no language model was involved, and "
            "every text starts with [mock]."
        )
    if detail["busy"]:
        st.info("A background job is still working on this run.", icon=":material/hourglass_top:")
        if st.button("Refresh", icon=":material/refresh:"):
            st.rerun()
        return
    if state is None:
        st.warning("No checkpoint for this run yet.")
        return
    if state["error"]:
        st.error(state["error"], icon=":material/error:")
    if state["warnings"]:
        with st.expander(
            f"{len(state['warnings'])} validation warning(s) kept after retries",
            icon=":material/warning:",
        ):
            for w in state["warnings"]:
                st.write(f"- `{w['code']}` {w['path']}: {w['message']}")

    if state["diagnosis"]:
        with st.container(key="pp-section-diagnosis"):
            st.subheader("Diagnosis")
            st.caption("Why the agent thinks cases wait. Each claim cites the evidence beside it.")
            for n, h in enumerate(state["diagnosis"]["hypotheses"], start=1):
                _hypothesis(n, h, evidence)

    if detail["proposals"]:
        with st.container(key="pp-section-proposals"):
            st.subheader(f"Proposals · revision {state['revision_count']}")
            tabs = st.tabs(
                [
                    f"P{p['index'] + 1}. {_short(p['proposal']['title'])}"
                    for p in detail["proposals"]
                ]
            )
            for tab, p in zip(tabs, detail["proposals"], strict=True):
                with tab:
                    _proposal(run_id, slug, p, evidence)
        _compare_models(run_id, slug, detail)

    if detail["awaiting_review"]:
        _decision_form(run_id, slug, detail)
    if detail["decisions"]:
        with st.container(key="pp-section-decisions"):
            st.subheader("Decision log")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Round": d["round"],
                            "Decision": d["decision"],
                            "Comment": d["comment"] or "—",
                            "Analyst": d["analyst"] or "—",
                            "Proposals": "; ".join(d["proposal_titles"]),
                            "At (UTC)": d["decided_at"],
                        }
                        for d in detail["decisions"]
                    ]
                ),
                hide_index=True,
            )


def _short(title: str, limit: int = 42) -> str:
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def _hypothesis(n: int, h: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> None:
    with st.container(border=True):
        claim, sources = st.columns([3, 2], gap="large")
        with claim:
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.markdown(f"**H{n}**")
                st.badge(
                    f"{h['confidence']} confidence", color=_CONFIDENCE.get(h["confidence"], "gray")
                )
            st.markdown(h["description"])
            if h["alternative_explanations"]:
                st.caption("Could also be: " + " · ".join(h["alternative_explanations"]))
        with sources:
            _citations(h["evidence_ids"], evidence)


def _citations(ids: list[str], evidence: dict[str, dict[str, Any]]) -> None:
    """The cited items with their code-written summaries, in the margin of the claim."""
    st.caption(f"Cites {len(ids)} evidence item(s)")
    for evidence_id in ids:
        item = evidence.get(evidence_id)
        st.markdown(
            f"`{evidence_id}`  \n"
            + (
                f"<small>{item['summary']}</small>"
                if item
                else "<small>Not in this segment's evidence.</small>"
            ),
            unsafe_allow_html=True,
        )


def _proposal(
    run_id: str, slug: str, p: dict[str, Any], evidence: dict[str, dict[str, Any]]
) -> None:
    proposal, validation = p["proposal"], p["validation"]
    st.markdown(f"#### {proposal['title']}")
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.badge(proposal["heuristic_id"], color="gray", icon=":material/menu_book:")
        if validation and validation["soundness"]:
            sound = validation["soundness"]["sound"]
            st.badge(
                "Sound model" if sound else "Not sound",
                color="green" if sound else "red",
                icon=":material/verified:" if sound else ":material/error:",
                help=validation["soundness"]["method"],
            )
    if p["edit"] and p["edit"]["status"] != "applied":
        st.error(f"Edits could not be applied: {p['edit']['message']}", icon=":material/error:")
    what, effect = st.columns([3, 2], gap="large")
    with what:
        st.markdown("**Changes**\n" + "".join(f"\n- {op}" for op in p["operations_text"]))
        st.markdown(f"**Rationale.** {proposal['rationale']}")
        st.markdown(f"**Expected effect.** {proposal['expected_effect']}")
    with effect:
        _estimate(p, validation)
    _proposal_ladder(run_id, slug, proposal, evidence)
    risks, assumptions, sources = st.columns(3, gap="large")
    with risks:
        st.markdown("**Risks**" + "".join(f"\n- {r}" for r in proposal["risks"]))
    with assumptions:
        st.markdown("**Assumptions**" + "".join(f"\n- {a}" for a in proposal["assumptions"]))
    with sources:
        _citations(proposal["evidence_ids"], evidence)


def _estimate(p: dict[str, Any], validation: dict[str, Any] | None) -> None:
    st.markdown("**ESTIMATE**")
    if validation is None:
        st.caption("Not validated.")
        return
    e = validation["estimate"]
    if not e:
        st.caption(validation["message"] or "No estimate for this proposal.")
        return
    change = e["median_change_share"]
    delta = None
    direction = None
    if change is not None and e["median_change_seconds"]:
        sign = "−" if e["median_change_seconds"] < 0 else "+"
        saved = fmt_duration(abs(e["median_change_seconds"]))
        delta = f"{sign}{fmt_share(abs(change))} ({sign}{saved})"
        direction = "good" if e["median_change_seconds"] < 0 else "bad"
    elif change is not None:
        delta = "no change"
    widgets.compare_bars(
        [
            {
                "label": "Median throughput",
                "now": e["as_is"]["median_seconds"],
                "estimate": e["to_be"]["median_seconds"],
                "now_text": fmt_duration(e["as_is"]["median_seconds"]),
                "estimate_text": fmt_duration(e["to_be"]["median_seconds"]),
                "delta": delta,
                "direction": direction,
            },
            {
                "label": "P90 throughput",
                "now": e["as_is"]["p90_seconds"],
                "estimate": e["to_be"]["p90_seconds"],
                "now_text": fmt_duration(e["as_is"]["p90_seconds"]),
                "estimate_text": fmt_duration(e["to_be"]["p90_seconds"]),
            },
        ],
        key=f"estimate-{p['index']}-{e['case_count']}",
    )
    st.caption(
        f"Replay of observed waits, not a simulation. Touches {e['cases_affected']:,} of "
        f"{e['case_count']:,} cases ({fmt_share(e['cases_affected_share'])})."
    )
    with st.popover("Assumptions and limits", icon=":material/info:"):
        for a in e["assumptions"]:
            st.markdown(f"- {a}")
        for n in e["not_estimated"]:
            st.markdown(f"- Not estimated: {n}")


def _touched(proposal: Mapping[str, Any]) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Rung marks and inserted steps from a proposal's edit operations."""
    marks: dict[str, tuple[str, str]] = {}
    added: dict[str, str] = {}
    for op in proposal["edit_operations"]:
        kind = op["op"]
        if kind == "automate_activity":
            marks[op["activity"]] = ("automated", "automated")
        elif kind == "remove_activity":
            marks[op["activity"]] = ("removed", "removed")
        elif kind in ("add_activity", "add_check"):
            added[op["after"]] = op["name"]
        elif kind == "reorder_activities":
            marks.setdefault(op["activity"], ("", "moved"))
        elif kind in ("parallelize_activities", "merge_activities"):
            for activity in op["activities"]:
                marks.setdefault(activity, ("", "parallel" if kind.startswith("par") else "merged"))
    return marks, added


def _proposal_ladder(
    run_id: str, slug: str, proposal: Mapping[str, Any], evidence: dict[str, dict[str, Any]]
) -> None:
    """The most frequent listed path the proposal touches, with its rungs marked."""
    marks, added = _touched(proposal)
    touched = set(marks) | set(added)
    variants = sorted(
        (i for i in evidence.values() if i["type"] == "variant" and "activities" in i["metrics"]),
        key=lambda i: i["metrics"]["rank"],
    )
    path = next((v for v in variants if touched & set(v["metrics"]["activities"])), None)
    if path is None:
        return
    bottlenecks = [
        b for b in evidence.values() if b["type"] == "bottleneck" and "source" in b["metrics"]
    ]
    ids = {(b["metrics"]["source"], b["metrics"]["target"]): b["id"] for b in bottlenecks}
    rank = int(path["metrics"]["rank"])
    waits = ladder.path_waits(run_id, slug, rank)
    st.markdown("**Where it acts**")
    st.caption(
        f"{ladder.path_label(path['metrics'])} ({path['id']}), the most frequent listed path "
        "this proposal touches. Bars are today's waits; the marks show what would change."
    )
    widgets.wait_ladder(
        ladder.rows(waits, ids, marks={a: m for a, m in marks.items()}, added=added),
        waits["mean_throughput_seconds"],
        "",
        key=f"proposal-ladder-{run_id}-{slug}-{rank}-{proposal['title']}",
    )


def _compare_models(run_id: str, slug: str, detail: dict[str, Any]) -> None:
    applied = [p for p in detail["proposals"] if p["edit"] and p["edit"]["status"] == "applied"]
    if not applied:
        return
    with st.container(key="pp-section-models"):
        title, picker = st.columns([3, 2], vertical_alignment="bottom")
        title.subheader("As-is and to-be model")
        index = picker.selectbox(
            "Proposal",
            [p["index"] for p in applied],
            format_func=lambda i: f"P{i + 1}. {detail['proposals'][i]['proposal']['title']}",
            label_visibility="collapsed",
        )
        diff = detail["proposals"][index]["edit"]["diff"] or {}
        if diff.get("changes"):
            st.caption(
                "Outlined in both models: " + "; ".join(diff["changes"]) + ". Scroll to zoom, "
                "drag to pan."
            )
        height = settings()["bpmn_height_px"]
        # Stacked, not side by side: discovered models are wide and flat, and a half-width
        # frame shrinks their labels below reading size.
        st.markdown("**As-is**")
        bpmn_viewer(
            client().as_is_bpmn(run_id, slug),
            height,
            key=f"cmp-asis-{run_id}-{slug}-{index}",
            highlight=[*diff.get("removed", []), *diff.get("automated", [])],
        )
        st.markdown(f"**To-be, proposal {index + 1}**")
        xml = client().to_be_bpmn(run_id, slug, detail["agent_run_id"], index)
        bpmn_viewer(
            xml,
            height,
            key=f"cmp-tobe-{detail['agent_run_id']}-{index}",
            highlight=[
                *diff.get("added", []),
                *diff.get("checks_added", []),
                *(a + AUTOMATED_SUFFIX for a in diff.get("automated", [])),
            ],
        )


def _decision_form(run_id: str, slug: str, detail: dict[str, Any]) -> None:
    loops_left = detail["state"]["revision_count"] < detail["max_revision_loops"]
    count = len(detail["proposals"])
    with st.container(border=True, key="pp-decision"):
        st.subheader(f"Your decision · round {detail['review_round']}")
        st.caption(
            f"Applies to all {count} proposal(s) above. ProcessPilot records it and never "
            "executes a change. "
            + (
                "Rejecting with a comment asks the agent for revised proposals; rejecting "
                "without one ends the run."
                if loops_left
                else "No revisions are left: rejecting ends this run."
            )
        )
        with st.form("decision", clear_on_submit=True, border=False):
            # No default: the analyst makes the call, the form never makes it for them.
            decision = st.segmented_control(
                "Decision",
                ["approve", "reject"],
                default=None,
                format_func=lambda d: "Approve" if d == "approve" else "Reject",
            )
            comment = st.text_area(
                "Comment",
                st.session_state.pop(_DRAFT, ""),
                placeholder="What should change, or why these proposals are acceptable",
            )
            analyst = st.text_input(
                "Your name, for the decision log", st.session_state.get(Keys.analyst, "")
            )
            submitted = st.form_submit_button(
                "Submit decision", type="primary", icon=":material/send:"
            )
            if st.session_state.pop(_MISSING, False):
                st.error("Choose Approve or Reject first.", icon=":material/touch_app:")
            if submitted and decision is None:
                # The form clears on submit; keep what was typed and ask again.
                st.session_state[_DRAFT] = comment
                st.session_state[_MISSING] = True
                st.rerun()
            elif submitted:
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
