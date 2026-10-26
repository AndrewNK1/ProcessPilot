"""Page 2: the evidence of one segment and its as-is model.

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

from processpilot.app.bpmn import bpmn_viewer
from processpilot.app.client import ApiError
from processpilot.app.ui import (
    api_error,
    client,
    fmt_duration,
    fmt_share,
    page,
    pick_run,
    pick_segment,
    settings,
)


def _bottleneck(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "transition": f"{m['source']} → {m['target']}",
        "cases": fmt_share(m["case_share"]),
        "median wait": fmt_duration(m["median_wait_seconds"]),
        "P90 wait": fmt_duration(m["p90_wait_seconds"]),
        "occurrences": m["frequency"],
    }


def _activity(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "activity": m["activity"],
        "cases": fmt_share(m["case_share"]),
        "median wait before": fmt_duration(m["median_wait_seconds"]),
        "P90 wait before": fmt_duration(m["p90_wait_seconds"]),
    }


def _variant(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "cases": fmt_share(m["case_share"]),
        "median throughput": fmt_duration(m["median_throughput_seconds"]),
        "path": " → ".join(m["activities"]),
    }


def _rework(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "activity": m["activity"],
        "cases": fmt_share(m["case_share"]),
        "extra executions": m["extra_occurrences"],
        "extra median time": fmt_duration(m["extra_median_throughput_seconds"]),
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
    """ID, the code-written summary, and the type's key metrics as columns."""
    rows = []
    for item in items:
        row: dict[str, Any] = {"id": item["id"]}
        if columns is not None and item["metrics"].get("available", True):
            try:
                row.update(columns(item["metrics"]))
            except KeyError:
                pass  # a notice item (e.g. "not performed") has no metrics to tabulate
        row["summary"] = item["summary"]
        rows.append(row)
    return pd.DataFrame(rows)


def render() -> None:
    """The Evidence page."""
    st.title("Evidence")
    st.caption("What mining found, per segment. Every number is computed, none is generated.")
    try:
        run = pick_run()
        if run is None:
            return
        _preset_notice(run)
        segment = pick_segment(run)
        if segment is None:
            return
        _kpis(segment)
        _quality(run)
        items = client().evidence(run["run_id"], segment["slug"])
        tabs = st.tabs([label for _, label, _ in _TABS] + ["As-is model", "All items"])
        for tab, (type_, _, columns) in zip(tabs, _TABS, strict=False):
            with tab:
                selected = [i for i in items if i["type"] == type_]
                if selected:
                    st.dataframe(evidence_table(selected, columns), hide_index=True)
                else:
                    st.info("No items of this type for this segment.")
        with tabs[len(_TABS)]:
            if segment["has_model"]:
                xml = client().as_is_bpmn(run["run_id"], segment["slug"])
                bpmn_viewer(
                    xml, settings()["bpmn_height_px"], key=f"asis-{run['run_id']}-{segment['slug']}"
                )
            else:
                st.info("This run has no as-is model for the segment (mined before Phase 4).")
        with tabs[len(_TABS) + 1]:
            st.dataframe(
                pd.DataFrame([{k: i[k] for k in ("id", "type", "summary")} for i in items]),
                hide_index=True,
            )
        st.page_link(page("redesign"), label="Diagnose and redesign this segment →")
    except ApiError as exc:
        api_error(exc)


def _preset_notice(run: dict[str, Any]) -> None:
    """Runs found on disk need their preset named before the agent can run."""
    if run["preset"] is not None:
        return
    st.warning(
        "This run was found on disk (e.g. mined in a notebook). Name the preset it was mined "
        "with; the agent needs its constraints and domain knowledge."
    )
    names = [p["name"] for p in client().presets()]
    with st.form("run_preset"):
        name = st.selectbox("Preset", names)
        if st.form_submit_button("Save"):
            client().set_run_preset(run["run_id"], name)
            st.rerun()


def _kpis(segment: dict[str, Any]) -> None:
    kpi = segment["kpi"]
    if kpi is None:
        return
    m = kpi["metrics"]
    cols = st.columns(5)
    cols[0].metric("Cases", f"{m['case_count']:,}")
    cols[1].metric("Events", f"{m['event_count']:,}")
    cols[2].metric("Median throughput", fmt_duration(m["median_throughput_seconds"]))
    cols[3].metric("Mean throughput", fmt_duration(m["mean_throughput_seconds"]))
    cols[4].metric("P90 throughput", fmt_duration(m["p90_throughput_seconds"]))
    st.caption(f"[{kpi['id']}] {kpi['summary']}")


def _quality(run: dict[str, Any]) -> None:
    quality = run["data_quality"]
    if quality is None:
        return
    with st.expander("Data quality and cleaning of this run"):
        if quality["cleaning_log"]:
            st.dataframe(pd.DataFrame(quality["cleaning_log"]), hide_index=True)
        else:
            st.write("Nothing was removed before mining.")
        st.write(f"{quality['issue_count']} data-quality issue(s) were reported.")
