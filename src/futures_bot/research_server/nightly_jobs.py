"""Phase 8B: submits nightly (and weekly) research through the **existing**
job system -- `api.jobs`/`api.services` -- rather than reinventing
backtest/optimizer/report execution. This is the one module in
`research_server` that imports from `api` (a one-way dependency: `api.jobs`
and `api.services` never import `research_server`, so this doesn't create
a cycle with `api/app.py`'s own dependency on `research_server
.orchestrator` to start everything on boot).

Every submission targets dataset ``db:{contract}:{resolution}`` (Phase
8A's market-data pseudo-dataset), so nightly research always runs against
whatever the data scheduler has freshly synced -- no dataset ever needs to
be picked by hand.

**Idempotent by construction.** A background thread wakes every
``check_interval_seconds`` and compares the current Central-Time date
against ``_last_run_date`` (in-memory); a match means today's batch
already went out, so nothing resubmits. A restart before today's trigger
hour just re-arms normally; a restart after it already fired doesn't
resubmit, because the actual results already live in `jobs`/`runs` --
this scheduler's only job is *deciding when to submit*, never redoing
work those tables already show as done.

**Parameter sweep, stated plainly**: the optimizer step sweeps whatever
`strategy_params` a strategy already has configured -- the exact same
"any list-valued entry is a sweep dimension" rule `cmd_optimize` and the
manual Optimizer dashboard page already use. No grid is auto-generated;
a strategy with only scalar params still runs (a one-combination sweep),
consistent with that existing behavior rather than inventing a new one.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Optional

from ..api import services as api_services
from ..api.schemas import BacktestRunRequest, CompareRequest, OptimizerRunRequest
from ..config import Settings
from ..contracts import CME_TZ
from ..journal import LOGGER_NAME

log = logging.getLogger(LOGGER_NAME)

#: How long to wait for a nightly walk-forward job to finish before giving
#: up on generating its report -- generous, since a full-history walk-
#: forward over a slow-changing local DB is the longest single step here,
#: but this must not hang the scheduler thread forever if something wedges.
_JOB_WAIT_TIMEOUT_SECONDS = 20 * 60
_JOB_POLL_INTERVAL_SECONDS = 2.0


def _dataset_name(settings: Settings) -> str:
    return f"db:{settings.contract}:{settings.research_server.resolution}"


def _params_for(settings: Settings, strategy_name: str) -> dict:
    return settings.strategy_params if strategy_name == settings.strategy_name else {}


def _wait_for_job(job_id: str, timeout: float = _JOB_WAIT_TIMEOUT_SECONDS) -> Optional[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = api_services.get_job(job_id)
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(_JOB_POLL_INTERVAL_SECONDS)
    log.error("Nightly job %s did not finish within %.0fs -- giving up waiting for it.", job_id, timeout)
    return None


class NightlyJobScheduler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._running = False
        self._last_run_date: Optional[date] = None
        self._last_run_summary: Optional[str] = None
        self._last_error: Optional[str] = None

    def start(self, settings: Settings, check_interval_seconds: int = 60) -> None:
        with self._lock:
            if self._running:
                raise RuntimeError("The nightly job scheduler is already running.")
            self._running = True
            self._stop_event = threading.Event()
            stop_event = self._stop_event

        thread = threading.Thread(
            target=self._loop, args=(settings, stop_event, check_interval_seconds),
            daemon=True, name="futures-bot-nightly-job-scheduler",
        )
        with self._lock:
            self._thread = thread
        thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self._running = False

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "last_run_date": self._last_run_date.isoformat() if self._last_run_date else None,
                "last_run_summary": self._last_run_summary,
                "last_error": self._last_error,
            }

    def run_now(self, settings: Settings, now_ct: Optional[datetime] = None) -> str:
        """Runs one batch immediately, bypassing the daily-trigger check --
        used by the dashboard's manual trigger and by tests that don't
        want to wait for the real clock. Updates the same
        `_last_run_date`/`_last_run_summary` state the scheduled path
        does, so a manual run also counts as "today's batch already ran"."""
        now_ct = now_ct or datetime.now(CME_TZ)
        summary = self._run_batch(settings, now_ct)
        with self._lock:
            self._last_run_date = now_ct.date()
            self._last_run_summary = summary
        return summary

    # --- Background thread body ---

    def _loop(self, settings: Settings, stop_event: threading.Event, check_interval_seconds: int) -> None:
        while not stop_event.is_set():
            now_ct = datetime.now(CME_TZ)
            with self._lock:
                already_ran_today = self._last_run_date == now_ct.date()
            if now_ct.hour == settings.research_server.nightly_job_hour_ct and not already_ran_today:
                try:
                    summary = self._run_batch(settings, now_ct)
                    with self._lock:
                        self._last_run_date = now_ct.date()
                        self._last_run_summary = summary
                        self._last_error = None
                except Exception as exc:  # noqa: BLE001 -- one bad night must not kill the scheduler thread.
                    log.error("Nightly job batch failed: %s", exc, exc_info=True)
                    with self._lock:
                        self._last_error = f"{type(exc).__name__}: {exc}"
            stop_event.wait(check_interval_seconds)

    def _run_batch(self, settings: Settings, now_ct: datetime) -> str:
        dataset = _dataset_name(settings)
        submitted: list[str] = []

        for name in settings.research_server.paper_strategies:
            params = _params_for(settings, name)
            try:
                bt_job = api_services.submit_backtest_job(
                    BacktestRunRequest(strategy_name=name, dataset=dataset, contract=settings.contract,
                                        strategy_params=params, walk_forward=False)
                )
                submitted.append(bt_job)

                opt_job = api_services.submit_optimizer_job(
                    OptimizerRunRequest(strategy_name=name, dataset=dataset, contract=settings.contract,
                                         param_grid=params, rolling=False)
                )
                submitted.append(opt_job)

                wf_job_id = api_services.submit_backtest_job(
                    BacktestRunRequest(strategy_name=name, dataset=dataset, contract=settings.contract,
                                        strategy_params=params, walk_forward=True)
                )
                submitted.append(wf_job_id)
                self._generate_report_when_ready(wf_job_id)
            except Exception as exc:  # noqa: BLE001 -- one strategy's failure must not block the others.
                log.error("Nightly research for %s failed: %s", name, exc, exc_info=True)

        if now_ct.weekday() == settings.research_server.weekly_report_weekday and settings.research_server.paper_strategies:
            try:
                cmp_job = api_services.submit_compare_job(
                    CompareRequest(dataset=dataset, contract=settings.contract,
                                   strategy_names=list(settings.research_server.paper_strategies))
                )
                submitted.append(cmp_job)
            except Exception as exc:  # noqa: BLE001
                log.error("Weekly comparison failed: %s", exc, exc_info=True)

        return f"{len(submitted)} job(s) submitted for {now_ct.date().isoformat()}"

    def _generate_report_when_ready(self, walk_forward_job_id: str) -> None:
        job = _wait_for_job(walk_forward_job_id)
        if job is None or job["status"] != "completed" or not job.get("result_id"):
            return
        try:
            api_services.submit_report_job(job["result_id"])
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to submit nightly report for run %s: %s", job["result_id"], exc, exc_info=True)


_scheduler: Optional[NightlyJobScheduler] = None
_scheduler_lock = threading.Lock()


def get_nightly_job_scheduler() -> NightlyJobScheduler:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = NightlyJobScheduler()
        return _scheduler


def reset_nightly_job_scheduler() -> None:
    """Test-only. Production code never calls this."""
    global _scheduler
    with _scheduler_lock:
        _scheduler = None
