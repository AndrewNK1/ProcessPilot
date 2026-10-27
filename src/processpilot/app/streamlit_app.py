r"""Streamlit front end for business analysts. Talks only to the FastAPI backend.

WHY a UI is part of the thesis and not decoration: the claim is that an SME
business analyst -- not a data scientist -- can run this. The mapping
confirmation screen, the evidence table, and the approve/reject control are
where that claim is either true or false.

Pages: Data (source -> mapping -> data quality -> cleaning -> mine), Evidence
(per segment: where cases wait, ranked evidence, the as-is BPMN), Redesign
(agent run, cited evidence, proposals with ESTIMATE, as-is vs to-be BPMN,
approve/reject with a comment, which resumes the paused LangGraph run
through the API).

The look: ``.streamlit/config.toml`` (light and dark theme, vendored IBM
Plex), ``widgets`` (the wait ladder and the other ProcessPilot pieces) and a
light/dark switch in the sidebar.

Run (PowerShell): ``scripts\run_app.ps1`` starts the API and this app, or
``.\.venv\python.exe -m streamlit run src\processpilot\app\streamlit_app.py``
with the API already running.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from processpilot.app import page_data, page_evidence, page_redesign
from processpilot.app.ui import Keys, api_health, page_header, restore_selection, sidebar, style

st.set_page_config(
    page_title="ProcessPilot",
    page_icon=str(Path(__file__).parent / "static" / "mark.svg"),
    layout="wide",
)

pages = {
    "data": st.Page(page_data.render, title="Data", url_path="data", default=True),
    "evidence": st.Page(page_evidence.render, title="Evidence", url_path="evidence"),
    "redesign": st.Page(page_redesign.render, title="Redesign", url_path="redesign"),
}
st.session_state[Keys.pages] = pages
navigation = st.navigation(list(pages.values()), position="hidden")
restore_selection()
style()
health = api_health()
finish_sidebar = sidebar(health)
if health is None:
    page_header(
        "ProcessPilot", "The analysis backend is not reachable, so there is nothing to show."
    )
    st.error(
        "Start everything with `scripts\\run_app.ps1`, or the API alone with "
        "`.\\.venv\\python.exe -m processpilot.api`, then reload this page.",
        icon=":material/cloud_off:",
    )
else:
    navigation.run()
finish_sidebar()
