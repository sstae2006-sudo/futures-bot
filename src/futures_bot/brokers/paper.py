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

from datetime import datetime
from decimal import Decimal
from typing import Optional

from ..contracts import ContractSpec
from ..models import Bar, Fill, Order, OrderStatus, OrderType, Position, Side, Trade
from .base import Broker, BrokerError


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
        )
        return order

    def cancel_all(self) -> None:
        for order in self._orders.values():
            if order.status is OrderStatus.PENDING:
                order.status = OrderStatus.CANCELLED

    def flatten(self, now: datetime, reason: str) -> Optional[Fill]:
        if self._position is None:
            return None
        if self._last_bar is None:
            raise BrokerError("No market data yet; cannot price an exit.")

        exit_price = self._slip(self._last_bar.close, self._position.side, opening=False)
        return self._close(exit_price, now, reason)

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

        if stop_hit and target_hit:
            # Ambiguous bar. Resolve against the position — see module docstring.
            self.ambiguous_bars += 1
            return self._close(pos.stop_loss, now, "stop_loss (ambiguous bar, resolved against)")
        if stop_hit:
            return self._close(pos.stop_loss, now, "stop_loss")
        if target_hit:
            return self._close(pos.take_profit, now, "take_profit")
        return None

    # --- Internals ---

    def _close(self, exit_price: Decimal, now: datetime, reason: str) -> Trade:
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
        )
        self.trades.append(trade)
        self._position = None
        self.cancel_all()
        return trade
