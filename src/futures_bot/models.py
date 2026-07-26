"""Core domain types.

Everything downstream — strategies, brokers, the risk manager, the engine —
speaks in these. Prices are carried as Decimal rather than float: futures P&L
is computed in exact tick increments, and float drift on a $200 account is the
difference between "stop hit" and "stop missed".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
import uuid


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def opposite(self) -> "Side":
        return Side.SHORT if self is Side.LONG else Side.LONG

    @property
    def sign(self) -> int:
        """+1 for long, -1 for short. Used in P&L arithmetic."""
        return 1 if self is Side.LONG else -1


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class SignalAction(str, Enum):
    """What a strategy wants to happen on this bar."""

    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT = "exit"
    HOLD = "hold"


class InvalidSignalError(ValueError):
    """A strategy produced something that does not satisfy the Signal contract.

    Raised both by :meth:`Signal.__post_init__` (a malformed Signal was
    constructed) and by the engine (a strategy's ``on_bar`` returned
    something that isn't a ``Signal`` at all). Kept as one exception type so
    both cases are recognizable in logs the same way.
    """


@dataclass(frozen=True)
class Bar:
    """One OHLCV price bar. Immutable — strategies must not mutate history."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Bar.timestamp must be timezone-aware")
        if self.high < self.low:
            raise ValueError(f"Bar high {self.high} is below low {self.low}")


@dataclass(frozen=True)
class Signal:
    """A strategy's decision for one bar, plus the reasoning behind it.

    ``reason`` is required rather than optional on purpose. The spec asks for
    logging of every decision, and a decision log that only records *what*
    without *why* is useless during the review-and-optimize step.
    """

    action: SignalAction
    reason: str
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Fail fast, at the strategy call site, rather than downstream in
        the engine or broker where the error message would point at the
        wrong code. Mirrors :meth:`Bar.__post_init__`'s existing pattern."""
        if not isinstance(self.action, SignalAction):
            raise InvalidSignalError(
                f"Signal.action must be a SignalAction, got {type(self.action).__name__}"
            )
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise InvalidSignalError("Signal.reason must be a non-empty string.")
        if self.stop_loss is not None and not isinstance(self.stop_loss, Decimal):
            raise InvalidSignalError(
                f"Signal.stop_loss must be a Decimal or None, got {type(self.stop_loss).__name__}"
            )
        if self.take_profit is not None and not isinstance(self.take_profit, Decimal):
            raise InvalidSignalError(
                f"Signal.take_profit must be a Decimal or None, got {type(self.take_profit).__name__}"
            )
        if not isinstance(self.metadata, dict):
            raise InvalidSignalError(
                f"Signal.metadata must be a dict, got {type(self.metadata).__name__}"
            )


@dataclass
class Order:
    order_id: str
    side: Side
    quantity: int
    order_type: OrderType
    status: OrderStatus = OrderStatus.PENDING
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    submitted_at: Optional[datetime] = None
    # Set when this order is a protective leg of a bracket.
    parent_order_id: Optional[str] = None

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class Fill:
    order_id: str
    side: Side
    quantity: int
    price: Decimal
    timestamp: datetime
    commission: Decimal = Decimal("0")


@dataclass
class Position:
    """An open position in a single contract.

    ``stop_order_id`` and ``target_order_id`` reference orders resting at the
    broker, not values tracked in memory. If this process dies, the protective
    orders survive it.
    """

    side: Side
    quantity: int
    entry_price: Decimal
    entry_time: datetime
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    stop_order_id: Optional[str] = None
    target_order_id: Optional[str] = None
    #: Adverse slippage applied at entry, in index points. Carried here only
    #: so the broker can copy it onto the eventual `Trade` once this
    #: position closes -- not otherwise used by risk/engine logic.
    entry_slippage: Decimal = Decimal("0")

    def unrealized_pnl(self, current_price: Decimal, point_value: Decimal) -> Decimal:
        move = (current_price - self.entry_price) * self.side.sign
        return move * point_value * self.quantity


@dataclass(frozen=True)
class Trade:
    """A completed round turn, written to the trade log."""

    side: Side
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    entry_time: datetime
    exit_time: datetime
    gross_pnl: Decimal
    commission: Decimal
    exit_reason: str
    #: Protective levels in effect when this trade closed. ``take_profit``
    #: is always the entry-time target (nothing in this codebase moves it
    #: once set); ``stop_loss`` reflects the *current* stop at exit, which
    #: only differs from the entry-time stop for a strategy that trails or
    #: ratchets it (``trend_pullback``, today -- see its ``exits.py``).
    #: ``None`` for a Trade with no simulated bracket at all (e.g. a
    #: client-imported fill; see ``research.trade_import``).
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    #: Adverse slippage actually applied, in index points (not dollars, so
    #: it reads the same regardless of contract/quantity) -- see
    #: ``PaperBroker._slip``. Zero for a Trade with nothing simulated to report.
    entry_slippage: Decimal = Decimal("0")
    exit_slippage: Decimal = Decimal("0")

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.commission
