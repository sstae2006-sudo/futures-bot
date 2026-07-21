"""The broker interface.

Everything the engine needs from a broker, and nothing more. Tradovate, IBKR,
or any other adapter implements this; the engine never learns which one it got.

One requirement is worth stating explicitly because it is a safety property
rather than a convenience: :meth:`submit_bracket` must place the protective
stop *at the broker*, as a resting order, in the same call that opens the
position. A stop tracked only in this process disappears the moment the process
does — leaving an open futures position with no protection and nobody watching
it. Any adapter that cannot place a broker-side stop should raise rather than
quietly fall back to an in-memory one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ..models import Bar, Fill, Order, Position, Side


class BrokerError(RuntimeError):
    """Raised when the broker rejects a request or is unreachable."""


class Broker(ABC):
    """Abstract broker connection."""

    @abstractmethod
    def connect(self) -> None:
        """Establish the connection. Must be idempotent."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection, leaving resting orders untouched."""

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def get_position(self) -> Optional[Position]:
        """The current open position, or ``None`` when flat.

        Adapters should report the broker's view rather than a cached local
        one. If the two disagree, the broker is right and this process is
        wrong.
        """

    @abstractmethod
    def submit_bracket(
        self,
        side: Side,
        quantity: int,
        stop_loss: Decimal,
        take_profit: Decimal,
        now: datetime,
    ) -> Order:
        """Open a position with protective stop and target attached.

        The stop and target must rest at the broker as a one-cancels-other
        pair, so that filling either one cancels the other automatically.
        """

    @abstractmethod
    def flatten(self, now: datetime, reason: str) -> Optional[Fill]:
        """Close any open position at market and cancel resting orders."""

    @abstractmethod
    def cancel_all(self) -> None:
        """Cancel every working order, leaving positions alone."""

    @property
    @abstractmethod
    def cash(self) -> Decimal:
        """Account cash balance."""
