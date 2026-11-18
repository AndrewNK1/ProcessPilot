"""Campaign runner (evaluation Phase 5) on the hand-built agent fixture, no LLM.

The model is scripted per run; the clock is simulated, so waits are recorded
instead of slept.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agent_fixtures import (
    SEGMENT,
    SLUG,
    agent_settings,
    preset,
    valid_diagnosis,
    valid_redesign,
    write_run_dir,
)
from processpilot.agent.llm import ChatTurn, LLMResponse
from processpilot.agent.ratelimit import (
    QuotaLedger,
    RateLimitSettings,
    SimulatedClock,
    next_reset,
)
from processpilot.agent.runlog import RunLogger
from processpilot.api import service
from processpilot.api.db import Database
from processpilot.api.main import create_app
from processpilot.api.service import AppSettings, Workspace
from processpilot.evaluation.runner import CampaignPlan, CampaignRunner, Slot, run_order

T0 = datetime(2026, 11, 18, 1, 30, tzinfo=UTC)  # 2026-11-17 in Los Angeles


def _settings(**over: Any) -> RateLimitSettings:
    base = {
        "providers": ["google_genai"],
        "min_seconds_between_calls": 20,
        "seconds_between_runs": 180,
        "daily_request_budget": 17,
        "worst_case_calls": {"processpilot": 6, "baseline": 3},
        "quota_timezone": "America/Los_Angeles",
        "display_timezone": "Africa/Cairo",
        "default_retry_seconds": 65,
        "max_transport_retries": 5,
        "server_retry_seconds": [60, 120, 240],
        "ledger": "unused",
    }
    return RateLimitSettings.model_validate({**base, **over})


class Model:
    """Scripted outputs for one run; answers as a served model would."""

    model_id = "google_genai:gemini-3.6-flash"

    def __init__(self, outputs: list[Any], served: str = "gemini-3.6-flash") -> None:
        """Outputs are consumed one per call."""
        self.outputs = list(outputs)
        self.served = served

    def invoke(self, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResponse:
        """Raise or answer with the next output."""
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return LLMResponse(parsed=out, raw_text=json.dumps(out), served_model=self.served)


class ServerError(Exception):
    """A 503 shaped like the SDK's error."""

    def __init__(self) -> None:
        """Code 503."""
        super().__init__("503 UNAVAILABLE")
        self.code = 503


def _good() -> list[Any]:
    return [valid_diagnosis(), valid_redesign()]


def _bad_redesign() -> dict[str, Any]:
    bad = copy.deepcopy(valid_redesign())
    bad["proposals"][0]["evidence_ids"] = ["BN-99-seg-a"]  # unknown id, every time
    return bad


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    run_dir = write_run_dir(tmp_path / "runs")
    db = Database(tmp_path / "app" / "pp.sqlite")
    db.add_run(run_dir.name, "disk", "mined", preset="generic", purpose="evaluation")
    db.add_campaign("c1", {}, "x", "draft")
    plan = CampaignPlan(
        campaign_id="c1",
        campaign_dir=tmp_path / "eval" / "c1",
        run_dir=run_dir,
        preset=preset(),
        segments=[SEGMENT],
        repetitions=2,
        conditions=["processpilot", "baseline"],
        model_id=Model.model_id,
        purpose="evaluation",
    )
    return {
        "tmp": tmp_path,
        "db": db,
        "plan": plan,
        "ledger": QuotaLedger(tmp_path / "ledger.jsonl"),
    }


def _runner(
    env: dict[str, Any],
    scripts: list[Any],
    settings: RateLimitSettings | None = None,
    preflight: list[str] | None = None,
    simulate_days: bool = False,
    clock: SimulatedClock | None = None,
) -> CampaignRunner:
    queue = list(scripts)
    return CampaignRunner(
        env["plan"],
        env["db"],
        settings or _settings(),
        env["ledger"],
        clock or SimulatedClock(T0),
        lambda _segment: queue.pop(0),
        lambda: list(preflight or []),
        agent_settings=agent_settings(),
        simulate_days=simulate_days,
        out=lambda _line: None,
    )


def _row(env: dict[str, Any], rep: int) -> dict[str, Any]:
    return env["db"].eval_run("c1", SEGMENT, "processpilot", rep)


def _record(env: dict[str, Any], rep: int) -> dict[str, Any]:
    path = env["plan"].campaign_dir / "processpilot" / SLUG / f"rep{rep}" / "eval_run.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_order_is_interleaved_repetition_first() -> None:
    plan = CampaignPlan(
        "c",
        Path("."),
        Path("."),
        {},
        ["s1", "s2"],
        2,
        ["baseline", "processpilot"],
        "m:x",
        "evaluation",
    )
    assert run_order(plan) == [
        Slot(1, "s1", "processpilot"),
        Slot(1, "s1", "baseline"),
        Slot(1, "s2", "processpilot"),
        Slot(1, "s2", "baseline"),
        Slot(2, "s1", "processpilot"),
        Slot(2, "s1", "baseline"),
        Slot(2, "s2", "processpilot"),
        Slot(2, "s2", "baseline"),
    ]


def test_full_run_reaches_review_in_the_campaign_folder(env: dict[str, Any]) -> None:
    runner = _runner(env, [Model(_good()), Model(_good())])
    report = runner.run()
    assert [a.outcome for _, a in report.executed] == ["reached_review", "reached_review"]
    assert report.stopped_because == "no runnable slot left"  # baseline slots wait for Phase 6
    for rep in (1, 2):
        row = _row(env, rep)
        assert (row["status"], row["outcome"], row["calls_used"]) == ("done", "reached_review", 2)
        assert _record(env, rep)["attempts"][0]["requests_sent"] == 2
    assert env["db"].eval_run("c1", SEGMENT, "baseline", 1)["status"] == "pending"
    # Logs and checkpoints live in the campaign folder; the frozen run is untouched.
    plan = env["plan"]
    assert not (plan.run_dir / SLUG / "agent").exists()
    assert not (plan.run_dir / "checkpoints.sqlite").exists()
    assert (plan.campaign_dir / "checkpoints.sqlite").is_file()
    agents = env["db"].agent_runs(plan.run_dir.name, SEGMENT)
    assert {(a["launched_by"], a["purpose"], a["campaign_id"]) for a in agents} == {
        ("batch", "evaluation", "c1")
    }
    assert all(Path(a["log_dir"]).is_relative_to(plan.campaign_dir) for a in agents)
    assert (plan.campaign_dir / "run_order.json").is_file()
    # 180 s between runs (the second run's first request then needs no gap wait).
    assert 180 in runner.clock.waits
    assert runner._state()["served_model"] == "gemini-3.6-flash"


def test_failed_validation_is_a_result_and_never_rerun(env: dict[str, Any]) -> None:
    bad = [valid_diagnosis(), _bad_redesign(), _bad_redesign(), _bad_redesign()]
    runner = _runner(env, [Model(bad), Model(_good())])
    runner.run(max_runs=1)
    row = _row(env, 1)
    assert (row["status"], row["outcome"], row["calls_used"]) == ("done", "failed_validation", 4)
    assert _record(env, 1)["attempts"][0]["validation_retries"] == {"redesign": 2}
    report = runner.run()
    assert [(s.repetition, a.outcome) for s, a in report.executed] == [(2, "reached_review")]
    assert _row(env, 1)["attempt_count"] == 1


def test_transport_failure_is_retried_as_a_new_attempt(env: dict[str, Any]) -> None:
    failing = Model([valid_diagnosis(), ServerError(), ServerError(), ServerError(), ServerError()])
    runner = _runner(env, [failing, Model(_good()), Model(_good())])
    runner.run(max_runs=1)
    row = _row(env, 1)
    assert (row["status"], row["outcome"]) == ("failed", "transport_error")
    first_attempt = _record(env, 1)["attempts"][0]
    assert first_attempt["transport_retries"] == 0  # the failed call has no llm_call event
    assert first_attempt["requests_sent"] == 5  # 1 diagnose + 4 redesign attempts, all counted
    runner.run()
    record = _record(env, 1)
    assert [a["outcome"] for a in record["attempts"]] == ["transport_error", "reached_review"]
    assert record["attempts"][0]["agent_run_id"] != record["attempts"][1]["agent_run_id"]


def test_budget_too_small_for_the_worst_case_stops_before_starting(env: dict[str, Any]) -> None:
    runner = _runner(env, [Model(_good())], settings=_settings(daily_request_budget=5))
    report = runner.run()
    assert report.executed == []
    assert "5 requests left" in report.stopped_because and "Cairo" in report.stopped_because


def test_quota_stop_mid_run_cuts_it_short_and_resumes_next_day(env: dict[str, Any]) -> None:
    settings = _settings(
        daily_request_budget=3, worst_case_calls={"processpilot": 1, "baseline": 1}
    )
    clock = SimulatedClock(T0)
    runner = _runner(env, [Model(_good()), Model(_good()), Model(_good())], settings, clock=clock)
    runner.run()  # rep 1 uses 2 requests; rep 2 gets 1 request (diagnose), then the stop
    assert _row(env, 1)["outcome"] == "reached_review"
    row = _row(env, 2)
    assert (row["status"], row["outcome"]) == ("quota_stopped", "cut_short_quota")
    attempt = _record(env, 2)["attempts"][0]
    assert attempt["cut_short"] and "next reset 2026-11-18 10:00 Cairo" in attempt["reason"]
    clock.sleep((next_reset(clock.now(), "America/Los_Angeles") - clock.now()).total_seconds())
    runner.run()
    attempts = _record(env, 2)["attempts"]
    assert [a["outcome"] for a in attempts] == ["cut_short_quota", "reached_review"]
    assert _row(env, 2)["attempt_count"] == 2


def test_simulated_days_carry_a_dry_run_through(env: dict[str, Any]) -> None:
    settings = _settings(
        daily_request_budget=2, worst_case_calls={"processpilot": 2, "baseline": 1}
    )
    runner = _runner(env, [Model(_good()), Model(_good())], settings, simulate_days=True)
    report = runner.run()
    assert [a.outcome for _, a in report.executed] == ["reached_review"] * 2
    days = {e["quota_day"] for e in env["ledger"].requests("google_genai")}
    assert days == {"2026-11-17", "2026-11-18"}


def test_served_model_change_stops_until_accepted(env: dict[str, Any]) -> None:
    runner = _runner(
        env,
        [
            Model(_good(), "gemini-3.6-flash"),
            Model(_good(), "gemini-3.7-flash"),
            Model(_good(), "gemini-3.7-flash"),
        ],
    )
    report = runner.run()
    assert "served model changed" in report.stopped_because
    row = _row(env, 2)
    assert (row["status"], row["outcome"]) == ("failed", "served_model_changed")
    with pytest.raises(RuntimeError, match="accept-served-model"):
        runner.run()
    runner.accept_served_model("gemini-3.7-flash", "decided by the analyst")
    runner.run()
    assert _row(env, 2)["outcome"] == "reached_review"
    assert runner._state()["served_model_decisions"][0]["previous"] == "gemini-3.6-flash"


def test_preflight_problems_refuse_to_run(env: dict[str, Any]) -> None:
    with pytest.raises(RuntimeError, match="frozen file changed"):
        _runner(env, [], preflight=["frozen file changed: x"]).run()


def test_interrupted_rows_become_retryable(env: dict[str, Any]) -> None:
    runner = _runner(env, [Model(_good())])
    runner.ensure_grid()
    env["db"].update_eval_run("c1", SEGMENT, "processpilot", 1, status="running")
    runner.ensure_grid()
    assert (_row(env, 1)["status"], _row(env, 1)["outcome"]) == ("failed", "interrupted")


def test_status_reads_without_calling(env: dict[str, Any]) -> None:
    runner = _runner(env, [Model(_good())])
    assert "grid not created yet" in runner.status()
    runner.run(max_runs=1)
    text = _runner(env, []).status()
    assert "Seg A" in text and "total 1/4 done" in text
    assert "not available until Phase 6" in text
    assert "2 sent, 15 of 17 left" in text and "10:00 Cairo" in text


# --- the app and campaign runs --------------------------------------------------------


def test_app_refuses_decisions_on_campaign_runs_but_shows_them(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp = env["tmp"]
    runner = _runner(env, [Model(_good())])
    runner.run(max_runs=1)
    agent_run_id = _row(env, 1)["agent_run_id"]
    run_id = env["plan"].run_dir.name
    (env["plan"].run_dir / "manifest.json").write_text(
        json.dumps({"segments": {SEGMENT: SLUG}}), encoding="utf-8"
    )
    ws = Workspace(
        tmp / "runs",
        tmp / "app",
        AppSettings(host="h", port=0, job_workers=1, max_upload_mb=1),
        inline_jobs=True,
    )
    ws.db = env["db"]
    with TestClient(create_app(ws)) as client:
        base = f"/runs/{run_id}/segments/{SLUG}/agent-runs/{agent_run_id}"
        detail = client.get(base).json()
        assert detail["awaiting_review"] and detail["launched_by"] == "batch"
        assert len(detail["proposals"]) == 2
        assert client.get(f"{base}/proposals/0/to-be.bpmn").status_code == 200
        response = client.post(f"{base}/decision", json={"decision": "approve", "comment": None})
        assert response.status_code == 409 and "campaign runner" in response.json()["detail"]


def test_app_start_is_refused_when_the_budget_cannot_cover_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("PP_QUOTA_LEDGER", str(ledger_path))
    ledger = QuotaLedger(ledger_path)
    now = datetime.now(UTC)
    day = now.astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles")).date()
    for i in range(12):
        ledger.append(
            {
                "kind": "request",
                "provider": "google_genai",
                "quota_day": day.isoformat(),
                "at": (now - timedelta(hours=1, seconds=i)).isoformat(),
            }
        )
    run_dir = write_run_dir(tmp_path / "runs")
    (run_dir / "manifest.json").write_text(
        json.dumps({"segments": {SEGMENT: SLUG}}), encoding="utf-8"
    )
    monkeypatch.setattr(service, "create_llm", lambda model_id, settings: Model(_good()))
    ws = Workspace(
        tmp_path / "runs",
        tmp_path / "app",
        AppSettings(host="h", port=0, job_workers=1, max_upload_mb=1),
        inline_jobs=True,
    )
    with TestClient(create_app(ws)) as client:
        client.put(f"/runs/{run_dir.name}/preset", json={"preset": "generic"})
        response = client.post(
            f"/runs/{run_dir.name}/segments/{SLUG}/agent-runs",
            json={"model_id": "google_genai:gemini-3.6-flash"},
        )
    assert response.status_code == 409
    assert "5 of 17 requests left" in response.json()["detail"]
    assert RunLogger  # imported for parity with other agent tests
