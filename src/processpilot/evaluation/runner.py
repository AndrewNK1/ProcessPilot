"""Campaign runner: every evaluation run, one at a time, within the quota.

Rules (EVALUATION_PLAN.md Phase 5, user decisions 2026-11-16/18):

- **Order**: interleaved and fixed: repetition 1 of every segment x condition,
  then repetition 2, ... (``run_order``). A problem on day 4 then affects all
  segments and both conditions alike. Written to ``run_order.json`` once.
- **Strictly sequential**, resumable: ``done`` is skipped; ``pending``,
  ``quota_stopped`` and ``failed`` (transport errors, interruptions) are run
  again as a new attempt with a new agent run id. The cut-short attempt stays
  on disk and is listed in ``eval_run.json``. A run whose output failed
  validation after all retries is a result: ``done`` / ``failed_validation``,
  never re-run.
- **Quota**: a run starts only if today's remaining budget covers its worst
  case; runs are ``seconds_between_runs`` apart; every request passes the
  limiter and the shared ledger (``agent/ratelimit.py``).
- **Review**: the graph stops at ``human_review`` and is left interrupted; no
  decision is taken and the app refuses one (409). Outcome ``reached_review``.
- **Served model**: compared after every call with the campaign's first served
  model; a difference stops the runner (``ServedModelChanged``) until the
  analyst accepts it explicitly.
- **Where things go**: logs, to-be models and checkpoints in the campaign
  folder (``processpilot/<slug>/rep<k>/<agent_run_id>/``,
  ``checkpoints.sqlite``); the frozen mining run is only read.

The runner itself makes no decision about the campaign's validity; it calls
``preflight`` (``campaign.check_unchanged`` for a real campaign) before
starting and refuses on any problem.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple

from processpilot.agent.catalogue import AgentSettings, load_agent_settings
from processpilot.agent.graph import AgentRunner
from processpilot.agent.llm import LLMResponse, StructuredLLM
from processpilot.agent.ratelimit import (
    Clock,
    QuotaLedger,
    QuotaStopped,
    RateLimitedLLM,
    RateLimitSettings,
    ServedModelChanged,
    budget,
    call_labels,
)
from processpilot.agent.runlog import RunLogger
from processpilot.api.db import Database
from processpilot.mining.evidence import segment_slug

CONDITION_ORDER = ("processpilot", "baseline")
RUN_ORDER_FILE = "run_order.json"
STATE_FILE = "campaign_state.json"
EVAL_RUN_FILE = "eval_run.json"
CHECKPOINTS = "checkpoints.sqlite"
RETRYABLE = ("pending", "quota_stopped", "failed")
NOT_YET_AVAILABLE = {"baseline": "baseline condition not available until Phase 6"}


class Slot(NamedTuple):
    """One cell of the campaign grid."""

    repetition: int
    segment: str
    condition: str


@dataclass
class CampaignPlan:
    """What a runner works on (a real campaign, its dry run or a smoke run)."""

    campaign_id: str  # id in SQLite (a dry run uses "<id>-dry")
    campaign_dir: Path  # where this namespace writes
    run_dir: Path  # frozen mining run (read only)
    preset: dict[str, Any]
    segments: list[str]
    repetitions: int
    conditions: list[str]
    model_id: str
    purpose: Literal["evaluation", "dev"]

    @property
    def provider(self) -> str:
        """Model-id prefix; the ledger counts per provider."""
        return self.model_id.split(":", 1)[0]


def run_order(plan: CampaignPlan) -> list[Slot]:
    """Repetition-major, then segment (manifest order), then condition."""
    conditions = [c for c in CONDITION_ORDER if c in plan.conditions]
    return [
        Slot(rep, seg, cond)
        for rep in range(1, plan.repetitions + 1)
        for seg in plan.segments
        for cond in conditions
    ]


@dataclass
class Attempt:
    """One try at a slot (``eval_run.json`` keeps them all)."""

    attempt: int
    agent_run_id: str
    status: str
    outcome: str
    cut_short: bool
    reason: str | None
    requests_sent: int
    llm_calls: int
    validation_retries: dict[str, int]
    transport_retries: int
    input_tokens: int
    output_tokens: int
    seconds_per_node: dict[str, float]
    started_at: str
    finished_at: str
    duration_seconds: float
    simulated_clock: bool = False


@dataclass
class RunReport:
    """What ``run`` did, for the command line."""

    executed: list[tuple[Slot, Attempt]] = field(default_factory=list)
    stopped_because: str | None = None


class CampaignRunner:
    """Runs a plan's slots in order until done, out of budget or stopped."""

    def __init__(
        self,
        plan: CampaignPlan,
        db: Database,
        settings: RateLimitSettings,
        ledger: QuotaLedger,
        clock: Clock,
        llm_factory: Callable[[str], StructuredLLM],
        preflight: Callable[[], list[str]],
        *,
        agent_settings: AgentSettings | None = None,
        simulate_days: bool = False,
        out: Callable[[str], None] = print,
    ) -> None:
        """``llm_factory(segment)`` gives the unlimited model; the runner limits it."""
        self.plan = plan
        self.db = db
        self.settings = settings
        self.ledger = ledger
        self.clock = clock
        self.llm_factory = llm_factory
        self.preflight = preflight
        self.agent_settings = agent_settings or load_agent_settings()
        self.simulate_days = simulate_days
        self.out = out

    # --- state -------------------------------------------------------------------

    def _state(self) -> dict[str, Any]:
        path = self.plan.campaign_dir / STATE_FILE
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.plan.campaign_dir.mkdir(parents=True, exist_ok=True)
        (self.plan.campaign_dir / STATE_FILE).write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )

    def accept_served_model(self, model: str, note: str) -> None:
        """Record the analyst's decision to continue with a different served model."""
        state = self._state()
        state.setdefault("served_model_decisions", []).append(
            {
                "accepted": model,
                "previous": state.get("served_model"),
                "note": note,
                "at": self.clock.now().isoformat(),
            }
        )
        state["served_model"] = model
        state.pop("stop", None)
        self._save_state(state)

    def ensure_grid(self) -> None:
        """Create every row once, write the fixed order, recover interrupted rows."""
        order = run_order(self.plan)
        for slot in order:
            self.db.add_eval_run(
                self.plan.campaign_id, slot.segment, slot.condition, slot.repetition
            )
        path = self.plan.campaign_dir / RUN_ORDER_FILE
        if not path.is_file():
            self.plan.campaign_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps([s._asdict() for s in order], indent=2) + "\n", encoding="utf-8"
            )
        for row in self.db.eval_runs(self.plan.campaign_id):
            if row["status"] == "running":  # the previous process died mid-run
                self.db.update_eval_run(
                    self.plan.campaign_id,
                    row["segment"],
                    row["condition"],
                    row["repetition"],
                    status="failed",
                    outcome="interrupted",
                )

    # --- the loop ----------------------------------------------------------------

    def run(self, max_runs: int | None = None, only_condition: str | None = None) -> RunReport:
        """Execute runnable slots in order; returns what happened and why it stopped."""
        problems = self.preflight()
        if problems:
            raise RuntimeError("campaign may not run:\n- " + "\n- ".join(problems))
        stop = self._state().get("stop")
        if stop:
            raise RuntimeError(
                f"runner stopped earlier: {stop['reason']}. Decide, then rerun with "
                "--accept-served-model <name>"
            )
        self.ensure_grid()
        report = RunReport()
        rows = {
            (r["segment"], r["condition"], r["repetition"]): r
            for r in self.db.eval_runs(self.plan.campaign_id)
        }
        for slot in run_order(self.plan):
            if max_runs is not None and len(report.executed) >= max_runs:
                report.stopped_because = f"--max-runs {max_runs} reached"
                break
            if only_condition and slot.condition != only_condition:
                continue
            if slot.condition in NOT_YET_AVAILABLE:
                continue
            row = rows[(slot.segment, slot.condition, slot.repetition)]
            if row["status"] not in RETRYABLE:
                continue
            if not self._wait_for_budget(slot, report):
                break
            self._wait_between_runs()
            attempt = self._execute(slot, int(row["attempt_count"]) + 1)
            report.executed.append((slot, attempt))
            self.out(self.status_line(slot, attempt))
            if attempt.outcome == "served_model_changed":
                report.stopped_because = attempt.reason
                break
            if attempt.status == "quota_stopped" and not self.simulate_days:
                report.stopped_because = attempt.reason
                break
        else:
            report.stopped_because = report.stopped_because or "no runnable slot left"
        return report

    def _wait_for_budget(self, slot: Slot, report: RunReport) -> bool:
        need = self.settings.worst_case_calls[slot.condition]
        while True:
            b = budget(self.ledger, self.settings, self.plan.provider, self.clock.now())
            if b.remaining >= need:
                return True
            if not self.simulate_days:
                report.stopped_because = (
                    f"today's budget has {b.remaining} requests left, a {slot.condition} run "
                    f"may need {need}; next reset {b.reset_shown}"
                )
                return False
            # Dry run: jump to the next quota day instead of waiting for it.
            self.clock.sleep((b.reset - self.clock.now()).total_seconds() + 1)
            self.out(f"[simulated] new quota day {b.reset.date()} ({b.reset_shown})")

    def _wait_between_runs(self) -> None:
        last = self._state().get("last_run_finished_at")
        if last is None:
            return
        gap = (
            self.settings.seconds_between_runs
            - (self.clock.now() - datetime.fromisoformat(last)).total_seconds()
        )
        if gap > 0:
            self.clock.sleep(gap)

    def _check_served(self, resp: LLMResponse) -> None:
        if not resp.served_model:
            return
        state = self._state()
        expected = state.get("served_model")
        if expected is None:
            state["served_model"] = resp.served_model
            state["served_model_first_seen_at"] = self.clock.now().isoformat()
            self._save_state(state)
        elif resp.served_model != expected:
            reason = f"served model changed from {expected!r} to {resp.served_model!r}"
            state["stop"] = {"reason": reason, "at": self.clock.now().isoformat()}
            self._save_state(state)
            raise ServedModelChanged(reason)

    # --- one run ---------------------------------------------------------------

    def _slot_dir(self, slot: Slot) -> Path:
        return (
            self.plan.campaign_dir
            / slot.condition
            / segment_slug(slot.segment)
            / f"rep{slot.repetition}"
        )

    def _new_agent_run_id(self, slot_dir: Path, segment: str, suffix: str = "") -> str:
        """Unique per mining run and segment (the index key), e.g. ``20261118T101500Z-rep1-a1``."""
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + suffix
        candidate, n = stamp, 1
        while (slot_dir / candidate).exists() or self.db.agent_run(
            self.plan.run_dir.name, segment, candidate
        ):
            n += 1
            candidate = f"{stamp}-{n}"
        return candidate

    def _execute(self, slot: Slot, attempt_no: int) -> Attempt:
        slot_dir = self._slot_dir(slot)
        key = (self.plan.campaign_id, slot.segment, slot.condition, slot.repetition)
        agent_run_id = self._new_agent_run_id(
            slot_dir, slot.segment, f"-rep{slot.repetition}-a{attempt_no}"
        )
        self.db.update_eval_run(
            *key,
            status="running",
            attempt_count=attempt_no,
            agent_run_id=agent_run_id,
            started_at=self.clock.now().isoformat(),
        )
        attempt = self.run_agent(
            slot.segment,
            slot_dir,
            agent_run_id,
            attempt_no,
            labels={
                "campaign_id": self.plan.campaign_id,
                "repetition": slot.repetition,
                "condition": slot.condition,
            },
        )
        self.db.update_eval_run(
            *key,
            status=attempt.status,
            outcome=attempt.outcome,
            calls_used=attempt.requests_sent,
            input_tokens=attempt.input_tokens,
            output_tokens=attempt.output_tokens,
            finished_at=attempt.finished_at,
        )
        self._write_eval_run(slot, slot_dir, attempt)
        state = self._state()
        state["last_run_finished_at"] = self.clock.now().isoformat()
        self._save_state(state)
        return attempt

    def run_agent(
        self,
        segment: str,
        slot_dir: Path,
        agent_run_id: str,
        attempt_no: int,
        labels: dict[str, Any],
        in_campaign: bool = True,
    ) -> Attempt:
        """One ProcessPilot run up to the review interrupt (also used by the smoke run)."""
        plan = self.plan
        log_dir = slot_dir / agent_run_id
        checkpoint = plan.campaign_dir / CHECKPOINTS
        if self.db.run(plan.run_dir.name) is None:  # an index rebuilt since mining
            self.db.add_run(
                plan.run_dir.name, "disk", "mined", purpose="evaluation", campaign_id=None
            )
        self.db.add_agent_run(
            plan.run_dir.name,
            segment,
            agent_run_id,
            plan.model_id,
            purpose=plan.purpose,
            campaign_id=plan.campaign_id if in_campaign else None,
            launched_by="batch",
            condition="processpilot",
            log_dir=str(log_dir),
            checkpoint_path=str(checkpoint),
        )
        llm = RateLimitedLLM(
            self.llm_factory(segment),
            self.settings,
            self.ledger,
            self.clock,
            provider=plan.provider,
            on_response=self._check_served,
        )
        started_at = self.clock.now()
        wall = time.perf_counter()
        status, outcome, reason, cut_short = "failed", "error", None, False
        try:
            with (
                call_labels(
                    purpose=plan.purpose,
                    run_id=plan.run_dir.name,
                    segment=segment,
                    agent_run_id=agent_run_id,
                    attempt_no=attempt_no,
                    **labels,
                ),
                AgentRunner(
                    plan.run_dir,
                    plan.preset,
                    llm=llm,
                    settings=self.agent_settings,
                    checkpoint_path=checkpoint,
                    log_dir_for=lambda _s, _a: log_dir,
                ) as runner,
            ):
                _, state = runner.start(segment, None, agent_run_id=agent_run_id)
            agent_status = state.status
            events = RunLogger(log_dir).events()
            if state.status == "awaiting_review":
                status, outcome = "done", "reached_review"
            elif any(e["kind"] == "llm_error" for e in events):
                status, outcome, reason = "failed", "transport_error", state.error
            elif any(e["kind"] == "node_failed" for e in events):
                status, outcome, reason = "done", "failed_validation", state.error
            else:
                reason = state.error
        except QuotaStopped as exc:
            agent_status, status, outcome = "stopped", "quota_stopped", "cut_short_quota"
            reason, cut_short = str(exc), True
        except ServedModelChanged as exc:
            agent_status, status, outcome = "stopped", "failed", "served_model_changed"
            reason, cut_short = str(exc), True
        self.db.set_agent_status(plan.run_dir.name, segment, agent_run_id, agent_status)
        return self._attempt(
            log_dir,
            agent_run_id,
            attempt_no,
            status,
            outcome,
            reason,
            cut_short,
            started_at,
            time.perf_counter() - wall,
        )

    def _attempt(
        self,
        log_dir: Path,
        agent_run_id: str,
        attempt_no: int,
        status: str,
        outcome: str,
        reason: str | None,
        cut_short: bool,
        started_at: datetime,
        seconds: float,
    ) -> Attempt:
        calls = [e for e in RunLogger(log_dir).events() if e["kind"] == "llm_call"]
        retries: dict[str, int] = {}
        per_node: dict[str, float] = {}
        for c in calls:
            if c["attempt"] > 0:
                retries[c["node"]] = retries.get(c["node"], 0) + 1
            spent = (
                datetime.fromisoformat(c["finished_at"]) - datetime.fromisoformat(c["started_at"])
            ).total_seconds()
            per_node[c["node"]] = round(per_node.get(c["node"], 0.0) + spent, 3)
        sent = sum(
            1
            for e in self.ledger.entries()
            if e.get("kind") == "request" and e.get("agent_run_id") == agent_run_id
        )
        return Attempt(
            attempt=attempt_no,
            agent_run_id=agent_run_id,
            status=status,
            outcome=outcome,
            cut_short=cut_short,
            reason=reason,
            requests_sent=sent,
            llm_calls=len(calls),
            validation_retries=retries,
            transport_retries=sum(len(c.get("transport_retries") or []) for c in calls),
            input_tokens=sum((c["usage"].get("input_tokens") or 0) for c in calls),
            output_tokens=sum((c["usage"].get("output_tokens") or 0) for c in calls),
            seconds_per_node=per_node,
            started_at=started_at.isoformat(),
            finished_at=self.clock.now().isoformat(),
            duration_seconds=round(seconds, 3),
            simulated_clock=self.simulate_days,
        )

    def _write_eval_run(self, slot: Slot, slot_dir: Path, attempt: Attempt) -> None:
        path = slot_dir / EVAL_RUN_FILE
        record = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.is_file()
            else {
                "campaign_id": self.plan.campaign_id,
                "segment": slot.segment,
                "condition": slot.condition,
                "repetition": slot.repetition,
                "mining_run_id": self.plan.run_dir.name,
                "model_id": self.plan.model_id,
                "attempts": [],
            }
        )
        record["attempts"].append(attempt.__dict__)
        record.update(
            status=attempt.status,
            outcome=attempt.outcome,
            final_agent_run_id=attempt.agent_run_id,
        )
        slot_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    # --- reporting -----------------------------------------------------------------

    def status_line(self, slot: Slot, attempt: Attempt) -> str:
        """One line after every run."""
        b = budget(self.ledger, self.settings, self.plan.provider, self.clock.now())
        rows = self.db.eval_runs(self.plan.campaign_id)
        done = sum(1 for r in rows if r["status"] == "done")
        return (
            f"rep {slot.repetition} | {slot.segment} | {slot.condition}: {attempt.outcome} "
            f"({attempt.requests_sent} requests, {sum(attempt.validation_retries.values())} "
            f"validation / {attempt.transport_retries} transport retries) | campaign "
            f"{done}/{len(rows)} done | today {b.sent} sent, {b.remaining} left | reset "
            f"{b.reset_shown}"
        )

    def status(self) -> str:
        """Progress without calling anything."""
        p = self.plan
        rows = self.db.eval_runs(p.campaign_id)
        b = budget(self.ledger, self.settings, p.provider, self.clock.now())
        lines = [f"campaign {p.campaign_id} | frozen run {p.run_dir.name} | model {p.model_id}"]
        if not rows:
            lines.append("grid not created yet (no run started)")
        else:
            lines.append(
                f"{'segment':<34}{'condition':<14}{'done':>6}{'review':>8}"
                f"{'failed_val':>12}{'pending':>9}{'stopped':>9}{'failed':>8}"
            )
            for seg in p.segments:
                for cond in [c for c in CONDITION_ORDER if c in p.conditions]:
                    sub = [r for r in rows if r["segment"] == seg and r["condition"] == cond]
                    statuses = [r["status"] for r in sub]
                    outcomes = [r["outcome"] for r in sub]
                    note = f"  ({NOT_YET_AVAILABLE[cond]})" if cond in NOT_YET_AVAILABLE else ""
                    lines.append(
                        f"{seg:<34}{cond:<14}{statuses.count('done'):>6}"
                        f"{outcomes.count('reached_review'):>8}"
                        f"{outcomes.count('failed_validation'):>12}"
                        f"{statuses.count('pending'):>9}"
                        f"{statuses.count('quota_stopped'):>9}"
                        f"{statuses.count('failed'):>8}{note}"
                    )
            done = sum(1 for r in rows if r["status"] == "done")
            lines.append(f"total {done}/{len(rows)} done")
        per_day: dict[str, int] = {}
        for e in self.ledger.requests(p.provider):
            per_day[e["quota_day"]] = per_day.get(e["quota_day"], 0) + 1
        lines.append(
            f"quota ({p.provider}, ledger {self.ledger.path}): today {b.day} {b.sent} sent, "
            f"{b.remaining} of {self.settings.daily_request_budget} left; "
            f"next reset {b.reset_shown}"
        )
        if per_day:
            lines.append(
                "requests per quota day: "
                + ", ".join(f"{d} {k}" for d, k in sorted(per_day.items()))
            )
        state = self._state()
        if state.get("served_model"):
            lines.append(f"served model: {state['served_model']}")
        if state.get("stop"):
            lines.append(f"STOPPED: {state['stop']['reason']}")
        return "\n".join(lines)
