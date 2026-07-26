"""Keeps the market-data database current automatically, whenever the
market is open, for a configured list of products.

A hand-rolled background thread, not a new dependency: this project's
`pyproject.toml` is deliberately minimal (pydantic, pyyaml, requests, plus
fastapi/uvicorn behind the optional `api` extra), and a single-user local
research tool doesn't need APScheduler/Celery to run "check every N
seconds, sync if the market's open" -- it needs exactly the shape
`api/live_session.py::LiveSessionManager` already uses (a daemon thread,
a `threading.Event` for interruptible sleep, a locked status snapshot).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import NamedTuple, Optional

from ..contracts import is_market_open
from ..journal import LOGGER_NAME
from .store import MarketDataStore
from .sync import sync_incremental

log = logging.getLogger(LOGGER_NAME)


class SyncTarget(NamedTuple):
    product_code: str
    resolution: str


@dataclass
class _Status:
    running: bool = False
    targets: list[str] = field(default_factory=list)
    last_cycle_at: Optional[str] = None
    last_result: Optional[str] = None  # human-readable summary of the last cycle
    last_error: Optional[str] = None
    cycles_completed: int = 0


class MarketDataScheduler:
    """One instance keeps a fixed list of (product, resolution) targets
    synced. Embedded in the API process (started alongside the FastAPI app,
    same lifecycle as `LiveSessionManager`) rather than a separate daemon
    process -- this is a single-user local tool with one research DB
    already accessed in-process from many other places; a second OS
    process would need its own SQLite-writer discipline for no real
    benefit here (see `store.py`'s docstring on why even *this* process
    already opens one connection per caller rather than sharing one)."""

    def __init__(self, db_path, api_key_getter) -> None:
        """``api_key_getter`` is a zero-arg callable returning the current
        `MASSIVE_API_KEY`, read fresh on every cycle rather than captured
        once at construction -- so a key set *after* the scheduler starts
        (a real startup-ordering case for a locally-run tool) still works
        on the next cycle instead of requiring a restart."""
        self._db_path = db_path
        self._api_key_getter = api_key_getter
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._status = _Status()

    def start(self, targets: list[SyncTarget], interval_seconds: int = 300) -> None:
        with self._lock:
            if self._status.running:
                raise RuntimeError("The market-data scheduler is already running.")
            self._status = _Status(running=True, targets=[f"{t.product_code}:{t.resolution}" for t in targets])
            self._stop_event = threading.Event()
            stop_event = self._stop_event

        thread = threading.Thread(
            target=self._run, args=(list(targets), interval_seconds, stop_event),
            daemon=True, name="futures-bot-data-sync-scheduler",
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
                "targets": list(self._status.targets),
                "last_cycle_at": self._status.last_cycle_at,
                "last_result": self._status.last_result,
                "last_error": self._status.last_error,
                "cycles_completed": self._status.cycles_completed,
            }

    def _run(self, targets: list[SyncTarget], interval_seconds: int, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            now = datetime.now(timezone.utc)
            if is_market_open(now):
                self._run_cycle(targets, now)
            stop_event.wait(interval_seconds)

    def _run_cycle(self, targets: list[SyncTarget], now: datetime) -> None:
        api_key = self._api_key_getter()
        if not api_key:
            with self._lock:
                self._status.last_error = "MASSIVE_API_KEY is not set -- skipped this cycle."
            return

        store = MarketDataStore(self._db_path)
        try:
            summaries = []
            for target in targets:
                try:
                    result = sync_incremental(store, api_key, target.product_code, target.resolution, now=now)
                    if result.rolled:
                        log.warning(
                            "Data pipeline: %s rolled from %s to %s",
                            target.product_code, result.rolled[0], result.rolled[1],
                        )
                    summaries.append(f"{target.product_code}:{target.resolution}={result.bars_fetched} new bars")
                except Exception as exc:  # noqa: BLE001 -- one target's failure must not stop the others
                    log.error("Data pipeline sync failed for %s: %s", target, exc, exc_info=True)
                    summaries.append(f"{target.product_code}:{target.resolution}=FAILED ({exc})")

            with self._lock:
                self._status.last_cycle_at = now.isoformat()
                self._status.last_result = "; ".join(summaries)
                self._status.last_error = None
                self._status.cycles_completed += 1
        finally:
            store.close()


_scheduler: Optional[MarketDataScheduler] = None
_scheduler_lock = threading.Lock()


def get_scheduler(db_path=None, api_key_getter=None) -> MarketDataScheduler:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            if db_path is None or api_key_getter is None:
                raise RuntimeError("get_scheduler() needs db_path/api_key_getter the first time it's called.")
            _scheduler = MarketDataScheduler(db_path, api_key_getter)
        return _scheduler


def reset_scheduler() -> None:
    """Test-only. Production code never calls this."""
    global _scheduler
    with _scheduler_lock:
        _scheduler = None
