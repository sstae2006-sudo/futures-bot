"""The strategy interface.

A strategy's only job is to look at price history and say what it wants. It
does not place orders, size positions, check the clock, or track P&L — the
engine does that, and it does it *after* the risk manager has had a say. The
separation is deliberate: strategy proposes, risk disposes. A strategy that
could place its own orders could also bypass the kill switch.

Two rules for implementers:

1. **Never look ahead.** ``on_bar`` receives history up to and including the
   current bar and nothing beyond it. Indexing past the end is the classic way
   to produce a backtest that looks profitable and cannot be traded.
2. **Always give a reason.** Every returned :class:`Signal` carries a
   human-readable explanation, including holds. The review step depends on
   being able to see why a trade was skipped.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional, Sequence

from ..contracts import ContractSpec
from ..models import Bar, Position, Signal, SignalAction


class Strategy(ABC):
    """Base class for all trading strategies."""

    #: Bars of history required before :meth:`on_bar` is called at all.
    warmup_bars: int = 0

    def __init__(self, contract: ContractSpec, **params) -> None:
        self.contract = contract
        self.params = params

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def on_bar(
        self,
        bars: Sequence[Bar],
        position: Optional[Position],
    ) -> Signal:
        """Decide what to do, given history up to and including ``bars[-1]``.

        Args:
            bars: Price history, oldest first. ``bars[-1]`` is the bar that
                just closed. There is nothing after it — do not try to peek.
            position: The currently open position, or ``None`` when flat.

        Returns:
            A :class:`Signal`. Return ``SignalAction.HOLD`` with a reason when
            there is nothing to do; that is a decision worth logging too.
        """

    def on_start(self) -> None:
        """Called once before the first bar. Override for setup."""

    def on_stop(self) -> None:
        """Called once after the final bar. Override for teardown."""

    # --- Helpers available to subclasses ---

    def hold(self, reason: str) -> Signal:
        return Signal(action=SignalAction.HOLD, reason=reason)

    def enter_long(
        self,
        reason: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        **metadata,
    ) -> Signal:
        return Signal(
            action=SignalAction.ENTER_LONG,
            reason=reason,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata=metadata,
        )

    def enter_short(
        self,
        reason: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        **metadata,
    ) -> Signal:
        return Signal(
            action=SignalAction.ENTER_SHORT,
            reason=reason,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata=metadata,
        )

    def exit(self, reason: str, **metadata) -> Signal:
        return Signal(action=SignalAction.EXIT, reason=reason, metadata=metadata)


class StrategyRegistry:
    """Name-to-class lookup so the settings file can select a strategy."""

    _registry: dict[str, type[Strategy]] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(strategy_cls: type[Strategy]) -> type[Strategy]:
            cls._registry[name] = strategy_cls
            return strategy_cls

        return decorator

    @classmethod
    def get(cls, name: str) -> type[Strategy]:
        if name not in cls._registry:
            known = ", ".join(sorted(cls._registry)) or "(none registered)"
            raise KeyError(f"Unknown strategy {name!r}. Registered: {known}")
        return cls._registry[name]

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._registry)
