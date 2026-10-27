"""The FastAPI backend end to end, through HTTP, with jobs run inline.

WHY through HTTP and not the service directly: the contract the Streamlit app
relies on is the routes, status codes and JSON shapes. Jobs run inline
(``inline_jobs=True``) so every request's effects are visible when it returns.

Two flows: the committed BPI sample through preset -> validate -> mine ->
agent (rule-based mock) -> reject with comment -> revised proposals ->
approve; and a hand-built CSV through upload -> profile -> analyst mapping ->
validate -> mine. The agent-only tests use the hand-built run from
``agent_fixtures`` (fast, known evidence).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient

from agent_fixtures import SEGMENT, SLUG, write_run_dir
from processpilot.api.db import Database
from processpilot.api.main import create_app
from processpilot.api.service import AppSettings, Workspace

SETTINGS = AppSettings(host="127.0.0.1", port=0, job_workers=1, max_upload_mb=1)


def _workspace(root: Path) -> Workspace:
    return Workspace(root / "runs", root / "app", SETTINGS, inline_jobs=True)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_workspace(tmp_path))) as c:
        yield c


def _job(client: TestClient, response) -> dict:
    """The finished job behind a 202 response (jobs run inline in tests)."""
    assert response.status_code == 202, response.text
    job = client.get(f"/jobs/{response.json()['job_id']}").json()
    assert job["status"] == "succeeded", job
    return job


def _fixture_run(tmp_path: Path) -> str:
    """The hand-built agent run folder, with the manifest mining would write."""
    run_dir = write_run_dir(tmp_path / "runs")
    (run_dir / "manifest.json").write_text(
        json.dumps({"segments": {SEGMENT: SLUG}, "evidence_count": 7, "created_at": "x"}),
        encoding="utf-8",
    )
    return run_dir.name


# --- full flow on the committed BPI sample ----------------------------------------


def test_bpi_sample_from_preset_to_approved_redesign(client: TestClient) -> None:
    presets = {p["name"]: p for p in client.get("/presets").json()}
    assert presets["bpi2019_sample"]["source_available"]
    assert not presets["generic"]["source_available"]  # a base for uploads only
    assert "agent" not in presets and "app" not in presets

    ds = client.post("/datasets/preset", json={"preset": "bpi2019_sample"}).json()
    assert ds["kind"] == "preset" and ds["mapping"]["segment_by"] == "case:Item Category"

    _job(client, client.post(f"/datasets/{ds['id']}/validate"))
    quality = client.get(f"/datasets/{ds['id']}").json()["quality"]["report"]
    assert quality["case_count"] == 800
    suggested = client.get(f"/datasets/{ds['id']}").json()["suggested_cleaning"]
    assert suggested["drop_cases_with_out_of_range_timestamps"] is True
    advice = {
        a["option"]: a
        for a in client.get(f"/datasets/{ds['id']}").json()["quality"]["recommendation"]["advice"]
    }
    # The sample was cut from cleaned cases: no junk dates, so no removal is recommended.
    assert not advice["drop_cases_with_out_of_range_timestamps"]["recommended"]
    assert advice["drop_exact_duplicate_events"]["rows_removed"] > 0  # batch postings
    assert not advice["drop_exact_duplicate_events"]["recommended"]

    mined = _job(client, client.post(f"/datasets/{ds['id']}/runs", json=suggested))
    run_id = mined["result"]["run_id"]
    run = client.get(f"/runs/{run_id}").json()
    assert run["status"] == "mined" and run["origin"] == "app"
    assert {s["slug"] for s in run["segments"]} == {
        "2-way-match",
        "3-way-match-invoice-after-gr",
        "3-way-match-invoice-before-gr",
        "consignment",
    }
    assert run["data_quality"]["cleaning_log"] == []  # the sample has no junk dates

    slug = "2-way-match"
    bottlenecks = client.get(f"/runs/{run_id}/segments/{slug}/evidence?type=bottleneck").json()
    assert bottlenecks and bottlenecks[0]["id"] == f"BN-01-{slug}"
    assert "task_" in client.get(f"/runs/{run_id}/segments/{slug}/as-is.bpmn").text
    variant = client.get(f"/runs/{run_id}/segments/{slug}/evidence?type=variant").json()[1]
    ladder = client.get(f"/runs/{run_id}/segments/{slug}/variants/2/waits").json()
    assert ladder["evidence_id"] == variant["id"]
    assert ladder["activities"] == variant["metrics"]["activities"]
    assert ladder["case_count"] == variant["metrics"]["case_count"]
    assert ladder["median_throughput_seconds"] == variant["metrics"]["median_throughput_seconds"]
    assert client.get(f"/runs/{run_id}/segments/{slug}/variants/999/waits").status_code == 404
    model = client.get(f"/runs/{run_id}/segments/{slug}/model").json()
    assert model["default_constraints"]["protected_activities"] == ["Clear Invoice"]

    started = _job(
        client, client.post(f"/runs/{run_id}/segments/{slug}/agent-runs", json={"model_id": "mock"})
    )
    arid = started["result"]["agent_run_id"]
    base = f"/runs/{run_id}/segments/{slug}/agent-runs/{arid}"
    detail = client.get(base).json()
    assert detail["awaiting_review"] and detail["review_round"] == 1
    assert detail["model_id"] == "mock" and 2 <= len(detail["proposals"]) <= 3
    first_titles = [p["proposal"]["title"] for p in detail["proposals"]]
    assert all(p["validation"]["status"] == "validated" for p in detail["proposals"])
    assert all(p["validation"]["estimate"]["label"] == "ESTIMATE" for p in detail["proposals"])
    assert "task_" in client.get(f"{base}/proposals/0/to-be.bpmn").text

    _job(
        client,
        client.post(
            f"{base}/decision",
            json={"decision": "reject", "comment": "Prefer other steps.", "analyst": "A. Analyst"},
        ),
    )
    detail = client.get(base).json()
    assert detail["awaiting_review"] and detail["review_round"] == 2
    assert [p["proposal"]["title"] for p in detail["proposals"]] != first_titles

    _job(client, client.post(f"{base}/decision", json={"decision": "approve"}))
    detail = client.get(base).json()
    assert detail["status"] == "approved" and not detail["awaiting_review"]
    assert [(d["round"], d["decision"], d["analyst"]) for d in detail["decisions"]] == [
        (1, "reject", "A. Analyst"),
        (2, "approve", None),
    ]
    assert detail["decisions"][0]["proposal_titles"] == first_titles
    assert client.post(f"{base}/decision", json={"decision": "approve"}).status_code == 409
    assert len(client.get(f"/decisions?run_id={run_id}").json()) == 2


# --- upload with analyst mapping --------------------------------------------------


def test_upload_profile_map_validate_and_mine(client: TestClient) -> None:
    rows = ["Case,Step,When,Who"]
    for case in range(1, 7):
        day = f"2019-01-{case:02d}"
        rows += [
            f"k{case},Order,{day} 08:00:00,ann",
            f"k{case},Check,{day} 10:00:00,bob",
            f"k{case},Ship,{day} 16:00:00,ann",
        ]
    rows.append("k6,Ship,2019-01-06 16:00:00,ann")  # an exact duplicate event
    upload = client.post(
        "/datasets/upload",
        files={"file": ("my log.csv", "\n".join(rows).encode(), "text/csv")},
        data={"base_preset": "generic"},
    )
    assert upload.status_code == 201, upload.text
    ds = upload.json()
    assert ds["kind"] == "upload" and ds["mapping"] is None and ds["name"] == "my_log.csv"

    # Validation needs a confirmed mapping first.
    assert client.post(f"/datasets/{ds['id']}/validate").status_code == 409

    _job(client, client.post(f"/datasets/{ds['id']}/profile"))
    profile = client.get(f"/datasets/{ds['id']}").json()["profile"]
    assert profile["row_count"] == 19
    assert profile["suggestions"]["timestamp"][0]["column"] == "When"

    bad = {"case_id": "Case", "activity": "Step", "timestamp": "Nope"}
    assert client.put(f"/datasets/{ds['id']}/mapping", json=bad).status_code == 422
    same = {"case_id": "Case", "activity": "Case", "timestamp": "When"}
    assert client.put(f"/datasets/{ds['id']}/mapping", json=same).status_code == 422
    mapping = {"case_id": "Case", "activity": "Step", "timestamp": "When", "resource": "Who"}
    assert client.put(f"/datasets/{ds['id']}/mapping", json=mapping).status_code == 200

    _job(client, client.post(f"/datasets/{ds['id']}/validate"))
    issues = client.get(f"/datasets/{ds['id']}").json()["quality"]["report"]["issues"]
    assert "duplicate_event" in {i["code"] for i in issues}

    policy = {"drop_exact_duplicate_events": True}
    mined = _job(client, client.post(f"/datasets/{ds['id']}/runs", json=policy))
    run = client.get(f"/runs/{mined['result']['run_id']}").json()
    assert [s["slug"] for s in run["segments"]] == ["all"]  # no segment_by: whole log
    assert run["data_quality"]["cleaning_log"][0]["code"] == "drop_exact_duplicate_events"
    assert run["preset"] == "generic"


def test_upload_rejects_unsupported_and_oversized_files(client: TestClient) -> None:
    bad_type = client.post("/datasets/upload", files={"file": ("log.txt", b"x", "text/plain")})
    assert bad_type.status_code == 422 and "unsupported" in bad_type.json()["detail"]
    big = client.post(
        "/datasets/upload", files={"file": ("big.csv", b"x" * (2 * 1024 * 1024), "text/csv")}
    )
    assert big.status_code == 422 and "exceeds" in big.json()["detail"]
    assert client.get("/datasets").json() == []


# --- runs found on disk, agent rules ------------------------------------------------


def test_run_found_on_disk_needs_its_preset_before_the_agent(tmp_path: Path) -> None:
    run_id = _fixture_run(tmp_path)
    with TestClient(create_app(_workspace(tmp_path))) as client:
        listed = client.get("/runs").json()
        assert [(r["run_id"], r["origin"], r["preset"]) for r in listed] == [(run_id, "disk", None)]
        start = f"/runs/{run_id}/segments/{SLUG}/agent-runs"
        assert client.post(start, json={"model_id": "mock"}).status_code == 409
        assert client.put(f"/runs/{run_id}/preset", json={"preset": "nope"}).status_code == 422
        assert client.put(f"/runs/{run_id}/preset", json={"preset": "generic"}).status_code == 200

        job = _job(client, client.post(start, json={"model_id": "mock"}))
        detail = client.get(f"{start}/{job['result']['agent_run_id']}").json()
        assert detail["awaiting_review"]
        assert {p["proposal"]["heuristic_id"] for p in detail["proposals"]} == {
            "H-AUTOMATION",
            "H-TASK-ELIMINATION",
        }


def test_unknown_ids_are_404_and_never_reach_the_filesystem(tmp_path: Path) -> None:
    run_id = _fixture_run(tmp_path)
    with TestClient(create_app(_workspace(tmp_path))) as client:
        assert client.get("/runs/..%2F..%2Fapp").status_code == 404
        assert client.get(f"/runs/{run_id}/segments/..%2Fx/evidence").status_code == 404
        assert client.get(f"/runs/{run_id}/segments/nope/evidence").status_code == 404
        assert client.get(f"/runs/{run_id}/segments/..%2Fx/variants/1/waits").status_code == 404
        assert client.get(f"/runs/{run_id}/segments/{SLUG}/agent-runs/x").status_code == 404
        assert client.get("/datasets/x").status_code == 404
        assert client.get("/jobs/x").status_code == 404


def test_agent_failure_is_recorded_on_the_job_and_the_agent_run(tmp_path: Path) -> None:
    run_id = _fixture_run(tmp_path)
    (tmp_path / "runs" / run_id / SLUG / "as_is_tree.json").unlink()  # mock cannot redesign
    with TestClient(create_app(_workspace(tmp_path))) as client:
        client.put(f"/runs/{run_id}/preset", json={"preset": "generic"})
        response = client.post(
            f"/runs/{run_id}/segments/{SLUG}/agent-runs", json={"model_id": "mock"}
        )
        job = client.get(f"/jobs/{response.json()['job_id']}").json()
        # The graph handles a failed LLM step itself: the job succeeds, the run is failed.
        assert job["status"] == "succeeded" and job["result"]["status"] == "failed"
        runs = client.get(f"/runs/{run_id}/segments/{SLUG}/agent-runs").json()
        assert runs[0]["status"] == "failed"


# --- the index survives restarts ----------------------------------------------------


def test_restart_interrupts_unfinished_jobs_and_recovers_runs(tmp_path: Path) -> None:
    db = Database(tmp_path / "app" / "processpilot.sqlite")
    db.add_job("job-stuck", "mine", {"run_id": "20260101T000000Z"})
    db.update_job("job-stuck", status="running")
    db.add_run("20990101T000000Z", "app", "mining")  # died before writing a manifest

    ws = _workspace(tmp_path)
    assert ws.db.job("job-stuck")["status"] == "interrupted"
    assert ws.db.run("20990101T000000Z")["status"] == "failed"


def test_failed_job_keeps_its_error(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)

    def boom(progress: Callable[[str], None]) -> NoReturn:
        progress("about to fail")
        raise ValueError("no such column")

    job = ws.db.job(ws.jobs.submit("test", {}, boom))
    assert job["status"] == "failed" and job["error"] == "ValueError: no such column"
    assert job["message"] == "about to fail"
