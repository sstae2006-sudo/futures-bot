"""Paper-trading broker.

Simulates fills locally so the framework can run against historical or live
data without risking money. Two modelling choices are worth explaining, because
both make results *worse* than a naive simulator would report — and both are
the reason a paper result has any bearing on a live one.

**Slippage is always adverse.** Market orders fill some distance from the
signal price, and that distance is not symmetric in practice: the fill is worse
than hoped more often than it is better, particularly on stops, which by
definition trigger while price is moving against you. Every market fill here is
pushed against the position by ``slippage_ticks``.

**When a bar touches both the stop and the target, the stop wins.** From OHLC
alone there is no way to know which price came first inside the bar. Assuming
the target filled is the single most common way a backtest reports profits that
never materialise, because that assumption is wrong roughly half the time and
always wrong in your favour. Resolving the ambiguity pessimistically means live
results should surprise you upward rather than downward.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ..contracts import ContractSpec
from ..journal import LOGGER_NAME
from ..models import Bar, ExitReason, Fill, Order, OrderStatus, OrderType, Position, Side, Trade, classify_stop_exit
from .base import Broker, BrokerError

log = logging.getLogger(LOGGER_NAME)


class PaperBroker(Broker):
    def __init__(
        self,
        contract: ContractSpec,
        starting_cash: Decimal = Decimal("200"),
        slippage_ticks: Decimal = Decimal("1"),
        commission_per_side: Decimal = Decimal("0.62"),
    ) -> None:
        self.contract = contract
        self._cash = starting_cash
        self.slippage_ticks = slippage_ticks
        self.commission_per_side = commission_per_side

        self._connected = False
        self._position: Optional[Position] = None
        self._orders: dict[str, Order] = {}
        self._last_bar: Optional[Bar] = None
        self.trades: list[Trade] = []
        #: Set when a bar triggers both protective legs, for reporting.
        self.ambiguous_bars: int = 0

    # --- Connection ---

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def cash(self) -> Decimal:
        return self._cash

    def get_position(self) -> Optional[Position]:
        return self._position

    # --- Pricing helpers ---

    def _slip(self, price: Decimal, side: Side, opening: bool) -> Decimal:
        """Apply adverse slippage.

        Opening a long fills higher than expected; closing a long fills lower.
        Shorts are the mirror image.
        """
        offset = self.slippage_ticks * self.contract.tick_size
        direction = side.sign if opening else -side.sign
        return self.contract.round_to_tick(price + offset * direction)

    # --- Orders ---

    def submit_bracket(
        self,
        side: Side,
        quantity: int,
        stop_loss: Decimal,
        take_profit: Decimal,
        now: datetime,
    ) -> Order:
        if not self._connected:
            raise BrokerError("Paper broker is not connected.")
        if self._position is not None:
            raise BrokerError("Already in a position; flatten before entering another.")
        if self._last_bar is None:
            raise BrokerError("No market data yet; cannot price an entry.")

        # Validate the bracket geometry before accepting it. A stop on the
        # wrong side of entry would be filled instantly.
        entry_ref = self._last_bar.close
        if side is Side.LONG and not (stop_loss < entry_ref < take_profit):
            raise BrokerError(
                f"Long bracket is inverted: stop {stop_loss}, entry {entry_ref}, target {take_profit}."
            )
        if side is Side.SHORT and not (take_profit < entry_ref < stop_loss):
            raise BrokerError(
                f"Short bracket is inverted: target {take_profit}, entry {entry_ref}, stop {stop_loss}."
            )

        fill_price = self._slip(entry_ref, side, opening=True)
        order = Order(
            order_id=Order.new_id(),
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            submitted_at=now,
        )
        self._orders[order.order_id] = order

        self._cash -= self.commission_per_side * quantity
        self._position = Position(
            side=side,
            quantity=quantity,
            entry_price=fill_price,
            entry_time=now,
            stop_loss=self.contract.round_to_tick(stop_loss),
            take_profit=self.contract.round_to_tick(take_profit),
            # Real adapters put broker order ids here; the paper broker
            # simulates the resting pair internally.
            stop_order_id=f"{order.order_id}-stop",
            target_order_id=f"{order.order_id}-target",
            entry_slippage=abs(fill_price - entry_ref),
        )
        return order

    def modify_stop_loss(self, new_stop: Decimal) -> bool:
        if self._position is None:
            raise BrokerError("No open position; nothing to modify.")

        pos = self._position
        rounded = self.contract.round_to_tick(new_stop)

        # A raw ATR-based candidate computed in the strategy can be
        # genuinely higher/lower than the current stop yet round to the same
        # tick as it. That is not a loosening -- it is a no-op, and treating
        # it as one avoids rejecting (and noisily logging) the ordinary case
        # where a trail hasn't advanced far enough yet to clear a full tick.
        if rounded == pos.stop_loss:
            return False

        if pos.side is Side.LONG:
            if pos.stop_loss is not None and rounded < pos.stop_loss:
                raise BrokerError(
                    f"Refusing to move long stop from {pos.stop_loss} to {rounded}: "
                    f"that loosens it. A trail must only ratchet toward the position."
                )
            if self._last_bar is not None and rounded >= self._last_bar.close:
                raise BrokerError(
                    f"Refusing to set long stop {rounded} at or above the last price "
                    f"{self._last_bar.close}: it would fill immediately."
                )
        else:
            if pos.stop_loss is not None and rounded > pos.stop_loss:
                raise BrokerError(
                    f"Refusing to move short stop from {pos.stop_loss} to {rounded}: "
                    f"that loosens it. A trail must only ratchet toward the position."
                )
            if self._last_bar is not None and rounded <= self._last_bar.close:
                raise BrokerError(
                    f"Refusing to set short stop {rounded} at or below the last price "
                    f"{self._last_bar.close}: it would fill immediately."
                )

        pos.stop_loss = rounded
        return True

    def cancel_all(self) -> None:
        for order in self._orders.values():
            if order.status is OrderStatus.PENDING:
                order.status = OrderStatus.CANCELLED

    def flatten(self, now: datetime, reason: str) -> Optional[Fill]:
        if self._position is None:
            return None
        if self._last_bar is None:
            raise BrokerError("No market data yet; cannot price an exit.")

        raw_price = self._last_bar.close
        exit_price = self._slip(raw_price, self._position.side, opening=False)
        return self._close(exit_price, now, reason, exit_slippage=abs(exit_price - raw_price))

    # --- Market data ---

    def on_bar(self, bar: Bar, now: datetime) -> Optional[Trade]:
        """Feed a bar in and resolve any protective order it triggers.

        Returns the completed :class:`Trade` if the position closed on this
        bar, otherwise ``None``.
        """
        self._last_bar = bar
        pos = self._position
        if pos is None or pos.stop_loss is None or pos.take_profit is None:
            return None

        if pos.side is Side.LONG:
            stop_hit = bar.low <= pos.stop_loss
            target_hit = bar.high >= pos.take_profit
        else:
            stop_hit = bar.high >= pos.stop_loss
            target_hit = bar.low <= pos.take_profit

        # Both protective levels are market fills once triggered (a stop
        # always was; a resting target becomes one too the instant it's
        # touched in this simulation) -- so both get the same adverse
        # slippage `submit_bracket`/`flatten` already apply. Passing the
        # bare `pos.stop_loss`/`pos.take_profit` here (as this code used to)
        # let every stop/target exit fill at the exact protective level with
        # zero slippage, silently contradicting the module docstring's
        # "every market fill here is pushed against the position" and
        # overstating P&L on the two most common exit paths in every backtest.
        if stop_hit and target_hit:
            # Ambiguous bar. Resolve against the position — see module docstring.
            self.ambiguous_bars += 1
            exit_price = self._slip(pos.stop_loss, pos.side, opening=False)
            log.warning(
                "Ambiguous bar at %s: stop %s and target %s both within [low=%s, high=%s]; "
                "resolving against the position at %s.",
                now, pos.stop_loss, pos.take_profit, bar.low, bar.high, exit_price,
            )
            reason = classify_stop_exit(pos.side, pos.entry_price, exit_price)
            return self._close(
                exit_price, now, f"{reason} (ambiguous bar, resolved against)",
                exit_slippage=abs(exit_price - pos.stop_loss),
            )
        if stop_hit:
            exit_price = self._slip(pos.stop_loss, pos.side, opening=False)
            reason = classify_stop_exit(pos.side, pos.entry_price, exit_price)
            return self._close(exit_price, now, reason, exit_slippage=abs(exit_price - pos.stop_loss))
        if target_hit:
            exit_price = self._slip(pos.take_profit, pos.side, opening=False)
            return self._close(
                exit_price, now, ExitReason.TAKE_PROFIT, exit_slippage=abs(exit_price - pos.take_profit),
            )
        return None

    # --- Internals ---

    def _close(
        self, exit_price: Decimal, now: datetime, reason: str, exit_slippage: Decimal = Decimal("0"),
    ) -> Trade:
        pos = self._position
        assert pos is not None

        gross = (exit_price - pos.entry_price) * pos.side.sign * self.contract.point_value * pos.quantity
        commission = self.commission_per_side * pos.quantity  # exit side only
        self._cash += gross - commission

        trade = Trade(
            side=pos.side,
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=now,
            gross_pnl=gross,
            # Both sides of the round turn, so trade.net_pnl is the true figure.
            commission=self.commission_per_side * pos.quantity * 2,
            exit_reason=reason,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            entry_slippage=pos.entry_slippage,
            exit_slippage=exit_slippage,
        )
        self.trades.append(trade)
        self._position = None
        self.cancel_all()
        return trade
