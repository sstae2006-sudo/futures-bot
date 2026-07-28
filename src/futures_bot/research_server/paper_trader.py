"""Phase 8B: paper-trades several strategies concurrently, unattended.

One shared `MassiveBarFeed` poll per (product, resolution) -- fanned out to
one `TradingEngine` + `PaperBroker` + `RiskManager` **per enabled
strategy**, each with its own isolated risk state/kill-switch (its own
`state_file`), exactly as if each were its own `api.live_session
.LiveSessionManager` session, but costing one live-data API call per cycle
instead of N.

**Contract auto-detection/rollover reuses Phase 8A directly** --
`market_data.contracts_client.MassiveContractsClient.active_contract` picks
the front-month ticker at start; nobody ever types a specific expiry symbol
into config. A detected roll is logged through the same `MarketDataStore
.set_active_contract`/`contract_rolls` history the data scheduler already
writes to, keyed by the same product code -- one roll history per product,
not one per consumer.

**One source of truth for "did it roll", not two independent checks.**
`_check_for_roll` reads `MarketDataStore.get_active_contract` first -- the
data scheduler (`market_data.scheduler`, via `sync.sync_incremental`)
already calls the Contracts API on every one of its own cycles (as often
as every ~10s, versus a standalone daily check here) and writes the result
there, so as long as `research_server.data_sync_products` covers this
contract, that record is fresher than anything a separate check on this
class's own clock could produce -- and it's a local SQLite read, not an
API call, so there's no cost to checking it every poll cycle instead of
throttling it. An earlier version of this class called the Contracts API
itself, independently, once a day: harmless most of the time, but it meant
this class could keep trading an already-rolled (soon to be illiquid, then
expired) contract for up to 24 hours after the data scheduler had already
detected and recorded the real roll -- two sources of truth that could
disagree, silently. Only when the store has no record at all (the product
isn't in `data_sync_products`, or nothing has synced yet) does this class
fall back to asking the Contracts API directly, and only then is the
once-a-day throttle applied -- that path costs a real API call, so it's
worth being stingy about.

**Safety is structural, not a runtime toggle**: `research_server
.paper_strategies` has no field anywhere that names a broker, so there is
no way to reach this code path with anything other than the paper broker.
`start()` still checks `settings.broker.name` first anyway, before
constructing anything, matching `LiveSessionManager.start()`'s own guard
exactly -- belt and suspenders.

**Trade persistence** reuses `live_trade_journal.LiveTradeJournal` (a
top-level module precisely so both this and `api.live_session` can share
it without either depending on the other) -- one `runs` row
(`kind="live"`) per strategy, opened at `start()`, finalized at `stop()`,
with regime labels turned on (`label_regimes=True`) so
`research_server.insights.regime_drift_findings` has something to compare
against a strategy's historical backtest regime mix.

**Simplification, stated plainly**: `Settings.strategy_params` is a single
dict tied to `Settings.strategy_name` -- there's no per-entry override for
the other names in `paper_strategies`. Whichever configured strategy
matches `strategy_name` runs with `strategy_params`; every other entry
runs with its own class defaults (an empty params dict). A richer
per-strategy parameter config is a natural follow-up, not built here.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Union

import requests

from ..backtest.metrics import BacktestMetrics
from ..config import Settings
from ..contracts import get_contract, session_date
from ..engine import TradingEngine, build_engine
from ..journal import LOGGER_NAME
from ..live_trade_journal import LiveTradeJournal
from ..market_data.contracts_client import ContractsApiError, MassiveContractsClient
from ..market_data.store import MarketDataStore, get_market_data_store
from ..models import Bar, Position
from ..research.trade_store import TradeStore, default_db_path as research_db_path
from ..strategy.base import StrategyRegistry

if TYPE_CHECKING:
    # Postgres-only -- kept out of the real import above so a plain
    # SQLite setup never needs the `db` extra installed just to type-
    # check this module. `from __future__ import annotations` (already
    # in effect at the top of this file) means every annotation below is
    # a lazily-evaluated string, so this guard is all that's needed.
    from ..market_data.pg_store import PgMarketDataStore

    _AnyMarketDataStore = Union[MarketDataStore, PgMarketDataStore]

log = logging.getLogger(LOGGER_NAME)


def _build_deployed_signal_filter(strategy: str):
    """Phase 9: if `strategy` has a currently deployed model
    (`model_deployments`, latest row per strategy -- see
    `research.trade_store.TradeStore.fetch_current_deployment`), builds the
    exact same `signal_filter` the Backtest+AI comparison uses
    (`api.services._load_ml_signal_filter`) from it. Deliberately
    reimplemented here rather than imported from `api.services`: `research_server`
    is a dependency of `api`, never the reverse (see this module's own
    layering elsewhere), so this small function -- not a whole `api` import
    -- is the boundary. Returns `None` (no filter, current behavior)
    when nothing is deployed, the model isn't finished training, or loading
    the artifact fails for any reason -- a strategy must never stop trading
    live just because its optional AI layer had a problem."""
    store = TradeStore(research_db_path())
    try:
        deployment = store.fetch_current_deployment(strategy)
        if deployment is None or deployment.get("model_id") is None:
            return None
        model_row = store.fetch_model(deployment["model_id"])
    finally:
        store.close()

    if model_row is None or model_row["status"] != "finished":
        return None

    try:
        from ..models import Signal, SignalAction
        from ..research.ml.predict import load_model, score as ml_score

        loaded = load_model(model_row)
    except Exception:  # noqa: BLE001 -- a broken/missing artifact must not stop live trading
        log.error("Could not load deployed model %s for %s -- trading without it.", deployment["model_id"], strategy, exc_info=True)
        return None

    threshold = 0.5

    def signal_filter(signal: "Signal") -> "Signal":
        probability = ml_score(loaded, dict(signal.metadata))
        if probability < threshold:
            return Signal(
                action=SignalAction.HOLD,
                reason=f"AI filtered: win probability {probability:.2f} < {threshold:.2f} threshold.",
            )
        return signal

    return signal_filter

#: Throttle for the *fallback* direct-Contracts-API roll check only (used
#: when `MarketDataStore` has no active-contract record for this product
#: yet) -- daily is plenty; rollovers are a quarterly event, not an hourly
#: one. The primary check, against the store, runs every poll cycle
#: unthrottled since it's just a local read. See module docstring.
_ROLL_CHECK_INTERVAL_SECONDS = 24 * 60 * 60


class PaperTraderError(RuntimeError):
    """A client-facing, well-understood failure (unknown strategy, wrong
    broker, missing API key) -- `api/research_server_service.py` maps this
    to an HTTP 400, the same role `market_data.sync.SyncError` plays for
    the data pipeline."""


@dataclass
class _StrategySnapshot:
    status: str = "starting"  # 'starting' | 'running' | 'stopping' | 'stopped' | 'error'
    run_id: Optional[str] = None
    strategy: str = ""
    position: Optional[dict] = None
    session_pnl: Optional[str] = None
    trade_count_today: Optional[int] = None
    halted: bool = False
    halt_reason: Optional[str] = None
    error_message: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "status": self.status, "run_id": self.run_id, "strategy": self.strategy,
            "position": self.position, "session_pnl": self.session_pnl,
            "trade_count_today": self.trade_count_today, "halted": self.halted,
            "halt_reason": self.halt_reason, "error_message": self.error_message,
        }


def _build_strategy_instance(name: str, contract_spec, params: dict):
    if name not in StrategyRegistry.names():
        known = ", ".join(StrategyRegistry.names()) or "(none registered)"
        raise PaperTraderError(f"Unknown strategy {name!r} in research_server.paper_strategies. Known: {known}")
    try:
        return StrategyRegistry.get(name)(contract_spec, **params)
    except (TypeError, ValueError) as exc:
        raise PaperTraderError(f"Invalid strategy_params for {name!r}: {exc}") from exc


def _position_dict(position: Position, current_price: Decimal, point_value: Decimal) -> dict:
    unrealized = position.unrealized_pnl(current_price, point_value)
    return {
        "side": position.side.value, "quantity": position.quantity,
        "entry_price": str(position.entry_price),
        "stop_loss": str(position.stop_loss) if position.stop_loss is not None else None,
        "take_profit": str(position.take_profit) if position.take_profit is not None else None,
        "unrealized_pnl": str(unrealized),
    }


@dataclass
class _StrategyRuntime:
    """Everything the poll loop needs for one strategy -- built once at
    `start()`, read/mutated only from the background thread."""

    name: str
    engine: TradingEngine
    journal: LiveTradeJournal
    run_id: str
    snapshot: _StrategySnapshot = field(default_factory=_StrategySnapshot)


class AutonomousPaperTrader:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._running = False
        self._live_symbol: Optional[str] = None
        self._last_feed_error: Optional[str] = None
        self._runtimes: dict[str, _StrategyRuntime] = {}

    # --- Public API ---

    def start(self, settings: Settings, api_key: str, session: Optional[requests.Session] = None) -> dict:
        with self._lock:
            if self._running:
                raise PaperTraderError("The autonomous paper trader is already running.")

        # Structural guard -- see module docstring. Fails before anything
        # broker-adjacent is constructed, mirroring LiveSessionManager.start().
        if settings.broker.name != "paper":
            raise PaperTraderError(
                f"Refusing to autonomously paper trade with broker.name={settings.broker.name!r}. "
                f"research_server only ever drives the paper broker."
            )
        if not settings.research_server.paper_strategies:
            log.info("research_server.paper_strategies is empty -- nothing to autonomously paper trade.")
            return self.status()

        session = session or requests.Session()
        contracts_client = MassiveContractsClient(api_key, session=session)
        try:
            active = contracts_client.active_contract(settings.contract)
        except ContractsApiError as exc:
            raise PaperTraderError(f"Could not detect the active contract for {settings.contract!r}: {exc}") from exc

        resolution = settings.research_server.resolution
        if settings.live_feed == "websocket":
            # `resolution == "1min"` is already guaranteed here by
            # `Settings._websocket_feed_is_minute_only` at config-load time
            # -- no runtime check needed, unlike `LiveSessionManager`, whose
            # resolution is a per-request API parameter Settings can't see.
            from ..feeds.massive_websocket import MassiveWebSocketBarFeed  # local: pulls in `websockets`

            feed = MassiveWebSocketBarFeed(symbol=active.ticker, api_key=api_key, resolution=resolution)
            feed.start()  # blocks until connected/authenticated/subscribed, or raises -- fail fast
        else:
            from ..feeds.massive import MassiveBarFeed  # local: pulls in `requests`, only needed here

            feed = MassiveBarFeed(symbol=active.ticker, api_key=api_key, resolution=resolution)

        runtimes: dict[str, _StrategyRuntime] = {}
        contract_spec = get_contract(settings.contract)
        for name in settings.research_server.paper_strategies:
            params = settings.strategy_params if name == settings.strategy_name else {}
            strategy = _build_strategy_instance(name, contract_spec, params)

            run_id = uuid.uuid4().hex[:12]
            store = TradeStore(research_db_path())
            try:
                # A prior session for this strategy that never got a clean
                # stop (a process crash/kill, not a normal "Stop" click --
                # that path already flattens and completes its run row
                # first via `stop()`) leaves a stale status='running' row
                # here forever, with nothing anywhere surfacing that
                # anything was ever wrong. Any position open at that point
                # was never recorded as a closed trade and is NOT recovered
                # by this restart -- the broker/account (paper or real)
                # could be out of sync with what this bot now believes.
                # Nothing here can recover that position; this only makes
                # the gap visible instead of silent.
                orphaned = [
                    r for r in store.fetch_runs(strategy=name, kind="live", limit=5) if r["status"] == "running"
                ]
                for orphan in orphaned:
                    log.warning(
                        "Prior live run %s for strategy %r never shut down cleanly (process likely "
                        "crashed or was killed) -- marking it failed. If a position was open at that "
                        "point, it was NOT recovered and this bot has no record of it.",
                        orphan["id"], name,
                    )
                    store.fail_run(
                        orphan["id"],
                        "Orphaned: process did not shut down cleanly (no matching clean stop recorded).",
                    )

                store.insert_run(
                    run_id=run_id, kind="live", status="running",
                    strategy=name, contract=settings.contract, strategy_params=params,
                )
            finally:
                store.close()

            journal = LiveTradeJournal(
                settings.logging.directory, settings.logging.log_every_decision,
                run_id=run_id, contract=settings.contract, strategy=name,
                strategy_params=params, label_regimes=True,
            )
            # Per-strategy state file: isolated risk/session/kill-switch
            # state, so one strategy halting never touches another's.
            per_strategy_settings = settings.model_copy(update={
                "state_file": settings.state_file.parent / f"research_server_{name}{settings.state_file.suffix or '.json'}"
            })
            signal_filter = _build_deployed_signal_filter(name)
            engine = build_engine(per_strategy_settings, strategy, journal=journal, signal_filter=signal_filter)
            journal.bars_provider = lambda engine=engine: engine.bars

            runtimes[name] = _StrategyRuntime(
                name=name, engine=engine, journal=journal, run_id=run_id,
                snapshot=_StrategySnapshot(run_id=run_id, strategy=name),
            )

        stop_event = threading.Event()
        with self._lock:
            self._runtimes = runtimes
            self._live_symbol = active.ticker
            self._last_feed_error = None
            self._stop_event = stop_event
            self._running = True

        thread = threading.Thread(
            target=self._run, args=(feed, settings, contracts_client, stop_event),
            daemon=True, name="futures-bot-research-server-paper-trader",
        )
        with self._lock:
            self._thread = thread
        thread.start()
        return self.status()

    def stop(self, timeout: float = 15.0) -> dict:
        with self._lock:
            not_running = not self._running
            stop_event = self._stop_event
            thread = self._thread
        if not_running:
            return self.status()  # outside the lock -- status() acquires it itself
        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self._running = False
        return self.status()

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "live_symbol": self._live_symbol,
                "last_feed_error": self._last_feed_error,
                "strategies": {name: rt.snapshot.as_dict() for name, rt in self._runtimes.items()},
            }

    # --- Background thread body ---

    def _run(
        self, feed, settings: Settings, contracts_client: MassiveContractsClient, stop_event: threading.Event,
    ) -> None:
        for rt in self._runtimes.values():
            try:
                rt.engine.start()
                rt.snapshot.status = "running"
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to start engine for %s: %s", rt.name, exc, exc_info=True)
                rt.snapshot.status = "error"
                rt.snapshot.error_message = f"{type(exc).__name__}: {exc}"

        market_data_store = get_market_data_store()
        # Only meaningful for the fallback branch inside _check_for_roll --
        # see that method and the module docstring.
        last_fallback_roll_check = datetime.min.replace(tzinfo=timezone.utc)
        try:
            while not stop_event.is_set():
                now = datetime.now(timezone.utc)
                last_fallback_roll_check = self._check_for_roll(
                    feed, settings, contracts_client, market_data_store, last_fallback_roll_check, now,
                )

                try:
                    new_bars = feed.poll_new_bars()
                    with self._lock:
                        self._last_feed_error = None
                except RuntimeError as exc:
                    with self._lock:
                        self._last_feed_error = str(exc)
                    new_bars = []

                if new_bars:
                    try:
                        market_data_store.upsert_bars(
                            settings.contract, feed.symbol, feed.resolution, "autonomous_paper", new_bars
                        )
                    except Exception:  # noqa: BLE001 -- a storage hiccup must not stop paper trading.
                        log.error("Failed to persist autonomous paper-trader bars.", exc_info=True)

                for bar in new_bars:
                    for rt in self._runtimes.values():
                        if rt.snapshot.status == "error":
                            continue
                        try:
                            rt.engine.on_bar(bar)
                            self._update_snapshot(rt, settings, bar)
                        except Exception as exc:  # noqa: BLE001 -- one strategy's bug must not stop the others.
                            log.error("Strategy %s failed on a live bar: %s", rt.name, exc, exc_info=True)
                            rt.snapshot.status = "error"
                            rt.snapshot.error_message = f"{type(exc).__name__}: {exc}"

                stop_event.wait(settings.research_server.poll_seconds)
        finally:
            for rt in self._runtimes.values():
                try:
                    rt.engine.stop()
                except Exception as exc:  # noqa: BLE001
                    log.error("Shutdown (flatten) failed for %s: %s", rt.name, exc, exc_info=True)
                if rt.snapshot.status != "error":
                    rt.snapshot.status = "stopped"
                self._finalize_run(rt, settings)
            market_data_store.close()

    def _check_for_roll(
        self, feed, settings: Settings, contracts_client: MassiveContractsClient, market_data_store: "_AnyMarketDataStore",
        last_fallback_roll_check: datetime, now: datetime,
    ) -> datetime:
        """Returns the (possibly updated) ``last_fallback_roll_check`` the
        caller should pass back in next cycle. See module docstring for why
        the store read below is preferred over asking the Contracts API
        directly."""
        stored_ticker = market_data_store.get_active_contract(settings.contract)
        if stored_ticker is not None:
            if stored_ticker != feed.symbol:
                self._apply_roll(feed, settings.contract, stored_ticker)
            return last_fallback_roll_check

        if (now - last_fallback_roll_check).total_seconds() < _ROLL_CHECK_INTERVAL_SECONDS:
            return last_fallback_roll_check
        try:
            active = contracts_client.active_contract(settings.contract)
        except ContractsApiError as exc:
            log.error("Roll check failed: %s", exc, exc_info=True)
            return now
        market_data_store.set_active_contract(settings.contract, active.ticker)
        if active.ticker != feed.symbol:
            self._apply_roll(feed, settings.contract, active.ticker)
        return now

    def _apply_roll(self, feed, product_code: str, new_ticker: str) -> None:
        log.warning("research_server: %s rolled from %s to %s", product_code, feed.symbol, new_ticker)
        feed.symbol = new_ticker
        with self._lock:
            self._live_symbol = new_ticker

    def _update_snapshot(self, rt: _StrategyRuntime, settings: Settings, bar: Bar) -> None:
        position = rt.engine.broker.get_position()
        now = bar.timestamp
        state = rt.engine.risk.store.session(session_date(now))
        rt.snapshot.position = (
            _position_dict(position, bar.close, settings.contract_spec.point_value) if position else None
        )
        rt.snapshot.session_pnl = str(rt.engine.risk.session_pnl(now))
        rt.snapshot.trade_count_today = state.trade_count
        rt.snapshot.halted = state.halted
        rt.snapshot.halt_reason = state.halt_reason

    def _finalize_run(self, rt: _StrategyRuntime, settings: Settings) -> None:
        store = TradeStore(research_db_path())
        try:
            if rt.snapshot.status == "error" and rt.snapshot.error_message:
                store.fail_run(rt.run_id, rt.snapshot.error_message)
                return
            trades = rt.journal.closed_trades
            metrics = BacktestMetrics(trades=trades, starting_equity=settings.broker.starting_cash)
            store.complete_run(
                rt.run_id,
                starting_equity=metrics.starting_equity, trade_count=metrics.trade_count, net_pnl=metrics.net_pnl,
                profit_factor=metrics.profit_factor, win_rate=metrics.win_rate, expectancy=metrics.expectancy,
                sharpe_ratio=metrics.sharpe_ratio, sortino_ratio=metrics.sortino_ratio,
                max_drawdown=metrics.max_drawdown, max_drawdown_pct=metrics.max_drawdown_pct,
                caveats=metrics.caveats(),
                first_bar=trades[0].entry_time if trades else None,
                last_bar=trades[-1].exit_time if trades else None,
            )
        except Exception:  # noqa: BLE001 -- a failure to finalize must not prevent shutdown.
            log.error("Failed to finalize research-server run %s for %s.", rt.run_id, rt.name, exc_info=True)
        finally:
            store.close()


_trader: Optional[AutonomousPaperTrader] = None
_trader_lock = threading.Lock()


def get_paper_trader() -> AutonomousPaperTrader:
    global _trader
    with _trader_lock:
        if _trader is None:
            _trader = AutonomousPaperTrader()
        return _trader


def reset_paper_trader() -> None:
    """Test-only. Production code never calls this."""
    global _trader
    with _trader_lock:
        _trader = None
