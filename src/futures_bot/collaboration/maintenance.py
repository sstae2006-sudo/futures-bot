"""SIL Phase 4 "Intelligent Automation Layer" -- the "Automatic
Maintenance" piece: a low-frequency background thread that discards
stale draft work items (`is_draft=True`, untouched for
`automation.stale_draft_days`) and checks database connectivity via
`db.health.check_database_health()`, logging a summary each cycle.

Same hand-rolled daemon-thread shape `market_data/scheduler.py::MarketDataScheduler`
and `git_watcher.py::GitWatcherScheduler` already establish -- no new
concurrency idiom. Deliberately narrow: never touches a non-draft work
item (only `fetch_draft_work_items()` results are ever candidates for
discard), never modifies the database beyond that one delete, and the DB
health check is read-only.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import parse_db_timestamp
from .store import CollaborationError, get_collaboration_store
from ..journal import LOGGER_NAME

log = logging.getLogger(LOGGER_NAME)


@dataclass
class _Status:
    running: bool = False
    last_cycle_at: Optional[str] = None
    last_result: Optional[str] = None
    last_error: Optional[str] = None
    cycles_completed: int = 0
    stale_drafts_discarded_count: int = 0
    last_db_health_ok: Optional[bool] = None


class MaintenanceScheduler:
    """One instance runs this process's periodic housekeeping. Embedded in
    the API process, same lifecycle as `GitWatcherScheduler`/
    `MarketDataScheduler` -- see `api/app.py::_maybe_start_automation`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._status = _Status()

    def start(self, interval_seconds: int = 3600, stale_draft_days: float = 3.0) -> None:
        with self._lock:
            if self._status.running:
                raise RuntimeError("The maintenance scheduler is already running.")
            self._status = _Status(running=True)
            self._stop_event = threading.Event()
            stop_event = self._stop_event

        thread = threading.Thread(
            target=self._run, args=(interval_seconds, stale_draft_days, stop_event),
            daemon=True, name="futures-bot-maintenance",
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
            self._status.running = False

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._status.running,
                "last_cycle_at": self._status.last_cycle_at,
                "last_result": self._status.last_result,
                "last_error": self._status.last_error,
                "cycles_completed": self._status.cycles_completed,
                "stale_drafts_discarded_count": self._status.stale_drafts_discarded_count,
                "last_db_health_ok": self._status.last_db_health_ok,
            }

    def _run(self, interval_seconds: int, stale_draft_days: float, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self._run_cycle(datetime.now(timezone.utc), stale_draft_days)
            stop_event.wait(interval_seconds)

    def _run_cycle(self, now: datetime, stale_draft_days: float) -> None:
        try:
            discarded = self._discard_stale_drafts(now, stale_draft_days)
            db_health = self._check_database_health()
            db_summary = (
                "not configured (SQLite mode)" if not db_health.configured
                else f"ok ({db_health.latency_ms:.1f}ms)" if db_health.ok
                else f"UNREACHABLE ({db_health.error})"
            )
            with self._lock:
                self._status.last_cycle_at = now.isoformat()
                self._status.last_result = f"discarded {discarded} stale draft(s); DB: {db_summary}"
                self._status.last_error = None
                self._status.cycles_completed += 1
                self._status.stale_drafts_discarded_count += discarded
                self._status.last_db_health_ok = db_health.ok if db_health.configured else None
        except Exception as exc:  # noqa: BLE001 -- one bad cycle must not kill the thread
            log.error("maintenance cycle failed: %s", exc, exc_info=True)
            with self._lock:
                self._status.last_cycle_at = now.isoformat()
                self._status.last_error = str(exc)

    def _check_database_health(self):
        # Lazy import: `db.health` pulls in SQLAlchemy, which a SQLite-only
        # single-developer setup must never be required to install (see
        # `db/engine.py`'s own module docstring) -- same convention
        # `api/routes/system.py`'s health route already establishes.
        # `ModuleNotFoundError` here just means "this process can't
        # possibly be team-deployment mode," the same conclusion
        # `check_database_health()` itself reaches when the env var is
        # unset -- not a reason a maintenance cycle should error out.
        try:
            from ..db.health import check_database_health
        except ModuleNotFoundError:
            from types import SimpleNamespace

            return SimpleNamespace(configured=False, ok=False, latency_ms=None, error=None)
        return check_database_health()

    def _discard_stale_drafts(self, now: datetime, stale_draft_days: float) -> int:
        """Only ever reads `fetch_draft_work_items()` -- a real work item
        is never a candidate, by construction (see this module's own
        docstring)."""
        store = get_collaboration_store()
        cutoff = now - timedelta(days=stale_draft_days)
        discarded = 0
        for draft in store.fetch_draft_work_items():
            try:
                updated_at = parse_db_timestamp(draft["updated_at"])
            except (ValueError, TypeError):
                continue
            if updated_at > cutoff:
                continue  # still fresh; exactly at the cutoff counts as stale (inclusive)
            try:
                store.discard_draft_work_item(draft["id"])
                discarded += 1
            except CollaborationError:
                pass  # approved/discarded concurrently -- fine, ignore
        return discarded


_maintenance: Optional[MaintenanceScheduler] = None
_maintenance_lock = threading.Lock()


def get_maintenance_scheduler() -> MaintenanceScheduler:
    global _maintenance
    with _maintenance_lock:
        if _maintenance is None:
            _maintenance = MaintenanceScheduler()
        return _maintenance


def reset_maintenance_scheduler() -> None:
    """Test-only. Production code never calls this."""
    global _maintenance
    with _maintenance_lock:
        _maintenance = None
