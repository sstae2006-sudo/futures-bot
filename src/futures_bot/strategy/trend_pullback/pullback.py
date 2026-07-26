"""The pullback entry state machine.

The spec is explicit that this must not enter on the raw EMA cross. Instead:
the trend has to already be established (that's what the entry filters check
every bar), then price has to retrace toward EMA21, then a candle in the
trend's direction has to close inside that pullback zone, and only then does
the *next* bar's momentum resumption trigger the entry.

States, for a long setup (short is the mirror image):

``IDLE`` -- not watching anything; the filters aren't all passing.
``WATCHING`` -- filters pass; waiting for price to enter the pullback zone
around EMA21.
``ARMED`` -- a bullish candle just closed inside the pullback zone. Its high
is recorded as the trigger level.
Resolves back to ``IDLE`` if the trend filters stop passing, or if armed for
longer than ``max_arm_bars`` without triggering (a stale setup is not the
same setup).

This lives entirely inside the state machine class rather than the strategy,
so it can be constructed and driven bar-by-bar in a test with no engine, no
broker, and no risk manager involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

from ...models import Bar, Side
from .rolling import IndicatorSnapshot


class PullbackState(Enum):
    IDLE = "idle"
    WATCHING = "watching"
    ARMED = "armed"


@dataclass(frozen=True)
class PullbackConfig:
    #: How close price must come to EMA21 to count as "pulling back", in
    #: index points. Points rather than an ATR multiple, matching how the
    #: spec names it ("Pullback distance") -- simpler to reason about and to
    #: explain to a non-programmer editing config.yaml.
    pullback_distance: Decimal = Decimal("3")
    #: An armed setup this many bars old without triggering is stale.
    max_arm_bars: int = 6


@dataclass
class PullbackResult:
    triggered: bool
    reason: str
    trigger_level: Optional[Decimal] = None


class PullbackTracker:
    """One instance per direction (long or short) -- see TrendPullbackStrategy."""

    def __init__(self, side: Side, config: PullbackConfig) -> None:
        self.side = side
        self.config = config
        self.state = PullbackState.IDLE
        self._armed_bar_high_or_low: Optional[Decimal] = None
        self._armed_at: int = 0

    def reset(self) -> None:
        self.state = PullbackState.IDLE
        self._armed_bar_high_or_low = None
        self._armed_at = 0

    def _in_pullback_zone(self, bar: Bar, snap: IndicatorSnapshot) -> bool:
        assert snap.ema21 is not None
        if self.side is Side.LONG:
            # Price dipping down toward (or slightly through) EMA21.
            return bar.low <= snap.ema21 + self.config.pullback_distance
        return bar.high >= snap.ema21 - self.config.pullback_distance

    def _is_signal_candle(self, bar: Bar) -> bool:
        """A candle closing in the trend's direction -- the retracement's rejection."""
        return bar.close > bar.open if self.side is Side.LONG else bar.close < bar.open

    def update(self, bar: Bar, snap: IndicatorSnapshot, bar_index: int, trend_ok: bool) -> PullbackResult:
        """Advances the state machine by one bar. Call only when the strategy is flat."""
        if not trend_ok:
            if self.state is not PullbackState.IDLE:
                self.reset()
            return PullbackResult(False, "Trend filters not passing; not watching for a pullback.")

        if self.state is PullbackState.IDLE:
            self.state = PullbackState.WATCHING
            return PullbackResult(False, "Trend confirmed; watching for a pullback to EMA21.")

        if self.state is PullbackState.WATCHING:
            if not self._in_pullback_zone(bar, snap):
                return PullbackResult(
                    False,
                    f"Trend intact; price has not pulled back to EMA21 "
                    f"({snap.ema21:.2f} +/- {self.config.pullback_distance})."
                )
            if not self._is_signal_candle(bar):
                return PullbackResult(
                    False,
                    "Price is in the pullback zone but the candle didn't close in the "
                    "trend's direction yet."
                )
            # Armed: this candle's extreme is the level that confirms momentum resumed.
            self._armed_bar_high_or_low = bar.high if self.side is Side.LONG else bar.low
            self._armed_at = bar_index
            self.state = PullbackState.ARMED
            return PullbackResult(
                False,
                f"Pullback candle closed {'bullish' if self.side is Side.LONG else 'bearish'} "
                f"at {snap.ema21:.2f} +/- {self.config.pullback_distance}; armed at "
                f"{self._armed_bar_high_or_low:.2f}."
            )

        # ARMED
        if bar_index - self._armed_at > self.config.max_arm_bars:
            self.reset()
            self.state = PullbackState.WATCHING
            return PullbackResult(False, "Armed setup went stale without triggering; re-watching.")

        assert self._armed_bar_high_or_low is not None
        triggered = (
            bar.close > self._armed_bar_high_or_low
            if self.side is Side.LONG
            else bar.close < self._armed_bar_high_or_low
        )
        if not triggered:
            return PullbackResult(
                False,
                f"Armed at {self._armed_bar_high_or_low:.2f}; momentum has not resumed yet."
            )

        level = self._armed_bar_high_or_low
        self.reset()
        return PullbackResult(
            True,
            f"Momentum resumed through the armed level {level:.2f}.",
            trigger_level=level,
        )
