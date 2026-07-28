"""Exposes the existing paper-trading engine (`cli.cmd_live`'s live-polled
loop) through the research API, so it can be started/stopped/watched from
the dashboard instead of only a terminal.

**Paper only, enforced at runtime, not just by convention.** Everything
else in `api/` refuses to import `brokers.tradovate` at all (see
`tests/test_api_routes.py::TestNoUnsafeTradingControls`) -- this module is
the one exception that touches `engine.build_engine`, which *can*
construct a `TradovateBroker` if `settings.broker.name` says so. The guard
is `start()`'s very first check: refuse before `build_engine` is ever
called if `broker.name != "paper"`. That check is what actually matters,
not which modules get imported -- see this module's own test coverage in
`tests/test_api_live_session.py` for a case that proves this fires before
any broker/feed object is constructed. Real trading remains
terminal-only, deliberately: `python -m futures_bot.cli --live` still
requires someone at a keyboard to have read `brokers/tradovate.py`'s
safety checklist and typed the command themselves.

Threading model: one background thread runs the poll loop (mirroring
`cli.cmd_live` almost line for line); HTTP request threads only ever read
a locked snapshot dict this thread updates after each bar, never touching
`engine`/`broker` objects directly -- the same reasoning `api/store.py`
and `api/jobs.py` already document for why cross-thread access to
non-thread-safe objects (a `TradeStore`'s `sqlite3.Connection`,
`PaperBroker`'s internal position state) has to be avoided rather than
patched around.

**Phase 7a: live trades are persisted the same way a backtest's are.**
Before this, a live session's fills only ever reached `decisions.jsonl` --
invisible to Trade Explorer, the Dashboard, and Market Regime, and lost
entirely if the API process restarted mid-session. `live_trade_journal
.LiveTradeJournal` (a `CountingJournal` -- see `backtest.runner`, and that
module for why it lives at the top level rather than here) is wired into
the engine via `build_engine(..., journal=...)`, the same optional hook
`api.services._run_with_journal` already uses for backtests; its `trade()`
override fires exactly once per closed trade, on the same background
thread that owns the engine, and immediately writes one `TradeRecord` to
the shared `TradeStore` -- so a live session's trades show up in the
research half of the app in near real time, not just at the end. A `runs`
row (`kind="live"`) is opened at `start()` and finalized (`complete_run`/
`fail_run`) in `_run`'s `finally`, mirroring
`api.services._persist_completed_run`.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..backtest.metrics import BacktestMetrics
from ..config import Settings, load_settings
from ..contracts import session_date
from ..engine import TradingEngine, build_engine
from ..journal import LOGGER_NAME
from ..live_trade_journal import LiveTradeJournal
from ..market_data.store import get_market_data_store
from ..models import Position
from ..strategy.base import StrategyRegistry
from . import services  # noqa: F401 -- importing this registers every bundled strategy (see its own docstring)
from .services import ApiError, _build_strategy  # reuse the exact same strategy construction path
from .store import get_store

log = logging.getLogger(LOGGER_NAME)


@dataclass
class _Snapshot:
    status: str = "stopped"  # 'stopped' | 'starting' | 'running' | 'stopping' | 'error'
    #: `runs.id` for this session (kind='live') -- lets a client jump from
    #: the live dashboard into Trade Explorer / `/api/backtests/{run_id}`
    #: once trades exist. Set once at `start()`, kept for the life of the
    #: snapshot (including after stop/error) so the last session's trades
    #: stay reachable until the next `start()` replaces it.
    run_id: Optional[str] = None
    strategy: Optional[str] = None
    contract: Optional[str] = None
    broker: Optional[str] = None
    live_symbol: Optional[str] = None
    resolution: Optional[str] = None
    poll_seconds: Optional[int] = None
    position: Optional[dict] = None
    session_pnl: Optional[str] = None
    trade_count_today: Optional[int] = None
    halted: bool = False
    halt_reason: Optional[str] = None
    last_bar_time: Optional[str] = None
    last_bar_close: Optional[str] = None
    last_feed_error: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": self.status, "run_id": self.run_id, "strategy": self.strategy, "contract": self.contract,
            "broker": self.broker, "live_symbol": self.live_symbol, "resolution": self.resolution,
            "poll_seconds": self.poll_seconds, "position": self.position, "session_pnl": self.session_pnl,
            "trade_count_today": self.trade_count_today, "halted": self.halted, "halt_reason": self.halt_reason,
            "last_bar_time": self.last_bar_time, "last_bar_close": self.last_bar_close,
            "last_feed_error": self.last_feed_error, "error_message": self.error_message,
            "started_at": self.started_at, "stopped_at": self.stopped_at, "warnings": self.warnings,
        }


class LiveSessionManager:
    """At most one paper-trading session at a time -- the same "one
    position at a time" constraint the risk manager already enforces
    within a session extends naturally to "one session" for a single-
    account research tool with no concept of multiple simultaneous
    strategies sharing one broker connection."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._snapshot = _Snapshot()

    # --- Public API ---

    def start(
        self, live_symbol: str, resolution: str, poll_seconds: int,
        config_path: Path = Path("config.yaml"),
    ) -> dict:
        with self._lock:
            if self._snapshot.status in ("starting", "running", "stopping"):
                raise ApiError(
                    f"A live session is already {self._snapshot.status}. Stop it before starting another."
                )
            # Claimed atomically with the check above -- everything below is
            # slow (settings load, a DB insert, strategy/engine construction,
            # a blocking websocket handshake) and used to run before the
            # status actually changed from its pre-call value, so two
            # concurrent start() calls could both pass the check before
            # either claimed the slot (the same shape found and fixed in
            # research_server/paper_trader.py and orchestrator.py -- see
            # KNOWN_ISSUES.md ISSUE-016). Restored to whatever it was before
            # this call in the except block below if anything fails.
            previous_status = self._snapshot.status
            self._snapshot.status = "starting"

        try:
            return self._start_locked(live_symbol, resolution, poll_seconds, config_path)
        except Exception:
            with self._lock:
                self._snapshot.status = previous_status
            raise

    def _start_locked(
        self, live_symbol: str, resolution: str, poll_seconds: int, config_path: Path,
    ) -> dict:
        """The rest of `start()`'s body -- split out only so `start()` itself
        can wrap it in one `try/except` that restores the pre-call status on
        any failure, without re-indenting this whole block."""
        settings = load_settings(config_path)

        # The one check that actually matters -- see module docstring.
        # Fails before build_engine (and therefore before any broker
        # object, paper or otherwise) is even constructed.
        if settings.broker.name != "paper":
            raise ApiError(
                f"Refusing to start a dashboard live session with broker.name={settings.broker.name!r}. "
                f"This endpoint only ever drives the paper broker -- set broker.name: paper in "
                f"{config_path}, or use `python -m futures_bot.cli --live` directly (which prints its "
                f"own explicit warning and requires the safety checklist in brokers/tradovate.py's "
                f"module docstring) for anything else."
            )

        api_key = os.environ.get("MASSIVE_API_KEY")
        if not api_key:
            raise ApiError(
                "MASSIVE_API_KEY environment variable is not set. The live feed's credential is read "
                "from the environment, never from config.yaml -- see docs/USER_MANUAL.md."
            )

        if settings.strategy_name not in StrategyRegistry.names():
            raise ApiError(f"Unknown strategy {settings.strategy_name!r} in {config_path}.")

        if settings.live_feed == "websocket" and resolution != "1min":
            raise ApiError(
                f"live_feed is 'websocket' but resolution is {resolution!r} -- the delayed WebSocket "
                f"feed only publishes minute aggregates. Start with resolution='1min', or set "
                f"live_feed back to 'rest' in config.yaml to use any other resolution."
            )

        from ..feeds.massive import MassiveBarFeed  # local: pulls in `requests`, only needed here

        # Opened before the engine/feed so a client can already see the
        # session in the research DB (status='running', no trades yet) the
        # moment it starts -- finalized (complete_run/fail_run) in _run's
        # finally. If engine/feed construction below raises, this row is
        # left stuck at 'running' forever, the same accepted-gap shape
        # RESEARCH_WORKSTATION.md documents for a job whose worker process
        # dies mid-run: a stuck-looking row, not silent data loss.
        run_id = uuid.uuid4().hex[:12]
        get_store().insert_run(
            run_id=run_id, kind="live", status="running",
            strategy=settings.strategy_name, contract=settings.contract,
            strategy_params=settings.strategy_params,
        )

        strategy = _build_strategy(settings)
        journal = LiveTradeJournal(
            settings.logging.directory, settings.logging.log_every_decision,
            run_id=run_id, contract=settings.contract, strategy=settings.strategy_name,
            strategy_params=settings.strategy_params,
        )
        engine = build_engine(settings, strategy, journal=journal)
        if settings.live_feed == "websocket":
            from ..feeds.massive_websocket import MassiveWebSocketBarFeed  # local: pulls in `websockets`

            feed = MassiveWebSocketBarFeed(symbol=live_symbol, api_key=api_key, resolution=resolution)
            feed.start()  # blocks until connected/authenticated/subscribed, or raises -- fail fast here,
            # not three silent poll_new_bars() calls into an already-"running" session.
        else:
            feed = MassiveBarFeed(symbol=live_symbol, api_key=api_key, resolution=resolution)

        stop_event = threading.Event()
        with self._lock:
            self._stop_event = stop_event
            self._snapshot = _Snapshot(
                status="starting", run_id=run_id, strategy=settings.strategy_name, contract=settings.contract,
                broker=settings.broker.name, live_symbol=live_symbol, resolution=resolution,
                poll_seconds=poll_seconds, warnings=settings.risk_warnings() + settings.strategy_warnings(),
                started_at=datetime.now(timezone.utc).isoformat(),
            )

        thread = threading.Thread(
            target=self._run, args=(engine, feed, settings, poll_seconds, stop_event, journal, run_id, live_symbol),
            daemon=True, name="futures-bot-live-session",
        )
        with self._lock:
            self._thread = thread
        thread.start()
        return self.status()

    def stop(self, timeout: float = 15.0) -> dict:
        with self._lock:
            if self._snapshot.status not in ("starting", "running"):
                raise ApiError("No live session is running.")
            self._snapshot.status = "stopping"
            stop_event = self._stop_event
            thread = self._thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=timeout)  # engine.stop() (flatten) runs inside the thread's finally block
        return self.status()

    def status(self) -> dict:
        with self._lock:
            return self._snapshot.as_dict()

    # --- Background thread body ---

    def _run(
        self, engine: TradingEngine, feed, settings: Settings, poll_seconds: int,
        stop_event: threading.Event, journal: LiveTradeJournal, run_id: str, live_symbol: str,
    ) -> None:
        error_message: Optional[str] = None
        # Phase 8A: every bar this session actually trades on is also
        # written to the shared market-data DB -- paper trading becomes
        # another contributor to (and beneficiary of) the same local
        # history backtests/research read from, not a dead end. One store
        # for the whole thread's lifetime is safe -- see `store.py`'s
        # docstring on why a `sqlite3.Connection` must stay single-thread,
        # which this already is (this method runs entirely on one
        # background thread).
        market_data_store = get_market_data_store()
        try:
            engine.start()
            self._patch(status="running")
            while not stop_event.is_set():
                try:
                    new_bars = feed.poll_new_bars()
                except RuntimeError as exc:
                    self._patch(last_feed_error=str(exc))
                    new_bars = []

                for bar in new_bars:
                    engine.on_bar(bar)
                    self._update_from_engine(engine, settings, bar)
                    try:
                        market_data_store.upsert_bars(
                            settings.contract, live_symbol, feed.resolution, "live_massive", [bar]
                        )
                    except Exception:  # noqa: BLE001 -- a storage hiccup must not stop the live session.
                        log.error("Failed to persist live bar to the market-data DB.", exc_info=True)

                stop_event.wait(poll_seconds)  # interruptible sleep -- stop() doesn't wait out a full poll
        except Exception as exc:  # noqa: BLE001 -- must never crash silently; surfaced via status()
            error_message = f"{type(exc).__name__}: {exc}"
            log.error("Live session failed: %s", exc, exc_info=True)
            self._patch(status="error", error_message=error_message)
        finally:
            try:
                engine.stop()
            except Exception as exc:  # noqa: BLE001
                log.error("Live session shutdown (flatten) failed: %s", exc, exc_info=True)
            # Only the WebSocket feed has a background connection/thread to
            # tear down -- MassiveBarFeed is stateless between polls and has
            # no `stop()` at all, deliberately not given one just to make
            # this call unconditional (see feeds/massive_websocket.py's
            # module docstring on why the two feeds aren't symmetric here).
            stop = getattr(feed, "stop", None)
            if stop is not None:
                try:
                    stop()
                except Exception as exc:  # noqa: BLE001
                    log.error("Live session feed shutdown failed: %s", exc, exc_info=True)
            with self._lock:
                if self._snapshot.status != "error":
                    self._snapshot.status = "stopped"
                self._snapshot.stopped_at = datetime.now(timezone.utc).isoformat()
            self._finalize_run(journal, settings, run_id, error_message)
            market_data_store.close()

    def _finalize_run(
        self, journal: LiveTradeJournal, settings: Settings, run_id: str, error_message: Optional[str],
    ) -> None:
        """Closes out this session's `runs` row -- `fail_run` if the loop
        raised, otherwise `complete_run` with the same aggregate figures
        (Sharpe, drawdown, caveats, ...) a backtest reports, computed via the
        same `BacktestMetrics` over the trades `LiveTradeJournal` already
        persisted individually as they closed. Never raises -- a failure
        here must not prevent the session from reaching a terminal status."""
        try:
            store = get_store()
            if error_message is not None:
                store.fail_run(run_id, error_message)
                return
            trades = journal.closed_trades
            metrics = BacktestMetrics(trades=trades, starting_equity=settings.broker.starting_cash)
            store.complete_run(
                run_id,
                starting_equity=metrics.starting_equity, trade_count=metrics.trade_count, net_pnl=metrics.net_pnl,
                profit_factor=metrics.profit_factor, win_rate=metrics.win_rate, expectancy=metrics.expectancy,
                sharpe_ratio=metrics.sharpe_ratio, sortino_ratio=metrics.sortino_ratio,
                max_drawdown=metrics.max_drawdown, max_drawdown_pct=metrics.max_drawdown_pct,
                caveats=metrics.caveats(),
                first_bar=trades[0].entry_time if trades else None,
                last_bar=trades[-1].exit_time if trades else None,
            )
        except Exception:  # noqa: BLE001 -- see docstring.
            log.error("Failed to finalize live session run %s in the research DB.", run_id, exc_info=True)

    def _update_from_engine(self, engine: TradingEngine, settings: Settings, bar) -> None:
        position = engine.broker.get_position()
        now = bar.timestamp
        state = engine.risk.store.session(session_date(now))
        self._patch(
            position=position_dict(position, bar.close, settings) if position else None,
            session_pnl=str(engine.risk.session_pnl(now)),
            trade_count_today=state.trade_count,
            halted=state.halted,
            halt_reason=state.halt_reason,
            last_bar_time=now.isoformat(),
            last_bar_close=str(bar.close),
        )

    def _patch(self, **updates) -> None:
        with self._lock:
            for key, value in updates.items():
                setattr(self._snapshot, key, value)


def position_dict(position: Position, current_price: Decimal, settings: Settings) -> dict:
    unrealized = position.unrealized_pnl(current_price, settings.contract_spec.point_value)
    return {
        "side": position.side.value, "quantity": position.quantity,
        "entry_price": str(position.entry_price),
        "stop_loss": str(position.stop_loss) if position.stop_loss is not None else None,
        "take_profit": str(position.take_profit) if position.take_profit is not None else None,
        "unrealized_pnl": str(unrealized),
    }


_manager: Optional[LiveSessionManager] = None
_manager_lock = threading.Lock()


def get_live_session_manager() -> LiveSessionManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = LiveSessionManager()
        return _manager


def reset_live_session_manager() -> None:
    """Test-only. Production code never calls this."""
    global _manager
    with _manager_lock:
        _manager = None
