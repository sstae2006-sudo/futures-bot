"""A reference strategy.

**This is not the client's strategy.** It exists so the framework can be run,
tested, and backtested end to end before the real rules arrive, and to show
what implementing :class:`Strategy` looks like. Delete it or ignore it once the
actual signal logic is specified.

The rules are deliberately plain: enter on an EMA crossover, exit on the
opposite cross. Stops and targets come from settings rather than from the
strategy, so risk stays in one place.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

from ..models import Bar, Position, Side, Signal
from .base import Strategy, StrategyRegistry


def ema(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Exponential moving average, seeded with a simple average.

    Returns a list the same length as ``values``; entries before the seed is
    available are ``None``-free by construction because the caller checks
    ``warmup_bars`` first.
    """
    if len(values) < period:
        return []

    k = Decimal(2) / Decimal(period + 1)
    seed = sum(values[:period]) / Decimal(period)

    out: list[Decimal] = [seed]
    for value in values[period:]:
        out.append(value * k + out[-1] * (1 - k))
    return out


@StrategyRegistry.register("ema_crossover")
class EmaCrossover(Strategy):
    """Long when fast EMA crosses above slow, short on the reverse."""

    def __init__(self, contract, fast_period: int = 9, slow_period: int = 21, **params):
        super().__init__(contract, fast_period=fast_period, slow_period=slow_period, **params)
        if fast_period >= slow_period:
            raise ValueError(
                f"fast_period ({fast_period}) must be below slow_period ({slow_period})"
            )
        self.fast_period = fast_period
        self.slow_period = slow_period
        # One extra bar so the previous cross state can be compared.
        self.warmup_bars = slow_period + 2

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        if len(bars) < self.warmup_bars:
            return self.hold(f"Warming up ({len(bars)}/{self.warmup_bars} bars).")

        closes = [b.close for b in bars]
        fast = ema(closes, self.fast_period)
        slow = ema(closes, self.slow_period)

        # The two series have different lengths; align them on their tails.
        span = min(len(fast), len(slow))
        if span < 2:
            return self.hold("Not enough EMA history to detect a cross.")

        fast_now, fast_prev = fast[-1], fast[-2]
        slow_now, slow_prev = slow[-1], slow[-2]

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now

        if position is not None:
            if position.side is Side.LONG and crossed_down:
                return self.exit(f"Fast EMA crossed below slow ({fast_now:.2f} < {slow_now:.2f}).")
            if position.side is Side.SHORT and crossed_up:
                return self.exit(f"Fast EMA crossed above slow ({fast_now:.2f} > {slow_now:.2f}).")
            return self.hold(
                f"Holding {position.side.value}; no opposing cross "
                f"(fast {fast_now:.2f}, slow {slow_now:.2f})."
            )

        if crossed_up:
            return self.enter_long(
                f"Fast EMA crossed above slow ({fast_now:.2f} > {slow_now:.2f}).",
                fast=float(fast_now),
                slow=float(slow_now),
            )
        if crossed_down:
            return self.enter_short(
                f"Fast EMA crossed below slow ({fast_now:.2f} < {slow_now:.2f}).",
                fast=float(fast_now),
                slow=float(slow_now),
            )

        return self.hold(f"No cross (fast {fast_now:.2f}, slow {slow_now:.2f}).")
