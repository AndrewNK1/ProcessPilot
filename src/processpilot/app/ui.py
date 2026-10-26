"""Helpers shared by the Streamlit pages: the API client, job waiting, formatting.

Functional layer only; the visual design is a separate pass. Pages keep their
selections in ``st.session_state`` under the keys in ``Keys`` so a choice made
on one page (the run, the segment) carries over to the next.
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
import streamlit as st

from processpilot.app.client import ApiClient, ApiError, ui_settings

_FINISHED = {"succeeded", "failed", "interrupted"}
_SECONDS_PER_UNIT = (("d", 86400), ("h", 3600), ("min", 60))


class Keys:
    """Session-state keys shared across pages."""

    dataset = "pp_dataset_id"
    run = "pp_run_id"
    segment = "pp_segment_slug"
    agent_run = "pp_agent_run_id"
    analyst = "pp_analyst"
    pages = "pp_pages"


def page(name: str) -> Any:
    """The ``st.Page`` registered under ``name`` by the entry script (for links)."""
    return st.session_state[Keys.pages][name]


@st.cache_resource
def settings() -> dict[str, Any]:
    """``configs/app.yaml`` ui block (read once per server process)."""
    return ui_settings()


@st.cache_resource
def client() -> ApiClient:
    """One API client per Streamlit server process."""
    s = settings()
    return ApiClient(s["api_url"], s["request_timeout_seconds"])


def api_error(exc: ApiError) -> None:
    """Show an API error the way an analyst can act on it."""
    if exc.status is None:
        st.error(
            f"{exc.detail}\n\nStart the backend with `.\\.venv\\python.exe -m processpilot.api` "
            "(or use `scripts\\run_app.ps1`, which starts both)."
        )
    else:
        st.error(f"The API refused this ({exc.status}): {exc.detail}")


def wait_for_job(job_id: str, label: str) -> dict[str, Any] | None:
    """Block until a background job ends, showing its progress; ``None`` on failure."""
    poll = float(settings()["poll_seconds"])
    with st.status(label, expanded=True) as box:
        last = None
        while True:
            job = client().job(job_id)
            if job["message"] and job["message"] != last:
                st.write(job["message"])
                last = job["message"]
            if job["status"] in _FINISHED:
                break
            time.sleep(poll)
        if job["status"] == "succeeded":
            box.update(label=f"{label}: done", state="complete", expanded=False)
            return job
        box.update(label=f"{label}: {job['status']}", state="error", expanded=True)
        st.error(job["error"] or job["status"])
        return None


def fmt_duration(seconds: float | None) -> str:
    """``3.5 d``, ``4.0 h``, ``12 min``, ``40 s``; ``n/a`` for missing values."""
    if seconds is None or pd.isna(seconds):
        return "n/a"
    for unit, size in _SECONDS_PER_UNIT:
        if abs(seconds) >= size:
            value = seconds / size
            return f"{value:.0f} {unit}" if unit == "min" else f"{value:.1f} {unit}"
    return f"{seconds:.0f} s"


def fmt_share(share: float | None) -> str:
    """``0.123`` -> ``12.3%``."""
    return "n/a" if share is None else f"{share * 100:.1f}%"


def sidebar_status() -> dict[str, Any] | None:
    """API, model and Graphviz status in the sidebar; ``None`` if the API is down."""
    with st.sidebar:
        try:
            health = client().health()
        except ApiError as exc:
            st.error("API not reachable")
            st.caption(exc.detail)
            return None
        model = health["model_id"] or "not set (PP_MODEL)"
        st.caption(f"API: {client().base_url}")
        st.caption(f"Model: `{model}`" + (" (rule-based mock)" if model == "mock" else ""))
        if not health["graphviz"]:
            st.warning("Graphviz `dot` not on PATH: BPMN diagrams have no layout.")
        if health["active_jobs"]:
            st.info(f"{health['active_jobs']} background job(s) running")
        return health


def pick_run(label: str = "Mining run") -> dict[str, Any] | None:
    """Select a mined run (remembered across pages); returns its detail."""
    runs = [r for r in client().runs() if r["status"] == "mined"]
    if not runs:
        st.info("No mined run yet. Start on the **Data** page.")
        return None
    ids = [r["run_id"] for r in runs]
    by_id = {r["run_id"]: r for r in runs}
    current = st.session_state.get(Keys.run)
    run_id = st.selectbox(
        label,
        ids,
        index=ids.index(current) if current in ids else 0,
        format_func=lambda i: (
            f"{i} · {by_id[i]['preset'] or 'preset not set'} · "
            f"{by_id[i]['evidence_count']} evidence items · {by_id[i]['origin']}"
        ),
    )
    st.session_state[Keys.run] = run_id
    return client().run(run_id)


def pick_segment(run: dict[str, Any]) -> dict[str, Any] | None:
    """Select a segment of the run (remembered across pages)."""
    segments = run["segments"]
    if not segments:
        st.warning("This run has no segments.")
        return None
    slugs = [s["slug"] for s in segments]
    by_slug = {s["slug"]: s for s in segments}
    current = st.session_state.get(Keys.segment)
    slug = st.selectbox(
        "Segment",
        slugs,
        index=slugs.index(current) if current in slugs else 0,
        format_func=lambda s: by_slug[s]["name"],
    )
    st.session_state[Keys.segment] = slug
    return by_slug[slug]
