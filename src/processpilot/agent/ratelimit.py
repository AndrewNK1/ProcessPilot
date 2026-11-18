"""Rate limiter and quota ledger for hosted models with a small quota (Gemini).

WHY in front of every call: the evaluation runs on a quota of 5 requests per
minute and 20 per day per Google Cloud project. Calls fired back to back
(including validation retries) hit the per-minute limit, and the provider
SDK's own retries hide how many requests were really sent. So:

- the SDK's retries are off (``max_retries: 1`` in ``configs/agent.yaml``);
- every request passes ``RateLimitedLLM``, which waits until the minimum gap
  since the last request has passed and refuses when today's budget is used;
- every request sent is written to one append-only ledger shared by the app
  and the campaign runner (the quota is per project, not per process), and
  counts against the budget whatever its outcome, 429s included (user
  decision 2026-11-18: whether Google counts rejected requests is
  UNVERIFIED, so counting them is the safe side);
- a 429 "per minute", a 5xx or a timeout is waited out and repeated
  (``transport retry``, capped, reported apart from validation retries); a
  429 "per day" stops the run (``QuotaStopped``) with the next reset time.

The quota day is the calendar day in ``quota_timezone`` (America/Los_Angeles;
it resets at local midnight, computed with ``zoneinfo``, so DST is handled);
reset times are shown in ``display_timezone`` (Africa/Cairo).

Every 429/5xx payload is stored verbatim in the ledger, because the rule
that tells per-minute from per-day (Google's quota id contains ``PerMinute``
or ``PerDay``) is UNVERIFIED until a real one has been seen.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from processpilot.agent.llm import ChatTurn, LLMResponse, StructuredLLM
from processpilot.config import find_project_root, load_config_yaml

_LEDGER_ENV = "PP_QUOTA_LEDGER"


class RateLimitSettings(BaseModel):
    """``rate_limit`` block of ``configs/eval.yaml``."""

    providers: list[str]
    min_seconds_between_calls: float = Field(ge=0)
    seconds_between_runs: float = Field(ge=0)
    daily_request_budget: int = Field(gt=0)
    worst_case_calls: dict[str, int]
    quota_timezone: str
    display_timezone: str
    default_retry_seconds: float = Field(gt=0)
    max_transport_retries: int = Field(ge=0)
    max_server_retries: int = Field(ge=0)
    ledger: str

    @classmethod
    def load(cls) -> RateLimitSettings:
        """Read the block from ``configs/eval.yaml``."""
        return cls.model_validate(load_config_yaml("eval.yaml")["rate_limit"])

    def ledger_path(self) -> Path:
        """``$PP_QUOTA_LEDGER`` or the configured path (relative to the project root)."""
        override = os.environ.get(_LEDGER_ENV)
        return Path(override) if override else find_project_root() / self.ledger

    def limits(self, model_id: str) -> bool:
        """Whether calls to ``model_id``'s provider go through the limiter."""
        return model_id.split(":", 1)[0] in self.providers


# --- time -------------------------------------------------------------------------------


class Clock(Protocol):
    """Wall clock + sleep, injectable so tests and dry runs never wait."""

    def now(self) -> datetime:
        """Timezone-aware UTC now."""
        ...

    def sleep(self, seconds: float) -> None:
        """Wait (or pretend to) for ``seconds``."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        """Timezone-aware UTC now."""
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        """Block for ``seconds``."""
        if seconds > 0:
            time.sleep(seconds)


@dataclass
class SimulatedClock:
    """A clock that jumps instead of waiting (dry runs, tests). Records every wait."""

    current: datetime
    waits: list[float] = field(default_factory=list)

    def now(self) -> datetime:
        """The simulated time."""
        return self.current

    def sleep(self, seconds: float) -> None:
        """Advance the simulated time; never blocks."""
        if seconds > 0:
            self.waits.append(seconds)
            self.current += timedelta(seconds=seconds)


def quota_day(moment: datetime, tz: str) -> date:
    """The calendar day of ``moment`` in the quota timezone."""
    return moment.astimezone(ZoneInfo(tz)).date()


def next_reset(moment: datetime, tz: str) -> datetime:
    """Next local midnight in ``tz`` after ``moment``, as UTC (DST-correct)."""
    zone = ZoneInfo(tz)
    local = moment.astimezone(zone)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), zone)
    return midnight.astimezone(UTC)


def format_reset(reset: datetime, settings: RateLimitSettings) -> str:
    """E.g. ``2026-11-19 10:00 Cairo (00:00 America/Los_Angeles)``."""
    shown = reset.astimezone(ZoneInfo(settings.display_timezone))
    quota = reset.astimezone(ZoneInfo(settings.quota_timezone))
    city = settings.display_timezone.split("/")[-1]
    return f"{shown:%Y-%m-%d %H:%M} {city} ({quota:%H:%M} {settings.quota_timezone})"


# --- labels: who is calling ------------------------------------------------------------

CALL_LABELS: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "call_labels", default=None
)


@contextmanager
def call_labels(**labels: Any) -> Iterator[None]:
    """Tag every request made inside the block (purpose, campaign, run, node...)."""
    token = CALL_LABELS.set({**(CALL_LABELS.get() or {}), **labels})
    try:
        yield
    finally:
        CALL_LABELS.reset(token)


# --- stops ------------------------------------------------------------------------------


class RunStopped(Exception):  # noqa: N818 -- a stop, not an error
    """Base of the stops that must end a run immediately (not a validation failure)."""


class QuotaStopped(RunStopped):
    """The daily quota is used up (our budget or Google's per-day 429)."""

    def __init__(self, reason: str, reset: datetime, shown: str) -> None:
        """Keep the reason and the next reset (UTC and as shown)."""
        super().__init__(f"{reason}; next reset {shown}")
        self.reason = reason
        self.reset = reset
        self.shown = shown


class ServedModelChanged(RunStopped):
    """The provider served a different model than the campaign's first call."""


class TransportFailed(RuntimeError):
    """Transport retries exhausted on 5xx/timeouts: an ordinary call failure."""


# --- error classification ---------------------------------------------------------------

ErrorKind = Literal["minute", "day", "unclassified_429", "server", "other"]


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or cur.__context__


def error_payload(exc: BaseException) -> dict[str, Any]:
    """Everything the exception chain says, verbatim (for the ledger)."""
    out: dict[str, Any] = {"chain": []}
    for e in _chain(exc):
        entry: dict[str, Any] = {"type": f"{type(e).__module__}.{type(e).__name__}", "str": str(e)}
        for attr in ("code", "status", "message", "details"):
            value = getattr(e, attr, None)
            if value is not None and not callable(value):
                try:
                    json.dumps(value)
                    entry[attr] = value
                except TypeError:
                    entry[attr] = str(value)
        out["chain"].append(entry)
    return out


_RETRY_DELAY = re.compile(
    r'retry_?delay["\']?\s*[:{]\s*["\']?(?:seconds:\s*)?(\d+(?:\.\d+)?)s?', re.I
)


def classify(exc: BaseException) -> tuple[ErrorKind, float | None]:
    """(kind, suggested retry delay in seconds) for a failed request."""
    codes = [getattr(e, "code", None) for e in _chain(exc)]
    text = json.dumps(error_payload(exc), default=str)
    delay_match = _RETRY_DELAY.search(text)
    delay = float(delay_match.group(1)) if delay_match else None
    if 429 in codes or "RESOURCE_EXHAUSTED" in text:
        if "PerDay" in text:
            return "day", delay
        if "PerMinute" in text:
            return "minute", delay
        return "unclassified_429", delay
    timeouts = ("Timeout", "TimeoutError", "ReadTimeout", "ConnectTimeout")
    if any(isinstance(c, int) and c >= 500 for c in codes) or any(
        type(e).__name__ in timeouts for e in _chain(exc)
    ):
        return "server", delay
    return "other", delay


# --- ledger -----------------------------------------------------------------------------


class QuotaLedger:
    """Append-only JSONL of every request sent (one line) and its result (one line).

    A file lock (``<ledger>.lock``, created exclusively) makes "check gap and
    budget, then record the request" atomic across the app and the runner.
    """

    def __init__(self, path: Path, lock_stale_seconds: float = 120.0) -> None:
        """Use (and create on first write) the ledger at ``path``."""
        self.path = Path(path)
        self._lock = self.path.with_name(self.path.name + ".lock")
        self._stale = lock_stale_seconds

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the ledger lock (a stale lock left by a killed process is taken over)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(self._lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                try:
                    age = time.time() - self._lock.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > self._stale:
                    self._lock.unlink(missing_ok=True)
                    continue
                time.sleep(0.05)
        try:
            yield
        finally:
            self._lock.unlink(missing_ok=True)

    def entries(self) -> list[dict[str, Any]]:
        """Every line, oldest first."""
        if not self.path.is_file():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def append(self, entry: dict[str, Any]) -> None:
        """Write one line (the caller holds the lock for request lines)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def requests(self, provider: str) -> list[dict[str, Any]]:
        """Request lines of one provider."""
        return [
            e
            for e in self.entries()
            if e.get("kind") == "request" and e.get("provider") == provider
        ]

    def sent_on(self, provider: str, day: date) -> int:
        """Requests sent on a quota day (every outcome counts)."""
        return sum(1 for e in self.requests(provider) if e.get("quota_day") == day.isoformat())

    def last_request_at(self, provider: str) -> datetime | None:
        """Time of the most recent request of ``provider``."""
        times = [datetime.fromisoformat(e["at"]) for e in self.requests(provider)]
        return max(times) if times else None


@dataclass
class Budget:
    """Today's quota position."""

    day: date
    sent: int
    remaining: int
    reset: datetime
    reset_shown: str


def budget(
    ledger: QuotaLedger, settings: RateLimitSettings, provider: str, now: datetime
) -> Budget:
    """How many requests are left in the current quota day."""
    day = quota_day(now, settings.quota_timezone)
    sent = ledger.sent_on(provider, day)
    reset = next_reset(now, settings.quota_timezone)
    return Budget(
        day=day,
        sent=sent,
        remaining=max(0, settings.daily_request_budget - sent),
        reset=reset,
        reset_shown=format_reset(reset, settings),
    )


# --- the wrapper ------------------------------------------------------------------------


class RateLimitedLLM:
    """A ``StructuredLLM`` that paces, counts and retries transport failures."""

    def __init__(
        self,
        inner: StructuredLLM,
        settings: RateLimitSettings,
        ledger: QuotaLedger,
        clock: Clock | None = None,
        provider: str | None = None,
        on_response: Callable[[LLMResponse], None] | None = None,
    ) -> None:
        """Wrap ``inner``; ``provider`` defaults to the model id's prefix."""
        self.inner = inner
        self.model_id = inner.model_id
        self.settings = settings
        self.ledger = ledger
        self.clock = clock or SystemClock()
        self.provider = provider or inner.model_id.split(":", 1)[0]
        self.on_response = on_response

    def _reserve(self) -> str:
        """Wait for the gap, check the budget, record the request; returns its id."""
        s = self.settings
        while True:
            with self.ledger.locked():
                now = self.clock.now()
                last = self.ledger.last_request_at(self.provider)
                wait = (
                    0.0
                    if last is None
                    else s.min_seconds_between_calls - (now - last).total_seconds()
                )
                if wait <= 0:
                    b = budget(self.ledger, s, self.provider, now)
                    if b.remaining <= 0:
                        raise QuotaStopped(
                            f"daily budget of {s.daily_request_budget} requests used "
                            f"({b.sent} sent on {b.day})",
                            b.reset,
                            b.reset_shown,
                        )
                    request_id = uuid.uuid4().hex
                    self.ledger.append(
                        {
                            "kind": "request",
                            "request_id": request_id,
                            "at": now.isoformat(),
                            "quota_day": b.day.isoformat(),
                            "provider": self.provider,
                            "model_id": self.model_id,
                            **(CALL_LABELS.get() or {}),
                        }
                    )
                    return request_id
            self.clock.sleep(wait)

    def _result(self, request_id: str, outcome: str, **extra: Any) -> None:
        self.ledger.append(
            {
                "kind": "result",
                "request_id": request_id,
                "at": self.clock.now().isoformat(),
                "outcome": outcome,
                **extra,
            }
        )

    def invoke(self, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResponse:
        """One logical call; transport failures are waited out and repeated."""
        s = self.settings
        retries: list[dict[str, Any]] = []
        counts = {"minute": 0, "unclassified_429": 0, "server": 0}
        while True:
            request_id = self._reserve()
            try:
                resp = self.inner.invoke(turns, schema)
            except Exception as exc:
                kind, delay = classify(exc)
                payload = error_payload(exc)
                self._result(request_id, f"error_{kind}", error=payload, retry_delay=delay)
                if kind == "day":
                    b = budget(self.ledger, s, self.provider, self.clock.now())
                    raise QuotaStopped(
                        "the provider refused with a per-day quota error (429)",
                        b.reset,
                        b.reset_shown,
                    ) from exc
                if kind == "other":
                    raise
                cap = s.max_server_retries if kind == "server" else s.max_transport_retries
                counts[kind] += 1
                if counts[kind] > cap:
                    if kind == "server":
                        raise TransportFailed(
                            f"{counts[kind] - 1} transport retries on server errors/timeouts "
                            f"exhausted: {exc!r}"
                        ) from exc
                    b = budget(self.ledger, s, self.provider, self.clock.now())
                    raise QuotaStopped(
                        f"{counts[kind] - 1} retries on unclassified 429s exhausted",
                        b.reset,
                        b.reset_shown,
                    ) from exc
                wait = delay if delay is not None else s.default_retry_seconds
                retries.append(
                    {
                        "request_id": request_id,
                        "kind": kind,
                        "waited_seconds": wait,
                        "error": str(exc)[:500],
                    }
                )
                self.clock.sleep(wait)
                continue
            self._result(request_id, "ok", served_model=resp.served_model)
            resp = resp.model_copy(update={"transport_retries": retries})
            if self.on_response is not None:
                self.on_response(resp)
            return resp


def with_rate_limit(
    llm: StructuredLLM,
    settings: RateLimitSettings | None = None,
    clock: Clock | None = None,
) -> StructuredLLM:
    """``llm`` behind the limiter if its provider is limited, else unchanged."""
    settings = settings or RateLimitSettings.load()
    if not settings.limits(llm.model_id):
        return llm
    return RateLimitedLLM(llm, settings, QuotaLedger(settings.ledger_path()), clock)
