"""Page 2: the evidence of one segment and its as-is model.

The page answers "where do the days go?" first: the segment's headline
figures, then the wait ladder of one path (by default the most frequent path
through the worst bottleneck), then the ranked evidence tables.

Every row shows its evidence ID: these are the IDs the agent must cite, so an
analyst can check any claim in a proposal against the table it came from.
Every number here was computed by pandas/pm4py; nothing on this page comes
from a language model.
"""

from __future__ import annotations

from collections.abc import Callable
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
    duration_parts,
    fmt_duration,
    fmt_share,
    page,
    page_header,
    pick_run,
    pick_segment,
    settings,
)

_ID = st.column_config.TextColumn("ID", width="medium")
_SHARE = st.column_config.ProgressColumn(
    "Cases", format="%.1f%%", min_value=0, max_value=100, width=150
)
_RIGHT = {"alignment": "right"}
_SUMMARY = st.column_config.TextColumn("Summary (written by code)", width="large")


def _bottleneck(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "Transition": f"{m['source']} → {m['target']}",
        "Cases": m["case_share"] * 100,
        "Median wait": fmt_duration(m["median_wait_seconds"]),
        "P90 wait": fmt_duration(m["p90_wait_seconds"]),
        "Occurrences": m["frequency"],
    }


def _activity(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "Activity": m["activity"],
        "Cases": m["case_share"] * 100,
        "Median wait before": fmt_duration(m["median_wait_seconds"]),
        "P90 wait before": fmt_duration(m["p90_wait_seconds"]),
    }


def _variant(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "Cases": m["case_share"] * 100,
        "Median throughput": fmt_duration(m["median_throughput_seconds"]),
        "Path": " → ".join(m["activities"]),
    }


def _rework(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "Activity": m["activity"],
        "Cases": m["case_share"] * 100,
        "Extra executions": m["extra_occurrences"],
        "Extra median time": fmt_duration(m["extra_median_throughput_seconds"]),
    }


_COLUMNS: dict[str, Any] = {
    "Cases": _SHARE,
    "Median wait": st.column_config.TextColumn(**_RIGHT),
    "P90 wait": st.column_config.TextColumn(**_RIGHT),
    "Median wait before": st.column_config.TextColumn(**_RIGHT),
    "P90 wait before": st.column_config.TextColumn(**_RIGHT),
    "Median throughput": st.column_config.TextColumn(**_RIGHT),
    "Extra median time": st.column_config.TextColumn(**_RIGHT),
    "Occurrences": st.column_config.NumberColumn(format="localized", width="small"),
    "Extra executions": st.column_config.NumberColumn(format="localized", width="small"),
    "Transition": st.column_config.TextColumn(width=380),
    "Path": st.column_config.TextColumn(width="large"),
}

_TABS: list[tuple[str, str, Callable[[dict[str, Any]], dict[str, Any]] | None]] = [
    ("bottleneck", "Bottlenecks", _bottleneck),
    ("activity", "Waits before activities", _activity),
    ("variant", "Variants", _variant),
    ("rework", "Rework", _rework),
    ("resource", "Resources & handovers", None),
    ("conformance", "Conformance", None),
]


def evidence_table(items: list[dict[str, Any]], columns: Callable | None) -> pd.DataFrame:
    """ID and the type's key metrics as columns; the summary when there are no metrics.

    WHY the summary only as a fallback: with metrics it repeats them in a sentence
    too long for a cell. It stays in "All items" and beside every citation.
    """
    rows = []
    for item in items:
        row: dict[str, Any] = {"ID": item["id"]}
        tabulated = False
        if columns is not None and item["metrics"].get("available", True):
            try:
                row.update(columns(item["metrics"]))
                tabulated = True
            except KeyError:
                pass  # a notice item (e.g. "not performed") has no metrics to tabulate
        if not tabulated:
            row["Summary (written by code)"] = item["summary"]
        rows.append(row)
    return pd.DataFrame(rows)


def show_table(frame: pd.DataFrame) -> None:
    """An evidence table with the shared column formats."""
    config = {"ID": _ID, "Summary (written by code)": _SUMMARY}
    config.update({c: v for c, v in _COLUMNS.items() if c in frame.columns})
    st.dataframe(frame, hide_index=True, column_config=config)


def render() -> None:
    """The Evidence page."""
    header = st.container()
    try:
        scope_segment, scope_run = st.columns([5, 2], vertical_alignment="bottom")
        with scope_run:
            run = pick_run()
        if run is None:
            with header:
                page_header("Evidence", "What mining found, per segment.")
            return
        with scope_segment:
            segment = pick_segment(run)
        if segment is None:
            return
        items = client().evidence(run["run_id"], segment["slug"])
        by_type: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_type.setdefault(item["type"], []).append(item)
        with header:
            page_header(f"Evidence · {segment['name']}", _finding(by_type.get("bottleneck", [])))
        _preset_notice(run)
        _kpis(segment)
        _wait_ladder(run["run_id"], segment["slug"], by_type)
        with st.container(key="pp-section"):
            st.subheader("All findings")
            st.caption(
                "Ranked by impact (median wait × share of cases). Each ID is what the agent "
                "must cite; every number here was computed, none generated."
            )
            tabs = st.tabs([label for _, label, _ in _TABS] + ["As-is model", "All items"])
            for tab, (type_, _, columns) in zip(tabs, _TABS, strict=False):
                with tab:
                    selected = by_type.get(type_, [])
                    if selected:
                        show_table(evidence_table(selected, columns))
                    else:
                        st.caption("No items of this type for this segment.")
            with tabs[len(_TABS)]:
                if segment["has_model"]:
                    st.caption(
                        "Discovered with the inductive miner. Rare activities may be filtered out "
                        "of the model; they still appear in the tables."
                    )
                    bpmn_viewer(
                        client().as_is_bpmn(run["run_id"], segment["slug"]),
                        settings()["bpmn_height_px"],
                        key=f"asis-{run['run_id']}-{segment['slug']}",
                    )
                else:
                    st.caption(
                        "This run has no as-is model for the segment (mined before Phase 4)."
                    )
            with tabs[len(_TABS) + 1]:
                show_table(
                    pd.DataFrame(
                        [
                            {
                                "ID": i["id"],
                                "Type": i["type"],
                                "Summary (written by code)": i["summary"],
                            }
                            for i in items
                        ]
                    )
                )
        _quality(run)
        st.space("small")
        st.page_link(
            page("redesign"),
            label=f"Diagnose and redesign {segment['name']}",
            icon=":material/arrow_forward:",
        )
    except ApiError as exc:
        api_error(exc)


def _finding(bottlenecks: list[dict[str, Any]]) -> str:
    """The one line under the title: the worst wait, with its evidence ID."""
    if not bottlenecks:
        return "What mining found in this segment. No bottleneck was ranked."
    m = bottlenecks[0]["metrics"]
    return (
        f"Worst wait: {m['source']} → {m['target']}, median "
        f"{fmt_duration(m['median_wait_seconds'])} in {fmt_share(m['case_share'])} of cases "
        f"({bottlenecks[0]['id']})."
    )


def _preset_notice(run: dict[str, Any]) -> None:
    """Runs found on disk need their preset named before the agent can run."""
    if run["preset"] is not None:
        return
    with st.container(border=True):
        st.markdown("**Name the preset this run was mined with**")
        st.caption(
            "This run was found on disk (for example mined in a notebook). The agent needs the "
            "preset's constraints and domain knowledge before it can redesign."
        )
        names = [p["name"] for p in client().presets()]
        with st.form("run_preset", border=False):
            name = st.selectbox("Preset", names)
            if st.form_submit_button("Save preset"):
                client().set_run_preset(run["run_id"], name)
                st.rerun()


def _kpis(segment: dict[str, Any]) -> None:
    kpi = segment["kpi"]
    if kpi is None:
        return
    m = kpi["metrics"]
    items = [
        {"label": "Cases", "value": f"{m['case_count']:,}"},
        {"label": "Events", "value": f"{m['event_count']:,}"},
    ]
    for label, field in (
        ("Median throughput", "median_throughput_seconds"),
        ("Mean throughput", "mean_throughput_seconds"),
        ("P90 throughput", "p90_throughput_seconds"),
    ):
        value, unit = duration_parts(m[field])
        items.append({"label": label, "value": value, "unit": unit})
    widgets.kpi_strip(items, key=f"kpi-{kpi['id']}")


def _wait_ladder(run_id: str, slug: str, by_type: dict[str, list[dict[str, Any]]]) -> None:
    variants = by_type.get("variant", [])
    variants = [v for v in variants if "activities" in v["metrics"]]
    if not variants:
        return
    bottlenecks = [b for b in by_type.get("bottleneck", []) if "source" in b["metrics"]]
    ids = {(b["metrics"]["source"], b["metrics"]["target"]): b["id"] for b in bottlenecks}
    top = next(iter(ids), None)
    ranks = [int(v["metrics"]["rank"]) for v in variants]
    by_rank = {int(v["metrics"]["rank"]): v for v in variants}
    chosen = st.session_state.get(Keys.variant)
    if chosen not in ranks:
        chosen = ladder.default_rank(variants)
    with st.container(key="pp-section-ladder"):
        title, picker = st.columns([3, 2], vertical_alignment="bottom")
        title.subheader("Where the days go")
        rank = picker.selectbox(
            "Path",
            ranks,
            index=ranks.index(chosen),
            format_func=lambda r: ladder.path_label(by_rank[r]["metrics"]),
            label_visibility="collapsed",
        )
        st.session_state[Keys.variant] = rank
        waits = ladder.path_waits(run_id, slug, rank)
        note = by_rank[rank]["id"]
        if top and top in ladder.transitions(waits["activities"]):
            note += f" · passes the worst wait ({ids[top]})"
        st.caption(f"{waits['case_count']:,} cases followed exactly this path · {note}")
        widgets.wait_ladder(
            ladder.rows(waits, ids),
            waits["mean_throughput_seconds"],
            ladder.caption(waits),
            key=f"ladder-{run_id}-{slug}-{rank}",
        )


def _quality(run: dict[str, Any]) -> None:
    quality = run["data_quality"]
    if quality is None:
        return
    removed = len(quality["cleaning_log"])
    label = "Data quality and cleaning of this run · " + (
        f"{removed} cleaning step(s) logged" if removed else "nothing removed"
    )
    with st.expander(label, icon=":material/fact_check:"):
        if quality["cleaning_log"]:
            st.dataframe(pd.DataFrame(quality["cleaning_log"]), hide_index=True)
        else:
            st.write("Nothing was removed before mining.")
        st.caption(f"{quality['issue_count']} data-quality issue(s) were reported.")
