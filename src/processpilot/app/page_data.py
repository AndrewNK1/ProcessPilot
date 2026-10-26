"""Page 1: choose data, confirm the mapping, review data quality, choose cleaning, mine.

The order on this page is the order of the method: nothing is mined before the
analyst has seen the mapping and the data-quality report, and nothing is
removed unless the analyst ticks it (the preset's suggestion is only the
default).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from processpilot.app.client import ApiError
from processpilot.app.ui import Keys, api_error, client, fmt_share, page, wait_for_job

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


def render() -> None:
    """The Data page."""
    st.title("Data")
    st.caption("Choose an event log, confirm how its columns map, check its quality, then mine it.")
    try:
        _choose_dataset()
        dataset_id = st.session_state.get(Keys.dataset)
        if dataset_id is None:
            return
        dataset = client().dataset(dataset_id)
        st.divider()
        _dataset_header(dataset)
        if not _mapping_step(dataset):
            return
        dataset = client().dataset(dataset_id)
        if not _quality_step(dataset):
            return
        _cleaning_and_mining_step(client().dataset(dataset_id))
    except ApiError as exc:
        api_error(exc)


def _choose_dataset() -> None:
    source = st.radio(
        "Start from",
        ["A configured preset", "An uploaded file", "An earlier dataset"],
        horizontal=True,
    )
    if source == "A configured preset":
        presets = client().presets()
        usable = [p for p in presets if p["source_available"]]
        if not usable:
            st.warning("No preset has a reachable source file. Upload a file instead.")
        else:
            names = [p["name"] for p in usable]
            name = st.selectbox(
                "Preset",
                names,
                format_func=lambda n: (
                    f"{n}: {next(p for p in usable if p['name'] == n)['description']}"
                ),
            )
            if st.button("Use this preset", type="primary"):
                st.session_state[Keys.dataset] = client().create_preset_dataset(name)["id"]
                st.rerun()
        unavailable = [p for p in presets if p["has_mapping"] and not p["source_available"]]
        if unavailable:
            with st.expander("Presets whose source file is not available"):
                for p in unavailable:
                    st.write(f"**{p['name']}**: {p['source_problem']}")
    elif source == "An uploaded file":
        presets = [p["name"] for p in client().presets()]
        uploaded = st.file_uploader("Event log", type=_UPLOAD_TYPES)
        base = st.selectbox(
            "Settings preset (rules, thresholds; the mapping comes from you)",
            presets,
            index=presets.index("generic") if "generic" in presets else 0,
        )
        if uploaded is not None and st.button("Upload", type="primary"):
            dataset = client().upload_dataset(uploaded.name, uploaded.getvalue(), base)
            st.session_state[Keys.dataset] = dataset["id"]
            if wait_for_job(client().profile_dataset(dataset["id"]), "Profiling the file"):
                st.rerun()
    else:
        datasets = client().datasets()
        if not datasets:
            st.info("No earlier dataset.")
            return
        ids = [d["id"] for d in datasets]
        by_id = {d["id"]: d for d in datasets}
        current = st.session_state.get(Keys.dataset)
        chosen = st.selectbox(
            "Dataset",
            ids,
            index=ids.index(current) if current in ids else 0,
            format_func=lambda i: (
                f"{by_id[i]['name']} ({by_id[i]['kind']}, {by_id[i]['created_at']})"
            ),
        )
        if st.button("Continue with this dataset"):
            st.session_state[Keys.dataset] = chosen
            st.rerun()


def _dataset_header(dataset: dict[str, Any]) -> None:
    st.subheader(f"Dataset: {dataset['name']}")
    st.caption(
        f"{dataset['kind']} · settings preset `{dataset['preset']}` · `{dataset['source_path']}`"
    )
    if dataset["runs"]:
        st.caption("Runs mined from it: " + ", ".join(dataset["runs"]))


def _mapping_step(dataset: dict[str, Any]) -> bool:
    """Show or ask for the mapping; True once one is confirmed."""
    st.markdown("#### 1 · Column mapping")
    if dataset["kind"] == "preset":
        st.write("Mapping from the preset:")
        st.json(dataset["mapping"], expanded=False)
        return True
    profile = dataset["profile"]
    if profile is None:
        if st.button("Profile the file"):
            if wait_for_job(client().profile_dataset(dataset["id"]), "Profiling the file"):
                st.rerun()
        return False
    st.caption(f"{profile['row_count']:,} rows, {len(profile['columns'])} columns")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "column": c["name"],
                    "type": c["dtype"],
                    "empty": fmt_share(c["null_share"]),
                    "distinct": c["distinct"],
                    "examples": ", ".join(c["examples"]),
                }
                for c in profile["columns"]
            ]
        ),
        hide_index=True,
    )
    current = dataset["mapping"] or {}
    columns = [c["name"] for c in profile["columns"]]
    chosen: dict[str, Any] = {}
    with st.form("mapping"):
        st.caption("Suggestions are ranked by name and content; nothing is picked without you.")
        for role in (*_ROLES_REQUIRED, *_ROLES_OPTIONAL):
            ranked = profile["suggestions"].get(role, [])
            confidence = {c["column"]: c["confidence"] for c in ranked}
            options = [c["column"] for c in ranked] + [c for c in columns if c not in confidence]
            if role in _ROLES_OPTIONAL:
                options = [_NONE, *options]
            default = current.get(role) or (
                ranked[0]["column"] if ranked and role in _ROLES_REQUIRED else _NONE
            )
            chosen[role] = st.selectbox(
                _ROLE_LABELS[role],
                options,
                index=options.index(default) if default in options else 0,
                format_func=lambda c, conf=confidence: (
                    f"{c} (suggested, {conf[c]:.0%})" if c in conf else c
                ),
                key=f"map_{role}",
            )
        fmt = st.text_input(
            "Timestamp format (optional, e.g. %d.%m.%Y %H:%M; empty = detect)",
            current.get("timestamp_format") or "",
        )
        policy = st.selectbox(
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
    if dataset["mapping"] is None:
        st.info("Confirm a mapping to continue.")
        return False
    st.success("Mapping confirmed.")
    return True


def _quality_step(dataset: dict[str, Any]) -> bool:
    """Run or show the data-quality report; True once there is one."""
    st.markdown("#### 2 · Data quality")
    if dataset["quality"] is None:
        if st.button("Check data quality", type="primary"):
            if wait_for_job(client().validate_dataset(dataset["id"]), "Checking data quality"):
                st.rerun()
        return False
    report = dataset["quality"]["report"]
    a, b, c = st.columns(3)
    a.metric("Events", f"{report['event_count']:,}")
    b.metric("Cases", f"{report['case_count']:,}")
    c.metric("Issues", report["issue_count"])
    if report["issues"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "severity": i["severity"],
                        "check": i["code"],
                        "finding": i["message"],
                        "count": i["count"],
                        "share of events": fmt_share(i["share"]),
                    }
                    for i in report["issues"]
                ]
            ),
            hide_index=True,
        )
        with st.expander("Example rows per issue"):
            for issue in report["issues"]:
                if issue["example_rows"]:
                    st.write(f"**{issue['code']}**")
                    st.dataframe(pd.DataFrame(issue["example_rows"]), hide_index=True)
    st.caption("The report only reports. Nothing has been changed in the data.")
    return True


def _cleaning_and_mining_step(dataset: dict[str, Any]) -> None:
    st.markdown("#### 3 · Cleaning and mining")
    suggested = dataset["suggested_cleaning"]
    with st.form("cleaning"):
        st.caption(
            f"Defaults are the `{dataset['preset']}` preset's suggestion. Every removal is "
            "logged in the run."
        )
        policy = {
            key: st.checkbox(label, value=bool(suggested.get(key)), key=f"clean_{key}")
            for key, label in _CLEANING_LABELS.items()
        }
        submitted = st.form_submit_button("Mine this log", type="primary")
    if submitted:
        job_id, run_id = client().mine_dataset(dataset["id"], policy)
        if wait_for_job(job_id, f"Mining run {run_id}"):
            st.session_state[Keys.run] = run_id
            st.success(f"Run {run_id} is ready.")
            st.page_link(page("evidence"), label="Open its evidence →")
