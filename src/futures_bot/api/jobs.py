"""Background job manager (Phase 6B).

A single, process-wide `ThreadPoolExecutor` runs backtest/optimizer/
compare/report work off the request thread, with progress reported through
the `jobs` table (`research/trade_store.py`) so any request -- including a
different one than the request that submitted the job -- can poll or stream
its status.

Threads, not `asyncio`, on purpose: `services.py`'s functions
(`run_backtest_job`, `run_optimizer_job`, ...) are synchronous and call into
synchronous code (`backtest.runner.run_backtest`, which itself is a tight
Python loop, not I/O-bound) -- rewriting that whole call chain as async
would touch every layer for a local, single-user tool where a handful of
worker threads is already enough concurrency. This mirrors the same
threading model `api/store.py` already documented: FastAPI's own request
handling already runs in a thread pool, so this is one more,
purpose-specific pool underneath it, not a new paradigm.

The existing synchronous endpoints (`POST /api/backtest/run`, etc.) are
untouched by this module and keep working exactly as they did in Phase 6A
-- they call `services.py` directly, still blocking until done. The new
`/api/jobs/*` endpoints are what route through `JobManager`, so Phase 6A
API clients see no behavior change.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from ..journal import LOGGER_NAME
from .store import get_store

log = logging.getLogger(LOGGER_NAME)

#: Bounded so a burst of job submissions can't spawn unbounded threads --
#: generous for a single-user research tool (nothing here is meant to
#: serve concurrent users, see docs/RESEARCH_WORKSTATION.md).
_MAX_WORKERS = 4

_executor: Optional[ThreadPoolExecutor] = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="futures-bot-job")
    return _executor


def reset_executor() -> None:
    """Test-only: drops the cached executor so a test that wants a clean
    pool (or a different `FUTURES_BOT_RESEARCH_DB`) gets one. Production
    code never calls this."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=True, cancel_futures=False)
    _executor = None


class JobProgress:
    """Passed into a job's work function so it can report progress without
    knowing anything about HTTP, SSE, or the job's own id being threaded
    through every call -- it just calls `.update(...)`."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def update(self, current: int, total: Optional[int] = None, message: Optional[str] = None) -> None:
        # A fresh TradeStore per call, same reasoning as api/store.py: this
        # runs on a job worker thread, a different one than whatever
        # thread submitted the job, and sqlite3 connections aren't safe to
        # share across threads.
        get_store().update_job_progress(self.job_id, current=current, total=total, message=message)


def submit(
    kind: str, request: dict,
    work: Callable[[JobProgress], tuple[Optional[str], Optional[dict]]],
) -> str:
    """Queues `work` to run on the background thread pool and returns
    immediately with a job id.

    `work` receives a `JobProgress` it can call `.update()` on, and must
    return a ``(result_id, result_payload)`` pair:

    * ``result_id`` -- the id of whatever it produced that already has its
      own home (a `runs.id` for a backtest/optimizer job, a `reports.id`
      for a report job), or `None` if there isn't one.
    * ``result_payload`` -- the full result as a plain dict, for job kinds
      (chiefly `compare`) with no existing table to point `result_id` at.
      `None` when `result_id` is enough.

    Exceptions from `work` propagate into the job's `failed` status with
    the exception's message; they are logged but never raised back into the
    submitting thread (the whole point of a background job is that its
    caller has already moved on by the time it fails).
    """
    job_id = uuid.uuid4().hex[:12]
    get_store().insert_job(job_id=job_id, kind=kind, request=request)

    def _run() -> None:
        store = get_store()  # this thread's own connection
        store.start_job(job_id)
        try:
            result_id, result_payload = work(JobProgress(job_id))
            store.complete_job(job_id, result_id=result_id, result_payload=result_payload)
        except Exception as exc:  # noqa: BLE001 -- must never crash the worker thread
            log.error("Job %s (%s) failed: %s", job_id, kind, exc, exc_info=True)
            store.fail_job(job_id, f"{type(exc).__name__}: {exc}")

    _get_executor().submit(_run)
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    return get_store().fetch_job(job_id)


def list_jobs(status: Optional[str] = None, limit: int = 100) -> list[dict]:
    return get_store().fetch_jobs(status=status, limit=limit)
