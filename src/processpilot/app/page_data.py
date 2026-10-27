"""Page 1: choose data, confirm the mapping, review data quality, choose cleaning, mine.

The order on this page is the order of the method: nothing is mined before the
analyst has seen the mapping and the data-quality report, and nothing is
removed unless the analyst ticks it (the preset's suggestion is only the
default). A step track at the top says which step is open; finished steps
fold into a one-line summary that can be reopened.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from processpilot.app import widgets
from processpilot.app.client import ApiError
from processpilot.app.ui import Keys, api_error, client, fmt_share, page, page_header, wait_for_job

_ROLES_REQUIRED = ("case_id", "activity", "timestamp")
_ROLES_OPTIONAL = ("resource", "segment_by")
_ROLE_LABELS = {
    "case_id": "Case id",
    "activity": "Activity",
    "timestamp": "Timestamp",
    "resource": "Resource (optional)",
    "segment_by": "Segment by (optional, a case-level attribute)",
}
_NONE = "(none)"
_TIMEZONE_POLICIES = ("utc", "local", "none")
_CLEANING_LABELS = {
    "drop_cases_with_missing_values": "Drop cases with a missing case id, activity or timestamp",
    "drop_cases_with_out_of_range_timestamps": "Drop cases with timestamps outside the "
    "plausible range",
    "drop_single_event_cases": "Drop cases with fewer events than the preset's minimum",
    "drop_exact_duplicate_events": "Drop exact duplicate events (keeps the first)",
}
_UPLOAD_TYPES = ["csv", "xes", "xlsx", "xls", "parquet"]
_SOURCES = {"preset": "Configured preset", "upload": "Upload a file", "earlier": "Earlier dataset"}


def render() -> None:
    """The Data page."""
    page_header(
        "Data",
        "Choose an event log, confirm how its columns map, check its quality, then mine it.",
    )
    try:
        dataset_id = st.session_state.get(Keys.dataset)
        dataset = _dataset_or_none(dataset_id)
        _track(dataset)
        with st.container(key="pp-section-source"):
            st.subheader("Event log")
            if dataset is None:
                _choose_dataset()
                return
            _dataset_summary(dataset)
        with st.container(key="pp-section-mapping"):
            st.subheader("Column mapping")
            if not _mapping_step(dataset):
                return
        dataset = client().dataset(dataset["id"])
        with st.container(key="pp-section-quality"):
            st.subheader("Data quality")
            if not _quality_step(dataset):
                return
        with st.container(key="pp-section-mine"):
            st.subheader("Cleaning and mining")
            _cleaning_and_mining_step(client().dataset(dataset["id"]))
    except ApiError as exc:
        api_error(exc)


def _dataset_or_none(dataset_id: str | None) -> dict[str, Any] | None:
    """The selected dataset, or ``None`` when nothing (or a stale id) is selected."""
    if dataset_id is None:
        return None
    try:
        return client().dataset(dataset_id)
    except ApiError as exc:
        if exc.status == 404:
            st.session_state.pop(Keys.dataset, None)
            return None
        raise


def _track(dataset: dict[str, Any] | None) -> None:
    """Where the analyst is: each step done, open, or not reached yet."""
    has_mapping = dataset is not None and dataset["mapping"] is not None
    has_quality = dataset is not None and dataset["quality"] is not None
    mined = dataset is not None and bool(dataset["runs"])
    states = [dataset is not None, has_mapping, has_quality, mined]
    current = states.index(False) if False in states else None
    details = [
        dataset["name"] if dataset else "Preset, upload or earlier",
        "Confirmed" if has_mapping else "",
        (f"{dataset['quality']['report']['issue_count']} issue(s) found" if has_quality else ""),
        f"{len(dataset['runs'])} run(s) mined" if mined else "",
    ]
    labels = ["Event log", "Column mapping", "Data quality", "Clean and mine"]
    steps = []
    for i, (label, done, detail) in enumerate(zip(labels, states, details, strict=True)):
        state = "done" if done else "current" if i == current else "todo"
        steps.append((label, state, detail))
    widgets.step_track(steps)


def _choose_dataset() -> None:
    source = st.segmented_control(
        "Start from",
        list(_SOURCES),
        default="preset",
        required=True,
        format_func=_SOURCES.get,
        key="pp-source",
    )
    if source == "preset":
        presets = client().presets()
        usable = [p for p in presets if p["source_available"]]
        if not usable:
            st.warning("No preset has a reachable source file. Upload a file instead.")
        else:
            by_name = {p["name"]: p for p in usable}
            name = st.selectbox("Preset", list(by_name))
            st.caption(by_name[name]["description"])
            if st.button("Use this preset", type="primary", icon=":material/arrow_forward:"):
                st.session_state[Keys.dataset] = client().create_preset_dataset(name)["id"]
                st.rerun()
        unavailable = [p for p in presets if p["has_mapping"] and not p["source_available"]]
        if unavailable:
            with st.expander(f"{len(unavailable)} preset(s) whose source file is not available"):
                for p in unavailable:
                    st.write(f"**{p['name']}**: {p['source_problem']}")
    elif source == "upload":
        presets = [p["name"] for p in client().presets()]
        uploaded = st.file_uploader("Event log", type=_UPLOAD_TYPES)
        base = st.selectbox(
            "Settings preset",
            presets,
            index=presets.index("generic") if "generic" in presets else 0,
            help="Rules and thresholds come from this preset; the column mapping comes from you.",
        )
        if st.button("Upload", type="primary", disabled=uploaded is None, icon=":material/upload:"):
            dataset = client().upload_dataset(uploaded.name, uploaded.getvalue(), base)
            st.session_state[Keys.dataset] = dataset["id"]
            if wait_for_job(client().profile_dataset(dataset["id"]), "Profiling the file"):
                st.rerun()
    else:
        datasets = client().datasets()
        if not datasets:
            st.caption("No earlier dataset yet.")
            return
        ids = [d["id"] for d in datasets]
        by_id = {d["id"]: d for d in datasets}
        chosen = st.selectbox(
            "Dataset",
            ids,
            format_func=lambda i: (
                f"{by_id[i]['name']} · {by_id[i]['kind']} · {by_id[i]['created_at'][:16]}"
            ),
        )
        if st.button("Continue with this dataset", type="primary"):
            st.session_state[Keys.dataset] = chosen
            st.rerun()


def _dataset_summary(dataset: dict[str, Any]) -> None:
    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        with st.container(gap=None):
            st.markdown(f"**{dataset['name']}**")
            st.caption(
                f"{dataset['kind']} · settings preset `{dataset['preset']}` · "
                f"`{dataset['source_path']}`"
            )
        if st.button("Choose another log", icon=":material/swap_horiz:", width="content"):
            st.session_state.pop(Keys.dataset, None)
            st.rerun()


def _mapping_step(dataset: dict[str, Any]) -> bool:
    """Show or ask for the mapping; True once one is confirmed."""
    if dataset["kind"] == "preset":
        st.caption("The preset fixes the mapping; this is what each column means.")
        st.dataframe(_mapping_frame(dataset["mapping"]), hide_index=True, width="content")
        return True
    profile = dataset["profile"]
    if profile is None:
        if st.button("Profile the file", type="primary"):
            if wait_for_job(client().profile_dataset(dataset["id"]), "Profiling the file"):
                st.rerun()
        return False
    confirmed = dataset["mapping"] is not None
    with st.expander(
        "Mapping confirmed · " + _mapping_line(dataset["mapping"])
        if confirmed
        else "Map the columns",
        expanded=not confirmed,
        icon=":material/check_circle:" if confirmed else ":material/table_view:",
    ):
        _mapping_form(dataset, profile)
    if not confirmed:
        st.caption("Confirm a mapping to continue.")
    return confirmed


def _mapping_frame(mapping: dict[str, Any]) -> pd.DataFrame:
    labels = {**_ROLE_LABELS, "timestamp_format": "Timestamp format"}
    labels["timestamp_timezone_policy"] = "Timezone policy"
    return pd.DataFrame(
        [
            {"Role": label.split(" (")[0], "Column or setting": mapping.get(role) or "—"}
            for role, label in labels.items()
            if role in mapping
        ]
    )


def _mapping_line(mapping: dict[str, Any] | None) -> str:
    if not mapping:
        return ""
    return " · ".join(f"{role} = {mapping[role]}" for role in _ROLES_REQUIRED if mapping.get(role))


def _mapping_form(dataset: dict[str, Any], profile: dict[str, Any]) -> None:
    st.caption(f"{profile['row_count']:,} rows, {len(profile['columns'])} columns")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Column": c["name"],
                    "Type": c["dtype"],
                    "Empty": fmt_share(c["null_share"]),
                    "Distinct": c["distinct"],
                    "Examples": ", ".join(c["examples"]),
                }
                for c in profile["columns"]
            ]
        ),
        hide_index=True,
        column_config={
            "Empty": st.column_config.TextColumn(alignment="right"),
            "Distinct": st.column_config.NumberColumn(format="localized"),
        },
    )
    current = dataset["mapping"] or {}
    columns = [c["name"] for c in profile["columns"]]
    chosen: dict[str, Any] = {}
    with st.form("mapping", border=False):
        st.caption("Suggestions are ranked by name and content; nothing is picked without you.")
        left, right = st.columns(2)
        for n, role in enumerate((*_ROLES_REQUIRED, *_ROLES_OPTIONAL)):
            ranked = profile["suggestions"].get(role, [])
            confidence = {c["column"]: c["confidence"] for c in ranked}
            options = [c["column"] for c in ranked] + [c for c in columns if c not in confidence]
            if role in _ROLES_OPTIONAL:
                options = [_NONE, *options]
            default = current.get(role) or (
                ranked[0]["column"] if ranked and role in _ROLES_REQUIRED else _NONE
            )
            chosen[role] = (left if n % 2 == 0 else right).selectbox(
                _ROLE_LABELS[role],
                options,
                index=options.index(default) if default in options else 0,
                format_func=lambda c, conf=confidence: (
                    f"{c} (suggested, {conf[c]:.0%})" if c in conf else c
                ),
                key=f"map_{role}",
            )
        fmt = right.text_input(
            "Timestamp format (optional)",
            current.get("timestamp_format") or "",
            placeholder="e.g. %d.%m.%Y %H:%M — empty means detect",
        )
        policy = left.selectbox(
            "Timezone policy",
            _TIMEZONE_POLICIES,
            index=_TIMEZONE_POLICIES.index(current.get("timestamp_timezone_policy", "utc")),
            help="utc: convert offsets to UTC, then drop them (recommended).",
        )
        submitted = st.form_submit_button("Confirm mapping", type="primary")
    if submitted:
        mapping = {k: (None if v == _NONE else v) for k, v in chosen.items()}
        mapping["timestamp_format"] = fmt.strip() or None
        mapping["timestamp_timezone_policy"] = policy
        client().set_mapping(dataset["id"], mapping)
        st.rerun()


def _quality_step(dataset: dict[str, Any]) -> bool:
    """Run or show the data-quality report; True once there is one."""
    if dataset["quality"] is None:
        st.caption(
            "Checks nulls, unparseable and implausible timestamps, duplicates, single-event "
            "cases and more. It only reports; nothing in the data is changed."
        )
        if st.button("Check data quality", type="primary", icon=":material/fact_check:"):
            if wait_for_job(client().validate_dataset(dataset["id"]), "Checking data quality"):
                st.rerun()
        return False
    report = dataset["quality"]["report"]
    severities = pd.Series([i["severity"] for i in report["issues"]], dtype="object")
    widgets.kpi_strip(
        [
            {"label": "Events", "value": f"{report['event_count']:,}"},
            {"label": "Cases", "value": f"{report['case_count']:,}"},
            {"label": "Errors", "value": str(int((severities == "error").sum()))},
            {"label": "Warnings", "value": str(int((severities == "warn").sum()))},
            {"label": "Notes", "value": str(int((severities == "info").sum()))},
        ],
        key=f"quality-{dataset['id']}",
    )
    if report["issues"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Severity": i["severity"],
                        "Check": i["code"],
                        "Finding": i["message"],
                        "Count": i["count"],
                        "Share of events": fmt_share(i["share"]),
                    }
                    for i in report["issues"]
                ]
            ),
            hide_index=True,
            column_config={
                "Finding": st.column_config.TextColumn(width="large"),
                "Count": st.column_config.NumberColumn(format="localized"),
                "Share of events": st.column_config.TextColumn(alignment="right"),
            },
        )
        examples = [i for i in report["issues"] if i["example_rows"]]
        if examples:
            with st.expander(f"Example rows for {len(examples)} issue(s)"):
                for issue in examples:
                    st.markdown(f"**{issue['code']}**")
                    st.dataframe(pd.DataFrame(issue["example_rows"]), hide_index=True)
    st.caption("The report only reports. Nothing has been changed in the data.")
    return True


def _cleaning_and_mining_step(dataset: dict[str, Any]) -> None:
    recommendation = dataset["quality"].get("recommendation")
    advice = {a["option"]: a for a in (recommendation or {}).get("advice", [])}
    starts = {
        "recommended": "ProcessPilot's recommendation",
        "preset": f"The {dataset['preset']} preset",
        "none": "Remove nothing",
    }
    if not advice:
        starts.pop("recommended")
        st.caption("Run the data-quality check again to get ProcessPilot's recommendation.")
    start = st.segmented_control(
        "Start from",
        list(starts),
        default=next(iter(starts)),
        required=True,
        format_func=starts.get,
        key=f"pp-clean-start-{dataset['id']}",
        help="Only the starting point: you can change every option below.",
    )
    defaults = {
        "recommended": {k: a["recommended"] for k, a in advice.items()},
        "preset": dataset["suggested_cleaning"],
        "none": {},
    }[start]
    with st.form("cleaning"):
        st.caption("Every removal is logged in the run.")
        policy = {}
        for key, label in _CLEANING_LABELS.items():
            tip = advice.get(key)
            policy[key] = st.checkbox(
                label,
                value=bool(defaults.get(key)),
                key=f"clean_{dataset['id']}_{start}_{key}",
            )
            if tip:
                verdict = "Recommended" if tip["recommended"] else "Not recommended"
                cost = (
                    f" Alone, this removes {tip['cases_removed']:,} case(s), "
                    f"{tip['rows_removed']:,} event(s) ({fmt_share(tip['event_share_removed'])})."
                    if tip["rows_removed"]
                    else ""
                )
                st.caption(f"**{verdict}.** {tip['reason']}{cost}")
        submitted = st.form_submit_button(
            "Mine this log", type="primary", icon=":material/play_arrow:"
        )
    if advice and any(policy[k] != a["recommended"] for k, a in advice.items()):
        st.caption(":material/info: Your selection differs from ProcessPilot's recommendation.")
    if submitted:
        job_id, run_id = client().mine_dataset(dataset["id"], policy)
        if wait_for_job(job_id, f"Mining run {run_id}"):
            st.session_state[Keys.run] = run_id
            st.session_state.pop(Keys.variant, None)
            st.success(f"Run {run_id} is ready.", icon=":material/check_circle:")
            st.page_link(
                page("evidence"), label="Open its evidence", icon=":material/arrow_forward:"
            )
    elif dataset["runs"]:
        st.caption("Runs mined from this log: " + ", ".join(f"`{r}`" for r in dataset["runs"]))
        st.page_link(page("evidence"), label="Open the evidence", icon=":material/arrow_forward:")
