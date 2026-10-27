"""Helpers shared by the Streamlit pages: the API client, the shell, job waiting, formatting.

Pages keep their selections in ``st.session_state`` under the keys in ``Keys``
so a choice made on one page (the run, the segment) carries over to the next.
The look lives in ``.streamlit/config.toml`` (theme) and ``widgets`` (the
product's own components); this module only arranges Streamlit's own
elements.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st

from processpilot.app import widgets
from processpilot.app.client import ApiClient, ApiError, ui_settings

_FINISHED = {"succeeded", "failed", "interrupted"}
_SECONDS_PER_UNIT = (("d", 86400), ("h", 3600), ("min", 60))
# Page paths the theme switch stores the appearance under (see widgets.theme_switch).
_PAGE_PATHS = ("/", "/data", "/evidence", "/redesign")

# What Streamlit's theme does not reach: text selection, focus rings on our
# keyed containers, and the gap above section headings. Keyed classes only
# (``.st-key-*``), never Streamlit's private class names.
_CSS = """
<style>
::selection { background: rgba(26, 127, 136, 0.28); }
[class*="st-key-pp-section"] { margin-top: 1.5rem; }
.st-key-pp-decision { margin-top: 1.5rem; }
[data-testid="stSidebar"] [data-testid="stPageLink"] a { padding-top: 6px; padding-bottom: 6px; }
/* Selected segments and tabs keep their teal tint, border or underline, but the
   label is ink: dark mode's primary must stay deep enough for white button
   labels, and deep teal text on a dark ground would be under 4.5:1. */
[data-testid="stButtonGroup"] button[aria-checked="true"],
[data-testid="stButtonGroup"] button[aria-pressed="true"],
[data-testid="stTabs"] [role="tab"][aria-selected="true"] { color: inherit !important; }
[data-testid="stButtonGroup"] button[aria-checked="true"] p,
[data-testid="stButtonGroup"] button[aria-pressed="true"] p,
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p { font-weight: 500; }
/* Streamlit's narrow-screen gutter is 1rem (15px here); the page margin is 20px. */
@media (max-width: 900px) {
  [data-testid="stMainBlockContainer"] { padding-left: 20px; padding-right: 20px; }
}
</style>
"""


class Keys:
    """Session-state keys shared across pages."""

    dataset = "pp_dataset_id"
    run = "pp_run_id"
    segment = "pp_segment_slug"
    segment_name = "pp_segment_name"
    agent_run = "pp_agent_run_id"
    variant = "pp_variant_rank"
    analyst = "pp_analyst"
    pages = "pp_pages"


# Selections that survive the reload a theme switch needs: query name -> key.
_RESTORABLE = {
    "dataset": Keys.dataset,
    "run": Keys.run,
    "segment": Keys.segment,
    "agent": Keys.agent_run,
    "path": Keys.variant,
}


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


def restore_selection() -> None:
    """Take selections back from the query string after a theme switch reload.

    Values are only ids; every page still checks them against the API before
    using them, so a stale or edited URL falls back to the default choice.
    """
    params = st.query_params
    found = {name: params[name] for name in _RESTORABLE if name in params}
    for name, value in found.items():
        key = _RESTORABLE[name]
        st.session_state.setdefault(key, int(value) if key == Keys.variant else value)
    if found:
        for name in found:
            del st.query_params[name]


def style() -> None:
    """Inject the few rules the theme cannot express."""
    st.html(_CSS)


def api_error(exc: ApiError) -> None:
    """Show an API error the way an analyst can act on it."""
    if exc.status is None:
        st.error(
            f"{exc.detail}\n\nStart the backend with `.\\.venv\\python.exe -m processpilot.api` "
            "(or use `scripts\\run_app.ps1`, which starts both).",
            icon=":material/cloud_off:",
        )
    else:
        st.error(f"The API refused this ({exc.status}): {exc.detail}", icon=":material/error:")


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


def duration_parts(seconds: float | None) -> tuple[str, str]:
    """``(value, unit)`` for a duration, e.g. ``("3.5", "d")``; ``("—", "")`` when missing."""
    if seconds is None or pd.isna(seconds):
        return "—", ""
    for unit, size in _SECONDS_PER_UNIT:
        if abs(seconds) >= size:
            value = seconds / size
            return (f"{value:.0f}", unit) if unit == "min" else (f"{value:.1f}", unit)
    return f"{seconds:.0f}", "s"


def fmt_duration(seconds: float | None) -> str:
    """``3.5 d``, ``4.0 h``, ``12 min``, ``40 s``; ``—`` for missing values."""
    value, unit = duration_parts(seconds)
    return f"{value} {unit}".strip()


def fmt_share(share: float | None) -> str:
    """``0.123`` -> ``12.3%``; ``—`` when missing."""
    return "—" if share is None else f"{share * 100:.1f}%"


def page_header(title: str, subtitle: str | None = None) -> None:
    """The page's one h1 and its optional finding line."""
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def sidebar(health: dict[str, Any] | None) -> Callable[[], None]:
    """Mark, navigation, the current scope, appearance and system status.

    Returns ``finish``, to call after the page has run: the scope line and the
    theme switch (whose reload carries the selections) must show the choices
    the page just made, not the ones from the previous rerun.
    """
    pages = st.session_state[Keys.pages]
    with st.sidebar:
        widgets.brand()
        st.page_link(pages["data"], label="Data", icon=":material/database:")
        st.page_link(pages["evidence"], label="Evidence", icon=":material/query_stats:")
        st.page_link(pages["redesign"], label="Redesign", icon=":material/alt_route:")
        scope_slot = st.empty()
        st.space("medium")
        switch_slot = st.container()
        _status(health)

    def finish() -> None:
        run_id = st.session_state.get(Keys.run)
        if run_id:
            scope = f"Run `{run_id}`"
            if st.session_state.get(Keys.segment_name):
                scope += "  \n" + st.session_state[Keys.segment_name]
            scope_slot.caption(scope)
        with switch_slot:
            widgets.theme_switch(_PAGE_PATHS, _restore_query())

    return finish


def _status(health: dict[str, Any] | None) -> None:
    """Model, background jobs and Graphviz, in the sidebar's foot."""
    if health is None:
        st.caption(":material/cloud_off: API not reachable")
        return
    model = health["model_id"] or "not set (PP_MODEL)"
    lines = [f"Model `{model}`" + (" · rule-based mock" if model == "mock" else "")]
    if health["active_jobs"]:
        lines.append(f"{health['active_jobs']} background job(s) running")
    st.caption("  \n".join(lines))
    if not health["graphviz"]:
        st.caption(":material/warning: Graphviz `dot` not on PATH: diagrams have no layout.")


def _restore_query() -> dict[str, str]:
    return {
        name: str(st.session_state[key])
        for name, key in _RESTORABLE.items()
        if st.session_state.get(key) is not None
    }


def api_health() -> dict[str, Any] | None:
    """``/health``, or ``None`` when the API cannot be reached."""
    try:
        return client().health()
    except ApiError:
        return None


def pick_run(label: str = "Mining run") -> dict[str, Any] | None:
    """Select a mined run (remembered across pages); returns its detail."""
    runs = [r for r in client().runs() if r["status"] == "mined"]
    if not runs:
        st.info("No mined run yet. Start on the **Data** page.", icon=":material/database:")
        st.page_link(page("data"), label="Go to Data", icon=":material/arrow_forward:")
        return None
    ids = [r["run_id"] for r in runs]
    by_id = {r["run_id"]: r for r in runs}
    current = st.session_state.get(Keys.run)
    run_id = st.selectbox(
        label,
        ids,
        index=ids.index(current) if current in ids else 0,
        format_func=lambda i: f"{i} · {by_id[i]['preset'] or 'preset not named'}",
        help="Each mining run is a fixed snapshot: its evidence and models never change.",
    )
    if run_id != current:
        st.session_state.pop(Keys.variant, None)
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
    default = current if current in slugs else slugs[0]
    if len(slugs) <= 6:
        slug = st.segmented_control(
            "Segment",
            slugs,
            default=default,
            required=True,
            format_func=lambda s: by_slug[s]["name"],
            key=f"pp-segment-{run['run_id']}",
            wrap=True,
        )
    else:
        slug = st.selectbox(
            "Segment", slugs, index=slugs.index(default), format_func=lambda s: by_slug[s]["name"]
        )
    if slug != current:
        st.session_state.pop(Keys.variant, None)
    st.session_state[Keys.segment] = slug
    st.session_state[Keys.segment_name] = by_slug[slug]["name"]
    return by_slug[slug]
