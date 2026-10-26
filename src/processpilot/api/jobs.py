"""Background jobs: slow pipeline steps run off the request thread.

WHY jobs instead of long requests: profiling the full BPI XES the first time
takes minutes, mining about 1.5 minutes, an agent run several model calls.
An HTTP request that blocks that long times out in the browser and cannot
report progress. A job returns an id at once; the UI polls it, and the
latest progress line is stored with it.

Jobs run in a small thread pool inside the API process (``job_workers`` in
``configs/app.yaml``). If the process stops, unfinished jobs are marked
``interrupted`` on the next start (``Database.interrupt_unfinished_jobs``);
nothing is lost that matters, because runs live on disk and agent runs in
their LangGraph checkpoints.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from processpilot.api.db import Database

log = logging.getLogger(__name__)

Progress = Callable[[str], None]
JobFn = Callable[[Progress], dict[str, Any] | None]


class JobRunner:
    """Runs ``JobFn``s in worker threads and records their life in the database.

    ``inline=True`` runs each job to completion inside ``submit``: tests use
    it so a request's effects are visible as soon as it returns.
    """

    def __init__(self, db: Database, workers: int, inline: bool = False) -> None:
        """Start the pool (unless inline)."""
        self.db = db
        self.inline = inline
        self._pool = None if inline else ThreadPoolExecutor(workers, thread_name_prefix="pp-job")
        self._futures: dict[str, Future[None]] = {}

    def submit(
        self,
        kind: str,
        target: dict[str, Any],
        fn: JobFn,
        on_failure: Callable[[str], None] | None = None,
    ) -> str:
        """Queue ``fn``; returns the job id. ``on_failure`` gets the error text."""
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        self.db.add_job(job_id, kind, target)
        if self._pool is None:
            self._execute(job_id, fn, on_failure)
        else:
            self._futures[job_id] = self._pool.submit(self._execute, job_id, fn, on_failure)
        return job_id

    def _execute(self, job_id: str, fn: JobFn, on_failure: Callable[[str], None] | None) -> None:
        self.db.update_job(job_id, status="running")
        try:
            result = fn(lambda message: self.db.update_job(job_id, message=message))
        except Exception as exc:  # a job must never take the worker down
            error = f"{type(exc).__name__}: {exc}"
            log.error("job %s failed\n%s", job_id, traceback.format_exc())
            if on_failure is not None:
                try:
                    on_failure(error)
                except Exception:  # pragma: no cover -- bookkeeping must not mask the error
                    log.exception("on_failure of job %s raised", job_id)
            self.db.update_job(job_id, status="failed", error=error)
            return
        self.db.update_job(job_id, status="succeeded", result=result, message="done")

    def shutdown(self) -> None:
        """Wait for running jobs; queued ones are dropped (marked interrupted next start)."""
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
