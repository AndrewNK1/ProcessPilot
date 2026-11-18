"""Rate limiter and quota ledger (evaluation Phase 5), with a simulated clock.

No test waits: ``SimulatedClock.sleep`` advances time and records the wait.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from processpilot.agent.llm import ChatTurn, LLMResponse
from processpilot.agent.ratelimit import (
    QuotaLedger,
    QuotaStopped,
    RateLimitedLLM,
    RateLimitSettings,
    SimulatedClock,
    TransportFailed,
    budget,
    call_labels,
    classify,
    format_reset,
    next_reset,
    quota_day,
    with_rate_limit,
)

SETTINGS = RateLimitSettings(
    providers=["google_genai"],
    min_seconds_between_calls=20,
    seconds_between_runs=180,
    daily_request_budget=17,
    worst_case_calls={"processpilot": 6, "baseline": 3},
    quota_timezone="America/Los_Angeles",
    display_timezone="Africa/Cairo",
    default_retry_seconds=65,
    max_transport_retries=5,
    max_server_retries=3,
    ledger="outputs/quota/test.jsonl",
)
# 2026-11-18 01:30 UTC = 2026-11-17 17:30 in Los Angeles (PST, UTC-8).
T0 = datetime(2026, 11, 18, 1, 30, tzinfo=UTC)


class _Schema(BaseModel):
    x: int = 0


class FakeAPIError(Exception):
    """Shaped like google.genai.errors.APIError (code, status, details)."""

    def __init__(self, code: int, details: Any) -> None:
        """Keep code, status and details like the SDK does."""
        super().__init__(f"{code} {details}")
        self.code = code
        self.status = "RESOURCE_EXHAUSTED" if code == 429 else "UNAVAILABLE"
        self.details = details


def _quota_429(quota_id: str, delay: str | None = "37s") -> Exception:
    details: list[dict[str, Any]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id, "quotaValue": "20"}],
        }
    ]
    if delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay})
    cause = FakeAPIError(
        429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}
    )
    # langchain-google-genai raises its own error "from" the SDK's ClientError.
    try:
        raise RuntimeError("Error calling model (RESOURCE_EXHAUSTED)") from cause
    except RuntimeError as exc:
        return exc


class Scripted:
    """Raises or answers in order."""

    model_id = "google_genai:gemini-3.6-flash"

    def __init__(self, outcomes: list[Exception | str]) -> None:
        """Outcomes are consumed one per call."""
        self.outcomes = list(outcomes)
        self.calls = 0

    def invoke(self, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResponse:
        """Raise or answer with the next outcome."""
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return LLMResponse(parsed={"x": 1}, raw_text="{}", served_model=out)


def _llm(tmp_path: Path, outcomes: list[Exception | str], start: datetime = T0) -> RateLimitedLLM:
    return RateLimitedLLM(
        Scripted(outcomes), SETTINGS, QuotaLedger(tmp_path / "ledger.jsonl"), SimulatedClock(start)
    )


def _call(llm: RateLimitedLLM) -> LLMResponse:
    return llm.invoke([ChatTurn(role="user", content="q")], _Schema)


# --- time ---------------------------------------------------------------------------


def test_quota_day_and_reset_follow_pacific_time() -> None:
    assert quota_day(T0, SETTINGS.quota_timezone) == date(2026, 11, 17)
    reset = next_reset(T0, SETTINGS.quota_timezone)
    assert reset == datetime(2026, 11, 18, 8, 0, tzinfo=UTC)  # midnight PST
    assert format_reset(reset, SETTINGS) == "2026-11-18 10:00 Cairo (00:00 America/Los_Angeles)"


def test_reset_is_dst_correct() -> None:
    # Summer: midnight PDT = 07:00 UTC; the day before the November change still PDT.
    summer = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert next_reset(summer, "America/Los_Angeles") == datetime(2026, 7, 2, 7, 0, tzinfo=UTC)
    before_change = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert next_reset(before_change, "America/Los_Angeles") == datetime(
        2026, 11, 1, 7, 0, tzinfo=UTC
    )


# --- pacing and budget -----------------------------------------------------------------


def test_requests_are_spaced_and_counted(tmp_path: Path) -> None:
    llm = _llm(tmp_path, ["m", "m", "m"])
    for _ in range(3):
        _call(llm)
    assert llm.clock.waits == [20, 20]  # first request immediate, then the gap
    b = budget(llm.ledger, SETTINGS, "google_genai", llm.clock.now())
    assert (b.sent, b.remaining) == (3, 14)


def test_gap_holds_across_instances_sharing_the_ledger(tmp_path: Path) -> None:
    first = _llm(tmp_path, ["m"])
    _call(first)
    second = _llm(tmp_path, ["m"], start=T0 + timedelta(seconds=5))  # e.g. the app
    _call(second)
    assert second.clock.waits == [15]


def test_budget_exhausted_stops_with_the_cairo_reset(tmp_path: Path) -> None:
    llm = _llm(tmp_path, ["m"] * 18)
    for _ in range(17):
        _call(llm)
    with pytest.raises(QuotaStopped, match="daily budget of 17") as info:
        _call(llm)
    assert "10:00 Cairo" in info.value.shown
    assert llm.inner.calls == 17  # the 18th request was never sent


def test_budget_resets_on_the_next_pacific_day(tmp_path: Path) -> None:
    llm = _llm(tmp_path, ["m"] * 18)
    for _ in range(17):
        _call(llm)
    llm.clock.sleep(
        (next_reset(llm.clock.now(), SETTINGS.quota_timezone) - llm.clock.now()).total_seconds()
    )
    _call(llm)
    assert budget(llm.ledger, SETTINGS, "google_genai", llm.clock.now()).sent == 1


def test_ledger_survives_a_restart_and_keeps_labels(tmp_path: Path) -> None:
    llm = _llm(tmp_path, ["gemini-3.6-flash"])
    with call_labels(purpose="evaluation", campaign_id="c1", node="diagnose"):
        _call(llm)
    again = QuotaLedger(tmp_path / "ledger.jsonl")
    request, result = again.entries()
    assert request["kind"] == "request" and request["campaign_id"] == "c1"
    assert request["node"] == "diagnose" and request["quota_day"] == "2026-11-17"
    assert result == {
        "kind": "result",
        "request_id": request["request_id"],
        "at": result["at"],
        "outcome": "ok",
        "served_model": "gemini-3.6-flash",
    }


# --- transport failures -------------------------------------------------------------------


def test_per_minute_429_waits_retry_delay_and_counts_both_requests(tmp_path: Path) -> None:
    llm = _llm(tmp_path, [_quota_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"), "m"])
    resp = _call(llm)
    assert [r["kind"] for r in resp.transport_retries] == ["minute"]
    assert resp.transport_retries[0]["waited_seconds"] == 37.0
    assert llm.clock.waits == [37.0]  # >= 20 s, so no extra gap wait
    assert budget(llm.ledger, SETTINGS, "google_genai", llm.clock.now()).sent == 2
    error = next(e for e in llm.ledger.entries() if e.get("outcome") == "error_minute")
    # The exact payload is kept, down to the quota id and retry delay.
    text = json.dumps(error["error"])
    assert "GenerateRequestsPerMinutePerProjectPerModel-FreeTier" in text and "37s" in text


def test_per_minute_429_without_delay_waits_the_default(tmp_path: Path) -> None:
    llm = _llm(tmp_path, [_quota_429("GenerateRequestsPerMinute", delay=None), "m"])
    _call(llm)
    assert llm.clock.waits == [65]


def test_per_day_429_stops_immediately(tmp_path: Path) -> None:
    llm = _llm(tmp_path, [_quota_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier"), "m"])
    with pytest.raises(QuotaStopped, match="per-day") as info:
        _call(llm)
    assert info.value.reset == datetime(2026, 11, 18, 8, 0, tzinfo=UTC)
    assert llm.inner.calls == 1


def test_unclassified_429s_are_capped(tmp_path: Path) -> None:
    llm = _llm(tmp_path, [_quota_429("SomethingElse")] * 6 + ["m"])
    with pytest.raises(QuotaStopped, match="5 retries on unclassified"):
        _call(llm)
    assert llm.inner.calls == 6


def test_server_errors_retry_three_times_then_fail(tmp_path: Path) -> None:
    ok = _llm(tmp_path / "a", [FakeAPIError(503, {"error": {"code": 503}}), "m"])
    resp = _call(ok)
    assert [r["kind"] for r in resp.transport_retries] == ["server"]
    failing = _llm(tmp_path / "b", [FakeAPIError(500, {})] * 4 + ["m"])
    with pytest.raises(TransportFailed, match="3 transport retries"):
        _call(failing)
    assert failing.inner.calls == 4


def test_timeouts_count_as_server_errors() -> None:
    class ReadTimeout(Exception):
        pass

    assert classify(ReadTimeout("slow"))[0] == "server"
    assert classify(ValueError("bad request"))[0] == "other"


def test_other_errors_are_not_retried(tmp_path: Path) -> None:
    llm = _llm(tmp_path, [FakeAPIError(400, {"error": "bad"}), "m"])
    with pytest.raises(FakeAPIError):
        _call(llm)
    assert llm.inner.calls == 1


# --- configuration ---------------------------------------------------------------------


def test_eval_yaml_holds_the_decided_limits() -> None:
    s = RateLimitSettings.load()
    assert (s.min_seconds_between_calls, s.seconds_between_runs, s.daily_request_budget) == (
        20,
        180,
        17,
    )
    assert s.worst_case_calls == {"processpilot": 6, "baseline": 3}
    assert (s.quota_timezone, s.display_timezone) == ("America/Los_Angeles", "Africa/Cairo")
    assert (s.max_transport_retries, s.max_server_retries) == (5, 3)
    assert s.limits("google_genai:gemini-3.6-flash") and not s.limits("anthropic:claude-opus-5-5")


def test_only_limited_providers_are_wrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PP_QUOTA_LEDGER", str(tmp_path / "l.jsonl"))
    gemini = Scripted([])
    assert isinstance(with_rate_limit(gemini, SETTINGS), RateLimitedLLM)
    other = Scripted([])
    other.model_id = "anthropic:claude-opus-5-5"
    assert with_rate_limit(other, SETTINGS) is other


def test_gemini_sdk_retries_are_off_in_the_built_request() -> None:
    # langchain-google-genai's default is 6 attempts; 1 = the first request only.
    from langchain_core.messages import HumanMessage
    from langchain_google_genai import ChatGoogleGenerativeAI

    from processpilot.agent.catalogue import load_agent_settings
    from processpilot.agent.llm import bare_model_name

    model_id = "google_genai:gemini-3.6-flash"
    kwargs = load_agent_settings().model.kwargs_for(model_id)
    chat = ChatGoogleGenerativeAI(model=bare_model_name(model_id), google_api_key="x", **kwargs)
    config = chat._prepare_request([HumanMessage(content="q")])["config"]
    assert config.http_options.retry_options.attempts == 1
    default = ChatGoogleGenerativeAI(model=bare_model_name(model_id), google_api_key="x")
    assert (
        default._prepare_request([HumanMessage(content="q")])[
            "config"
        ].http_options.retry_options.attempts
        == 6
    )
