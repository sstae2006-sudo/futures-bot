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

**Market Context Engine integration (``ContextMode``):** a 0'th step now
sits before all four above -- see ``ContextMode`` and ``on_bar``'s own
docstring. ``ContextMode.OFF`` (the default for every existing caller) is a
complete no-op: no ``ContextEngine`` is even asked to build anything, so
behavior, timing, and every trade this engine produces are bit-for-bit
identical to before this integration existed. See docs/ARCHITECTURE.md's
"Market Context Engine" section for the full integration story and
performance notes.
"""

from __future__ import annotations

import dataclasses
import logging
from collections import deque
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Callable, Deque, Optional

from .brokers.base import Broker, BrokerError
from .brokers.paper import PaperBroker
from .config import Settings
from .context import ContextEngine, MarketContext
from .contracts import session_date
from .journal import DecisionJournal, LOGGER_NAME
from .models import Bar, InvalidSignalError, Position, Side, Signal, SignalAction, Trade
from .risk.manager import RiskManager
from .state import StateStore
from .strategy.base import Strategy

log = logging.getLogger(LOGGER_NAME)


class ContextMode(str, Enum):
    """How much the Market Context Engine participates in one
    ``TradingEngine`` run. See each member's own docstring; the three-way
    split (rather than a plain on/off flag) exists specifically so
    "context is generated and recorded" and "context can influence a
    decision" are two separate, independently verifiable guarantees --
    not the same switch.
    """

    #: Current behavior. No ``ContextEngine`` is ever asked to build
    #: anything; ``Strategy.context`` is never set; ``Trade.entry_context``
    #: is never populated. The default for every existing caller -- must
    #: reproduce historical results exactly, and does, because nothing
    #: about this integration executes at all in this mode.
    OFF = "off"
    #: A ``MarketContext`` is generated for every processed bar and
    #: attached to every completed trade's ``entry_context`` -- but
    #: ``Strategy.context`` is never set, so no strategy can read it, by
    #: construction. Trading decisions are therefore provably identical
    #: to ``OFF`` -- not merely "should be unaffected", but literally
    #: unable to differ, since the strategy never sees the object at all.
    OBSERVE = "observe"
    #: Same generation/attachment as ``OBSERVE``, **plus**
    #: ``Strategy.context`` is set -- but only for a strategy whose own
    #: ``uses_context`` is ``True``. Every bundled strategy defaults to
    #: ``False``, so running an existing, unmodified strategy in
    #: ``ENABLED`` mode behaves identically to ``OFF``/``OBSERVE`` too;
    #: only a strategy that has explicitly opted in can ever see or act
    #: on ``self.context``.
    ENABLED = "enabled"

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
        context_mode: ContextMode = ContextMode.OFF,
        context_engine: Optional[ContextEngine] = None,
    ) -> None:
        self.settings = settings
        self.strategy = strategy
        self.broker = broker
        self.risk = risk
        self.journal = journal
        self.contract = settings.contract_spec
        #: See ``ContextMode``. Defaults to ``OFF`` -- every existing caller
        #: (nobody passes this yet) gets exactly the pre-integration engine,
        #: with zero added overhead: `on_bar` skips the `ContextEngine` call
        #: entirely when this is `OFF`, rather than building and discarding
        #: a context nobody asked for.
        self.context_mode = context_mode
        #: Auto-constructed from `settings` when not given explicitly, using
        #: `research_server.resolution` as the timeframe label (the closest
        #: existing "what resolution does this bot run at" setting -- named
        #: for that one subsystem historically, but descriptive of the whole
        #: bot's bar resolution in practice; a purely cosmetic label on
        #: `MarketContext.timeframe`, never used in any classification math).
        #: Irrelevant when `context_mode` is `OFF` -- never called in that
        #: mode, so its construction cost (trivial -- no I/O, no bars
        #: touched) is the only cost paid regardless of mode.
        self.context_engine = context_engine or ContextEngine(
            symbol=self.contract.symbol, timeframe=settings.research_server.resolution,
        )
        #: The `MarketContext` captured at the most recent successful entry,
        #: held here (not on `Position`, which the broker owns and knows
        #: nothing about `context/`) until the position closes and
        #: `_record_trade` attaches it to the resulting `Trade`. `None`
        #: whenever `context_mode` is `OFF`, between trades, or before the
        #: first entry.
        self._pending_entry_context: Optional[MarketContext] = None
        #: Defensive reset, not just at construction: if `strategy` is a
        #: reused instance (e.g. an `ENABLED` run followed by an `OFF` run on
        #: the same object), any `self.context` left over from a *previous*
        #: engine's run must not leak into this one, even before the first
        #: `on_bar` call. `on_bar` itself re-asserts this every bar (see its
        #: step 0) -- the two together mean `self.strategy.context` is always
        #: exactly what *this* engine, in *this* mode, set it to, never a
        #: stale value from anywhere else. See
        #: docs/PLATFORM_VERIFICATION_PHASE1.md's "stale Strategy.context"
        #: finding and docs/PLATFORM_VERIFICATION_PHASE2.md.
        self.strategy.context = None
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

        # 0. Market Context Engine (ContextMode.OFF is a complete no-op --
        # see the class docstring and `_build_market_context`). Built before
        # anything else touches this bar so every step below sees the exact
        # same reading, and the strategy step can expose it via
        # `Strategy.context` (ENABLED mode only) with no extra work.
        #
        # `self.strategy.context` is set unconditionally, every bar, to
        # either the real context or `None` -- never left untouched -- so a
        # reused `Strategy` instance can never show a value from a different
        # engine/run/mode (see `__init__`'s matching reset and
        # docs/PLATFORM_VERIFICATION_PHASE1.md's "stale Strategy.context"
        # finding). No caller has to remember to clear anything.
        market_context = self._build_market_context(now)
        if self.context_mode is ContextMode.ENABLED and getattr(self.strategy, "uses_context", False):
            self.strategy.context = market_context
        else:
            self.strategy.context = None

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
        self._handle_signal(now, signal, position, bar.close, market_context)

    def _build_market_context(self, now: datetime) -> Optional[MarketContext]:
        """Exactly one ``MarketContext`` per processed bar, or ``None`` --
        never more than one call to ``ContextEngine.build_context`` per
        ``on_bar`` invocation, and never any call at all in ``ContextMode.OFF``.

        ``self.bars`` is a bounded ``deque`` (see ``_MIN_BARS_RETAINED``),
        which does not support the slice indexing several ``context/``
        modules rely on (``liquidity.py``/``volatility.py``'s trailing
        windows) -- converting to ``list`` here, once, is the fix; every
        classifier already only reads a trailing slice of whatever it's
        given, so this changes nothing about correctness, only compatibility
        with the container type. Guarded by a broad ``except`` so a defect
        in `context/` can never crash a live/paper/backtest run -- the exact
        same defensive posture `_safe_signal` already takes toward strategy
        code, applied here to context generation.
        """
        if self.context_mode is ContextMode.OFF:
            return None
        try:
            return self.context_engine.build_context(now, bars=list(self.bars))
        except Exception as exc:  # noqa: BLE001 - context generation must never take down the engine.
            log.error("Context generation failed on bar %s: %s", now, exc, exc_info=True)
            return None

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
        market_context: Optional[MarketContext] = None,
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

        # Remember this bar's context so `_record_trade` can attach it once
        # this position eventually closes -- possibly many bars from now.
        # `None` whenever `context_mode` is `OFF` (or context generation
        # failed this bar), same as every other `Trade.entry_context` case.
        self._pending_entry_context = market_context

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
        # Attach the entry-time MarketContext (see `_handle_signal`) -- the
        # broker itself never sets this (it knows nothing about `context/`),
        # so this is the one place every closed trade, from every path
        # (paper broker resolution, live reconciliation, a risk-forced or
        # strategy-requested flatten), gets it. `None` in `ContextMode.OFF`,
        # same as `trade.entry_context` already defaults to.
        if self._pending_entry_context is not None:
            trade = dataclasses.replace(trade, entry_context=self._pending_entry_context)
            # `dataclasses.replace` returns a *new* object -- `trade` (frozen)
            # cannot be mutated in place. Without this, the enriched copy
            # would only ever exist in this method's local scope: gone the
            # moment it returns, while `PaperBroker.trades` (what
            # `backtest/runner.py` reads via `list(broker.trades)` to build
            # `BacktestMetrics.trades`) would still hold the original,
            # un-enriched object. `trade` here is always exactly
            # `self.broker.trades[-1]` at this point (both call sites --
            # `on_bar`'s step 1 and `_flatten` -- fetch it from there and
            # pass it straight through with nothing in between that could
            # add another trade), so overwriting that slot keeps the
            # broker's own record consistent with what every other part of
            # this method already treats as "the" trade.
            if isinstance(self.broker, PaperBroker) and self.broker.trades:
                self.broker.trades[-1] = trade
        self._pending_entry_context = None

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
    context_mode: ContextMode = ContextMode.OFF,
    context_engine: Optional[ContextEngine] = None,
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

    ``context_mode``/``context_engine`` default to ``ContextMode.OFF``/
    ``None`` -- see ``ContextMode`` and ``TradingEngine.__init__``; no
    behavior change for any existing caller.
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

    return TradingEngine(
        settings, strategy, broker, risk, journal,
        signal_filter=signal_filter, context_mode=context_mode, context_engine=context_engine,
    )
