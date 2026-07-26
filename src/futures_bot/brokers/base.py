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

from ..models import Bar, Fill, Order, Position, Side, Trade


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
    def modify_stop_loss(self, new_stop: Decimal) -> bool:
        """Moves the resting protective stop for the open position.

        Returns True if the stop actually moved, False if the requested level
        rounds to the same tick it was already at (a normal, frequent
        no-op -- not every bar's trail candidate clears a full tick).

        Must reject (raise BrokerError) a move that would loosen the stop
        further from price than it already is, or that crosses to the wrong
        side of the current price -- a trailing or breakeven stop should only
        ever ratchet toward the position, never away from it. Adapters that
        cannot modify a resting stop order should raise rather than silently
        no-op, for the same reason a missing broker-side stop at entry is a
        hard failure: an unenforced trail is a stop that looks tighter in the
        strategy's own bookkeeping than it actually is at the exchange.
        """

    @abstractmethod
    def cancel_all(self) -> None:
        """Cancel every working order, leaving positions alone."""

    @property
    @abstractmethod
    def cash(self) -> Decimal:
        """Account cash balance."""

    def poll_closed_trade(self, now: datetime) -> Optional[Trade]:
        """If a position this adapter was tracking closed since the last
        call -- a stop or target filled *at the broker*, independent of any
        call this process made -- return the completed :class:`Trade` so the
        engine can record it (P&L, the daily-loss kill switch, the decision
        journal). Return ``None`` otherwise.

        The default here is a no-op, correct for adapters like
        :class:`~futures_bot.brokers.paper.PaperBroker` that resolve fills
        synchronously inside the same call that reports the bar (see its own
        ``on_bar``, which the engine calls instead of this for that adapter
        specifically). A live adapter connected to a real exchange, where a
        resting stop or target can fill at any moment rather than only when
        this process happens to check, **must** override this. Without it, a
        real fill would never reach ``RiskManager.record_trade`` -- the
        kill switch would silently stop enforcing the daily loss limit the
        moment a position closes on its own.
        """
        return None
