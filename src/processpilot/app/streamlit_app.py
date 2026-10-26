r"""Streamlit front end for business analysts. Talks only to the FastAPI backend.

WHY a UI is part of the thesis and not decoration: the claim is that an SME
business analyst -- not a data scientist -- can run this. The mapping
confirmation screen, the evidence table, and the approve/reject control are
where that claim is either true or false.

Pages: Data (source -> mapping -> data quality -> cleaning -> mine), Evidence
(per segment, with the as-is BPMN), Redesign (agent run, cited evidence,
proposals with ESTIMATE, as-is vs to-be BPMN, approve/reject with a comment,
which resumes the paused LangGraph run through the API).

Functional layout only; the visual design is a separate pass.

Run (PowerShell): ``scripts\run_app.ps1`` starts the API and this app, or
``.\.venv\python.exe -m streamlit run src\processpilot\app\streamlit_app.py``
with the API already running.
"""

from __future__ import annotations

import streamlit as st

from processpilot.app import page_data, page_evidence, page_redesign
from processpilot.app.ui import Keys, sidebar_status

st.set_page_config(page_title="ProcessPilot", layout="wide")

pages = {
    "data": st.Page(page_data.render, title="Data", url_path="data", default=True),
    "evidence": st.Page(page_evidence.render, title="Evidence", url_path="evidence"),
    "redesign": st.Page(page_redesign.render, title="Redesign", url_path="redesign"),
}
st.session_state[Keys.pages] = pages
navigation = st.navigation(list(pages.values()))
st.sidebar.markdown("**ProcessPilot**")
st.sidebar.caption("Evidence-grounded process redesign. A human decides.")
if sidebar_status() is None:
    st.title("ProcessPilot")
    st.error(
        "The API is not reachable. Start everything with `scripts\\run_app.ps1`, or the API "
        "alone with `.\\.venv\\python.exe -m processpilot.api`."
    )
else:
    navigation.run()
