"""The web app is served by the API process: shell, assets, client routes, settings.

WHY these tests: the React app (``web/``) is built into ``api/static`` and the
API serves it from the same origin, so one process runs everything offline.
What must hold is the routing contract: every client route answers with the
app shell, assets are served as files, the API's own routes keep answering
JSON, and an unbuilt checkout says how to build instead of failing silently.
The app's own logic is tested in ``web/src/*.test.ts`` (vitest) and was walked
through in a real browser (Playwright).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from processpilot.api import main
from processpilot.api.service import AppSettings, Workspace

SETTINGS = AppSettings(host="127.0.0.1", port=0, job_workers=1, max_upload_mb=1)
SHELL = "<!doctype html><title>ProcessPilot</title><div id=root></div>"


def _client(tmp_path: Path, static_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(main, "STATIC_DIR", static_dir)
    workspace = Workspace(tmp_path / "runs", tmp_path / "app", SETTINGS, inline_jobs=True)
    return TestClient(main.create_app(workspace))


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(SHELL, encoding="utf-8")
    (static / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (static / "favicon.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8"
    )
    with _client(tmp_path, static, monkeypatch) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/data",
        "/data?dataset=ds-1",
        "/evidence",
        "/evidence/20261027T113915Z/2-way-match",
        "/redesign/20261027T113915Z/2-way-match?agent=x",
    ],
)
def test_every_client_route_answers_with_the_app_shell(built: TestClient, path: str) -> None:
    response = built.get(path)
    assert response.status_code == 200
    assert response.text == SHELL
    # A rebuild renames the hashed assets, so the shell must never be cached.
    assert response.headers["cache-control"] == "no-cache"


def test_assets_and_favicon_are_served_as_files(built: TestClient) -> None:
    js = built.get("/assets/index-abc123.js")
    assert js.status_code == 200 and js.text == "console.log(1)"
    assert "javascript" in js.headers["content-type"]
    assert built.get("/favicon.svg").headers["content-type"].startswith("image/svg+xml")
    assert built.get("/assets/missing.js").status_code == 404


def test_api_routes_still_answer_json_next_to_the_app(built: TestClient) -> None:
    assert built.get("/runs").json() == []
    assert built.get("/datasets").json() == []
    assert built.get("/runs/nope").status_code == 404
    # Paths the app does not own are not swallowed by the shell.
    assert built.get("/not-a-page").status_code == 404


def test_ui_settings_come_from_app_yaml(built: TestClient) -> None:
    settings = built.get("/settings/ui").json()
    assert settings["poll_seconds"] > 0
    assert settings["bpmn_height_px"] > 0


def test_an_unbuilt_checkout_says_how_to_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, tmp_path / "no-build", monkeypatch) as client:
        response = client.get("/evidence/x/y")
        assert response.status_code == 503
        assert "npm run build" in response.text
        assert client.get("/health").status_code == 200
