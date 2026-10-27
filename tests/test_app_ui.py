"""The Streamlit pages render headless against a real (in-process) API.

WHY: the redesign moved every page onto new layouts and components; this
walks each page on the mined BPI sample with a mock agent run waiting for a
decision, so a broken page, a renamed key or a missing API field fails here
and not in front of an analyst. The look itself is checked in a browser
(light, dark, phone width); AppTest cannot render CSS or components.

Pages are run through ``AppTest.from_function`` (AppTest switches only
between file-based pages, and ours are functions under ``st.navigation``).
The app's HTTP client is pointed at a FastAPI ``TestClient``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from processpilot.api.main import create_app
from processpilot.api.service import AppSettings, Workspace
from processpilot.app import client as client_module
from processpilot.app import ui

SETTINGS = AppSettings(host="127.0.0.1", port=0, job_workers=1, max_upload_mb=1)
SLUG = "2-way-match"


def _page(name: str) -> None:
    """One page as the entry script would show it (shell + page)."""
    import streamlit as st

    from processpilot.app import page_data, page_evidence, page_redesign
    from processpilot.app.ui import Keys, api_health, sidebar, style

    renders = {
        "data": page_data.render,
        "evidence": page_evidence.render,
        "redesign": page_redesign.render,
    }
    st.session_state[Keys.pages] = {
        key: st.Page(fn, title=key.title(), url_path=key, default=key == "data")
        for key, fn in renders.items()
    }
    style()
    finish = sidebar(api_health())
    renders[name]()
    finish()


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    root = tmp_path_factory.mktemp("ui")
    workspace = Workspace(root / "runs", root / "app", SETTINGS, inline_jobs=True)
    with TestClient(create_app(workspace)) as http:
        ds = http.post("/datasets/preset", json={"preset": "bpi2019_sample"}).json()
        http.post(f"/datasets/{ds['id']}/validate")
        policy = http.get(f"/datasets/{ds['id']}").json()["suggested_cleaning"]
        run_id = http.post(f"/datasets/{ds['id']}/runs", json=policy).json()["run_id"]
        job = http.post(
            f"/runs/{run_id}/segments/{SLUG}/agent-runs", json={"model_id": "mock"}
        ).json()
        agent_run_id = http.get(f"/jobs/{job['job_id']}").json()["result"]["agent_run_id"]
        yield {"http": http, "dataset": ds["id"], "run": run_id, "agent": agent_run_id}


@pytest.fixture
def app(api: dict, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def init(self: client_module.ApiClient, base_url: str, timeout: float) -> None:
        self.base_url = "http://testserver"
        self._http = api["http"]

    monkeypatch.setattr(client_module.ApiClient, "__init__", init)
    ui.client.clear()
    yield
    ui.client.clear()


def _run(name: str, state: dict[str, object]) -> AppTest:
    at = AppTest.from_function(_page, args=(name,), default_timeout=60)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def test_data_page_shows_every_finished_step(api: dict, app: None) -> None:
    at = _run("data", {ui.Keys.dataset: api["dataset"]})
    assert not at.exception
    assert at.title[0].value == "Data"
    assert [s.value for s in at.subheader] == [
        "Event log",
        "Column mapping",
        "Data quality",
        "Cleaning and mining",
    ]
    assert any(b.label == "Mine this log" for b in at.get("form_submit_button"))


def test_data_page_first_run_offers_the_sources(app: None) -> None:
    at = _run("data", {})
    assert not at.exception
    assert at.segmented_control[0].options == [
        "Configured preset",
        "Upload a file",
        "Earlier dataset",
    ]


def test_evidence_page_leads_with_the_worst_wait_and_the_ladder(api: dict, app: None) -> None:
    at = _run("evidence", {ui.Keys.run: api["run"], ui.Keys.segment: SLUG})
    assert not at.exception
    assert at.title[0].value == "Evidence · 2-way match"
    assert at.caption[0].value.startswith("Worst wait:")
    assert "Where the days go" in [s.value for s in at.subheader]
    path = at.selectbox[1]
    assert path.label == "Path" and path.value == 2  # most frequent path with >1 wait


def test_redesign_page_waits_for_an_explicit_decision(api: dict, app: None) -> None:
    state = {ui.Keys.run: api["run"], ui.Keys.segment: SLUG, ui.Keys.agent_run: api["agent"]}
    at = _run("redesign", state)
    assert not at.exception
    assert at.title[0].value == "Redesign · 2-way match"
    headings = [s.value for s in at.subheader]
    assert "Diagnosis" in headings and "Your decision · round 1" in headings
    decision = next(s for s in at.segmented_control if s.label == "Decision")
    assert decision.value is None  # never pre-filled

    at.get("form_submit_button")[-1].click().run()  # submit without choosing
    assert not at.exception
    assert any("Choose Approve or Reject" in e.value for e in at.error)
    runs = api["http"].get(f"/runs/{api['run']}/segments/{SLUG}/agent-runs").json()
    assert runs[0]["status"] == "awaiting_review"  # nothing was sent
