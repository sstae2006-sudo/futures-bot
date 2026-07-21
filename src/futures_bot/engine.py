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
from datetime import datetime
from decimal import Decimal
from typing import Optional

from .brokers.base import Broker, BrokerError
from .brokers.paper import PaperBroker
from .config import Settings
from .contracts import session_date
from .journal import DecisionJournal, LOGGER_NAME
from .models import Bar, Position, Side, Signal, SignalAction, Trade
from .risk.manager import RiskManager
from .state import StateStore
from .strategy.base import Strategy

log = logging.getLogger(LOGGER_NAME)


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        strategy: Strategy,
        broker: Broker,
        risk: RiskManager,
        journal: DecisionJournal,
    ) -> None:
        self.settings = settings
        self.strategy = strategy
        self.broker = broker
        self.risk = risk
        self.journal = journal
        self.contract = settings.contract_spec
        self.bars: list[Bar] = []

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

        position = self.broker.get_position()

        # 2. Forced exits outrank everything the strategy might want.
        forced = self.risk.must_flatten(now, position)
        if forced.allowed:
            self._flatten(now, forced.reason)
            return

        # 3. Ask the strategy.
        signal = self.strategy.on_bar(self.bars, position)

        # 4. Act, subject to risk.
        self._handle_signal(now, signal, position, bar.close)

    def _handle_signal(
        self,
        now: datetime,
        signal: Signal,
        position: Optional[Position],
        price: Decimal,
    ) -> None:
        session_pnl = self.risk.session_pnl(now)

        if signal.action is SignalAction.HOLD:
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
) -> TradingEngine:
    """Assemble an engine from settings."""
    store = StateStore(settings.state_file)
    risk = RiskManager(settings, store)
    journal = DecisionJournal(settings.logging.directory, settings.logging.log_every_decision)

    if broker is None:
        if settings.broker.name != "paper":
            raise NotImplementedError(
                f"Broker adapter {settings.broker.name!r} is not implemented yet. "
                f"Implement futures_bot.brokers.base.Broker and pass it in."
            )
        broker = PaperBroker(
            contract=settings.contract_spec,
            starting_cash=settings.broker.starting_cash,
            slippage_ticks=settings.broker.slippage_ticks,
            commission_per_side=settings.broker.commission_per_side,
        )

    return TradingEngine(settings, strategy, broker, risk, journal)
