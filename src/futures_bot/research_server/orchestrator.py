"""Phase 8B: `ResearchServer` composes the three background systems that
together make the platform self-maintaining -- `market_data.scheduler
.MarketDataScheduler` (Phase 8A, unmodified), `paper_trader
.AutonomousPaperTrader`, and `nightly_jobs.NightlyJobScheduler` -- behind
one `start()`/`stop()`/`status()`, the same module-level-singleton shape
`api.live_session.get_live_session_manager` and `market_data.scheduler
.get_scheduler` already use.

**Always reaches its three subsystems through their own module-level
singleton accessors** (`get_paper_trader()`, `get_nightly_job_scheduler()`,
`get_market_data_scheduler()`), never by constructing a private instance --
an earlier version of this class constructed its own `AutonomousPaperTrader`/
`NightlyJobScheduler` directly, which meant `api/research_server_service.py`
's `run_nightly_batch_now` (the dashboard's "Run nightly batch now" button,
reached via `get_nightly_job_scheduler()`) was silently operating on a
*different* scheduler object than the one this class actually starts and
reports status for: the button still worked (job submission is a stateless
module-level call), but the resulting `last_run_date`/`last_run_summary`
landed on the wrong instance and never showed up in `/api/research-server
/status`. Routing everything through the shared singletons closes that gap
structurally -- there is exactly one paper trader and one nightly-job
scheduler in the process, however it's reached.

`api/app.py` is the only caller that starts this automatically (on boot,
only if `Settings.research_server.enabled`); the dashboard route can also
start/stop it by hand for the same reason `MarketDataScheduler`'s own
dashboard controls exist alongside its potential auto-start.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from ..config import Settings
from ..journal import LOGGER_NAME
from ..market_data.scheduler import SyncTarget
from ..market_data.scheduler import get_scheduler as get_market_data_scheduler
from ..market_data.store import default_db_path as market_data_db_path
from .nightly_jobs import get_nightly_job_scheduler
from .paper_trader import get_paper_trader

log = logging.getLogger(LOGGER_NAME)


class ResearchServerError(RuntimeError):
    pass


class ResearchServer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._started_at: Optional[datetime] = None
        self._data_scheduler_started_here = False

    def start(self, settings: Settings, api_key: str) -> dict:
        with self._lock:
            if self._running:
                raise ResearchServerError("The research server is already running.")
            # Claimed atomically with the check above -- everything below (three
            # subsystem starts, each potentially slow) used to run with
            # `_running` still False, so two concurrent start() calls could
            # both pass the check before either actually marked itself running
            # (the same shape existed one layer down in
            # AutonomousPaperTrader.start(), fixed alongside this). Released
            # back to False by the except block below if anything fails.
            self._running = True

        rs = settings.research_server

        # The *same* singleton the Market Data dashboard page's manual
        # start/stop controls already use (see api/market_data_service.py)
        # -- so "the research server auto-started data sync" and "someone
        # clicked Start on the Market Data page" are never two different
        # schedulers silently racing each other.
        data_scheduler = get_market_data_scheduler(market_data_db_path(), lambda: api_key)
        targets = [SyncTarget(product_code=p, resolution=rs.resolution) for p in rs.data_sync_products]
        started_scheduler_here = False

        # Each of the three subsystems below is started in sequence; if a
        # later one raises, whichever earlier ones already started must be
        # torn back down before re-raising -- otherwise (the bug this
        # replaced) a paper trader can end up live in the background while
        # `self._running` never became True, making it unreachable through
        # `stop()` (which early-returns when not running) until process exit.
        try:
            if targets and not data_scheduler.status()["running"]:
                try:
                    data_scheduler.start(targets, interval_seconds=rs.poll_seconds * 10)
                    started_scheduler_here = True
                except RuntimeError:
                    # Lost a race with a concurrent "Start scheduler" click on
                    # the Market Data page -- it's running either way, which
                    # is what this call wanted; just don't claim ownership of
                    # a scheduler this call didn't actually start.
                    pass

            if rs.paper_strategies:
                get_paper_trader().start(settings, api_key)

            get_nightly_job_scheduler().start(settings)
        except Exception:
            log.error("research_server.start() failed partway through -- rolling back what already started.", exc_info=True)
            if started_scheduler_here:
                try:
                    data_scheduler.stop()
                except Exception:  # noqa: BLE001 -- best-effort rollback, must not mask the original error
                    log.error("Rollback: failed to stop the data scheduler.", exc_info=True)
            try:
                get_paper_trader().stop()
            except Exception:  # noqa: BLE001
                log.error("Rollback: failed to stop the paper trader.", exc_info=True)
            try:
                get_nightly_job_scheduler().stop()
            except Exception:  # noqa: BLE001
                log.error("Rollback: failed to stop the nightly job scheduler.", exc_info=True)
            with self._lock:
                self._running = False  # release the claim made at the top of start()
            raise

        with self._lock:
            self._data_scheduler_started_here = started_scheduler_here
            # _running is already True -- claimed atomically with the check
            # at the top of start(), before the three subsystems above started.
            self._started_at = datetime.now(timezone.utc)
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            not_running = not self._running
            stop_data_scheduler = self._data_scheduler_started_here
        if not_running:
            return self.status()  # outside the lock -- status() acquires it itself

        # Only stop the data scheduler if this server was the one that
        # started it -- if the Market Data dashboard page already had it
        # running independently before this server booted, stopping the
        # research server shouldn't yank that away.
        if stop_data_scheduler:
            get_market_data_scheduler().stop()
        get_paper_trader().stop()
        get_nightly_job_scheduler().stop()

        with self._lock:
            self._running = False
            self._data_scheduler_started_here = False
        return self.status()

    def status(self) -> dict:
        with self._lock:
            running = self._running
            started_at = self._started_at

        try:
            data_scheduler_status = get_market_data_scheduler().status()
        except RuntimeError:
            data_scheduler_status = {"running": False}

        uptime_seconds = (datetime.now(timezone.utc) - started_at).total_seconds() if started_at else None
        return {
            "running": running,
            "started_at": started_at.isoformat() if started_at else None,
            "uptime_seconds": uptime_seconds,
            "data_scheduler": data_scheduler_status,
            "paper_trader": get_paper_trader().status(),
            "nightly_jobs": get_nightly_job_scheduler().status(),
        }


_server: Optional[ResearchServer] = None
_server_lock = threading.Lock()


def get_research_server() -> ResearchServer:
    global _server
    with _server_lock:
        if _server is None:
            _server = ResearchServer()
        return _server


def reset_research_server() -> None:
    """Test-only. Production code never calls this."""
    global _server
    with _server_lock:
        _server = None
