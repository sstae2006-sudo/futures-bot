"""The trading engine.

Processes one bar at a time in a fixed order, and the order is the design:

1. Feed the bar to the broker, so any resting stop or target resolves *first*.
2. Check whether risk demands a flatten — force-flat, halt.
3. Ask the strategy what it wants.
4. Act on it, but only after the risk manager agrees.

Protective orders are settled before anything else because they represent
decisions already made and resting in the market. A strategy signal cannot
overtake a stop that has already been hit; treating them in the other order
would let a bot "cancel" a loss that has, in reality, already happened.

Step 4 is the important boundary. The strategy never reaches the broker
directly — every entry passes :meth:`RiskManager.can_enter` first, and a
blocked signal is journalled rather than silently dropped.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from decimal import Decimal
from typing import Callable, Deque, Optional

from .brokers.base import Broker, BrokerError
from .brokers.paper import PaperBroker
from .config import Settings
from .contracts import session_date
from .journal import DecisionJournal, LOGGER_NAME
from .models import Bar, InvalidSignalError, Position, Side, Signal, SignalAction, Trade
from .risk.manager import RiskManager
from .state import StateStore
from .strategy.base import Strategy

log = logging.getLogger(LOGGER_NAME)

#: Floor for `TradingEngine.bars`' retention window (see `max_bars_retained`
#: below) -- generous headroom over every bundled strategy's own
#: `warmup_bars` (the largest, `trend_pullback`'s default, is ~201), so nothing
#: bundled today is ever affected; it only bounds what would otherwise be
#: truly unlimited growth for a long-running live/paper session.
_MIN_BARS_RETAINED = 2000


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        strategy: Strategy,
        broker: Broker,
        risk: RiskManager,
        journal: DecisionJournal,
        max_bars_retained: Optional[int] = None,
        signal_filter: Optional[Callable[[Signal], Signal]] = None,
    ) -> None:
        self.settings = settings
        self.strategy = strategy
        self.broker = broker
        self.risk = risk
        self.journal = journal
        self.contract = settings.contract_spec
        #: Phase 9: an optional post-strategy, pre-risk hook over entry
        #: signals only -- `None` (the default) is a no-op, so every caller
        #: that doesn't pass this is completely unaffected. Used by the
        #: Backtest+AI comparison and by a strategy's deployed model in
        #: live/paper trading (`research_server/paper_trader.py`) to convert
        #: a low-predicted-win-probability entry into a HOLD before it ever
        #: reaches the risk manager or broker -- one filter implementation,
        #: two call sites, never a second scoring path.
        self.signal_filter = signal_filter
        #: A bounded deque, not a plain list: `on_bar` is called once per
        #: closed bar for the life of the engine, and a live/paper session is
        #: now expected to run indefinitely (`research_server`) rather than
        #: for one backtest's worth of bars -- an unbounded list here is a
        #: slow, guaranteed memory leak. The bound defaults to comfortably
        #: more than any bundled strategy's `warmup_bars` needs (4x, floored
        #: at `_MIN_BARS_RETAINED`); pass `max_bars_retained` explicitly for a
        #: custom strategy with an unusually long lookback. A backtest never
        #: reads `self.bars` back out (it keeps its own separate, full bars
        #: list -- see `backtest/runner.py`), so this bound never affects a
        #: backtest's correctness, only a continuously-running engine's memory.
        retained = max_bars_retained or max(getattr(strategy, "warmup_bars", 0) * 4, _MIN_BARS_RETAINED)
        self.bars: Deque[Bar] = deque(maxlen=retained)
        #: Bars on which the strategy failed to produce a valid Signal --
        #: either it raised, or it returned something that isn't one. Each
        #: is suppressed into a safe HOLD for that bar (see `_safe_signal`)
        #: rather than crashing the run or letting a malformed Signal reach
        #: the risk manager / broker, but the count is carried through to
        #: `BacktestMetrics` and the report's caveats so it can never be
        #: mistaken for a clean run.
        self.strategy_error_count = 0

    # --- Lifecycle ---

    def start(self) -> None:
        self.broker.connect()
        self.strategy.on_start()
        log.info(
            "Engine started | contract=%s mode=%s strategy=%s",
            self.contract.symbol,
            self.settings.mode,
            self.strategy.name,
        )
        for warning in self.settings.risk_warnings():
            log.warning("RISK: %s", warning)

    def stop(self, now: Optional[datetime] = None) -> None:
        moment = now or datetime.now().astimezone()
        position = self.broker.get_position()
        if position is not None:
            log.warning("Stopping with an open position; flattening first.")
            self._flatten(moment, "engine shutdown")
        self.strategy.on_stop()
        self.broker.disconnect()
        log.info("Engine stopped | %s", self.risk.describe(moment))

    # --- Main loop ---

    def on_bar(self, bar: Bar) -> None:
        """Process one closed bar."""
        self.bars.append(bar)
        now = bar.timestamp

        # 1. Resolve resting protective orders before anything else.
        if isinstance(self.broker, PaperBroker):
            closed = self.broker.on_bar(bar, now)
            if closed is not None:
                self._record_trade(now, closed)
        else:
            # A live adapter's stop/target can fill at the exchange between
            # calls, with nothing else here to notice -- see
            # `Broker.poll_closed_trade`'s docstring for why this can't be
            # skipped the way it can for the paper broker.
            closed = self.broker.poll_closed_trade(now)
            if closed is not None:
                self._record_trade(now, closed)

        position = self.broker.get_position()

        # 2. Forced exits outrank everything the strategy might want.
        forced = self.risk.must_flatten(now, position)
        if forced.allowed:
            self._flatten(now, forced.reason)
            return

        # 3. Ask the strategy.
        signal = self._safe_signal(now, position)

        # 3b. Phase 9: an optional AI filter over entry signals only -- a
        # HOLD/EXIT is never touched, so a strategy with no deployed/selected
        # model behaves exactly as it always has.
        if self.signal_filter is not None and signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
            signal = self.signal_filter(signal)

        # 4. Act, subject to risk.
        self._handle_signal(now, signal, position, bar.close)

    def _safe_signal(self, now: datetime, position: Optional[Position]) -> Signal:
        """Call the strategy and guarantee a valid Signal comes back.

        A strategy is untrusted code from the engine's point of view: it can
        raise (a bug), or return something that isn't a Signal at all (a
        missing return statement, wrong type). Either failure is treated the
        same way -- logged loudly, journalled as a distinct event so it shows
        up in `decisions.jsonl` and is never mistaken for an ordinary HOLD,
        counted (see `strategy_error_count`), and suppressed into a safe HOLD
        for this bar only. One bad bar must not crash a multi-month backtest
        or leave a live session unmanaged; but it also must never look like
        a clean run -- the caveats in `BacktestMetrics` surface this count so
        the result cannot be trusted at face value if it happened.
        """
        try:
            signal = self.strategy.on_bar(self.bars, position)
            if not isinstance(signal, Signal):
                raise InvalidSignalError(
                    f"{self.strategy.name}.on_bar returned {signal!r} "
                    f"({type(signal).__name__}), not a Signal."
                )
            return signal
        except Exception as exc:  # noqa: BLE001 - any strategy failure must be contained here.
            self.strategy_error_count += 1
            message = f"{type(exc).__name__}: {exc}"
            log.error(
                "Strategy %s failed on bar %s: %s", self.strategy.name, now, message, exc_info=True
            )
            self.journal.event(now, "strategy_error", message)
            return Signal(
                action=SignalAction.HOLD,
                reason=f"[safety] {self.strategy.name} did not return a valid Signal: {message}",
            )

    def _handle_signal(
        self,
        now: datetime,
        signal: Signal,
        position: Optional[Position],
        price: Decimal,
    ) -> None:
        session_pnl = self.risk.session_pnl(now)

        if signal.action is SignalAction.HOLD:
            # A HOLD carrying stop_loss while a position is open is a request
            # to move the resting stop (trailing / breakeven), not a new
            # entry -- HOLD never had any use for that field before, so this
            # is purely additive: a strategy that never sets it behaves
            # exactly as before.
            if position is not None and signal.stop_loss is not None:
                try:
                    moved = self.broker.modify_stop_loss(signal.stop_loss)
                    if moved:
                        self.journal.decision(now, signal, acted=True, price=price, session_pnl=session_pnl)
                        log.info("STOP MOVED to %s | %s", signal.stop_loss, signal.reason)
                    else:
                        # Rounds to the same tick as the current stop -- a
                        # normal, frequent no-op, not worth its own log line.
                        self.journal.decision(now, signal, acted=False, price=price, session_pnl=session_pnl)
                except BrokerError as exc:
                    self.journal.decision(
                        now, signal, acted=False,
                        block_reason=f"Stop move rejected: {exc}",
                        price=price, session_pnl=session_pnl,
                    )
                    log.warning("Stop move rejected: %s", exc)
                return

            self.journal.decision(now, signal, acted=False, price=price, session_pnl=session_pnl)
            log.debug("HOLD: %s", signal.reason)
            return

        if signal.action is SignalAction.EXIT:
            if position is None:
                self.journal.decision(
                    now, signal, acted=False,
                    block_reason="No open position to exit.",
                    price=price, session_pnl=session_pnl,
                )
                return
            self.journal.decision(now, signal, acted=True, price=price, session_pnl=session_pnl)
            self._flatten(now, f"strategy exit: {signal.reason}")
            return

        # Entry signals.
        decision = self.risk.can_enter(now, position)
        if not decision.allowed:
            # The blocked-signal record is the whole point of the journal: it
            # answers "why wasn't that trade taken?" during review.
            self.journal.decision(
                now, signal, acted=False,
                block_reason=decision.reason,
                price=price, session_pnl=session_pnl,
            )
            log.info("BLOCKED %s: %s", signal.action.value, decision.reason)
            return

        side = Side.LONG if signal.action is SignalAction.ENTER_LONG else Side.SHORT
        stop, target = self._bracket_prices(side, price, signal)

        try:
            self.broker.submit_bracket(
                side=side,
                quantity=self.settings.risk.contracts_per_trade,
                stop_loss=stop,
                take_profit=target,
                now=now,
            )
        except BrokerError as exc:
            self.journal.decision(
                now, signal, acted=False,
                block_reason=f"Broker rejected the order: {exc}",
                price=price, session_pnl=session_pnl,
            )
            log.error("Entry rejected: %s", exc)
            return

        self.journal.decision(now, signal, acted=True, price=price, session_pnl=session_pnl)
        log.info(
            "ENTER %s @ ~%s | stop %s target %s | %s",
            side.value.upper(), price, stop, target, signal.reason,
        )

    def _bracket_prices(
        self, side: Side, price: Decimal, signal: Signal
    ) -> tuple[Decimal, Decimal]:
        """Stop and target for a new position.

        A strategy may supply explicit levels; otherwise they come from
        settings. Either way they are snapped to valid ticks, because brokers
        reject off-tick prices and a rejection here means an unprotected fill.
        """
        stop_pts = self.settings.risk.stop_loss_points
        target_pts = self.settings.risk.take_profit_points

        stop = signal.stop_loss if signal.stop_loss is not None else (
            price - stop_pts if side is Side.LONG else price + stop_pts
        )
        target = signal.take_profit if signal.take_profit is not None else (
            price + target_pts if side is Side.LONG else price - target_pts
        )
        return self.contract.round_to_tick(stop), self.contract.round_to_tick(target)

    def _flatten(self, now: datetime, reason: str) -> None:
        try:
            self.broker.flatten(now, reason)
        except BrokerError as exc:
            log.error("Flatten failed: %s", exc)
            self.journal.event(now, "flatten_failed", str(exc))
            return

        if isinstance(self.broker, PaperBroker) and self.broker.trades:
            last = self.broker.trades[-1]
            if last.exit_time == now:
                self._record_trade(now, last, already_logged=True)
        else:
            # `broker.flatten()` returns a Fill, not a Trade -- for a live
            # adapter, `poll_closed_trade` (the same reconciliation path used
            # in `on_bar` for a fill that happens between calls) is what
            # turns "the broker says I'm flat now" into a recorded Trade.
            # Skipping this would mean a risk-forced or strategy-requested
            # flatten never reaches the kill switch or the journal on a live
            # adapter -- the same gap `poll_closed_trade`'s docstring warns
            # about, just triggered from this call site instead of on_bar's.
            closed = self.broker.poll_closed_trade(now)
            if closed is not None:
                self._record_trade(now, closed, already_logged=True)

    def _record_trade(self, now: datetime, trade: Trade, already_logged: bool = False) -> None:
        self.risk.record_trade(now, trade)
        session_pnl = self.risk.session_pnl(now)
        self.journal.trade(trade, session_pnl)

        log.info(
            "TRADE %s %s @ %s -> %s | net $%s | %s | session $%s",
            trade.side.value.upper(), trade.quantity,
            trade.entry_price, trade.exit_price,
            trade.net_pnl, trade.exit_reason, session_pnl,
        )

        if self.risk.is_halted(now):
            state = self.risk.store.session(session_date(now))
            log.warning("KILL SWITCH: %s", state.halt_reason)
            self.journal.event(now, "halt", state.halt_reason or "halted")


def build_engine(
    settings: Settings,
    strategy: Strategy,
    broker: Optional[Broker] = None,
    journal: Optional[DecisionJournal] = None,
    signal_filter: Optional[Callable[[Signal], Signal]] = None,
) -> TradingEngine:
    """Assemble an engine from settings.

    ``journal``, like ``broker``, defaults to ``None`` and is only used by
    callers that need something other than a plain `DecisionJournal` -- e.g.
    `api.services._run_with_journal`'s `CountingJournal` (captures entry
    reason/metadata for `research.features.build_trade_records`) or
    `api.live_session`'s subclass of it (does the same for a live paper
    session, so its trades can be persisted the same way a backtest's are).

    ``signal_filter`` defaults to ``None`` (no behavior change for any
    existing caller) -- `research_server/paper_trader.py` passes one built
    from a strategy's currently deployed model, when it has one.
    """
    store = StateStore(settings.state_file)
    risk = RiskManager(settings, store)
    if journal is None:
        journal = DecisionJournal(settings.logging.directory, settings.logging.log_every_decision)

    if broker is None:
        if settings.broker.name == "paper":
            broker = PaperBroker(
                contract=settings.contract_spec,
                starting_cash=settings.broker.starting_cash,
                slippage_ticks=settings.broker.slippage_ticks,
                commission_per_side=settings.broker.commission_per_side,
            )
        elif settings.broker.name == "tradovate":
            # Imported here rather than at module level: it pulls in
            # `requests`, and every other code path through this module
            # (backtests, --demo, --optimize, --compare) has no reason to
            # need it. `tradovate_symbol` is validated present by
            # `BrokerSettings`; credentials come from the environment --
            # see brokers/tradovate.py's module docstring for the required
            # variables and the safety checklist before first use.
            from .brokers.tradovate import TradovateBroker

            broker = TradovateBroker(
                contract=settings.contract_spec,
                symbol=settings.broker.tradovate_symbol,
                commission_per_side=settings.broker.commission_per_side,
            )
        else:
            raise NotImplementedError(
                f"Broker adapter {settings.broker.name!r} is not implemented yet. "
                f"Implement futures_bot.brokers.base.Broker and pass it in."
            )

    return TradingEngine(settings, strategy, broker, risk, journal, signal_filter=signal_filter)
