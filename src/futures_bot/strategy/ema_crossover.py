from __future__ import annotations

from typing import Optional, Sequence

from ..models import Bar, Position, Side, Signal
from .base import Strategy, StrategyRegistry
from .indicators import ema_series, atr_series


@StrategyRegistry.register("ema_crossover")
class EmaCrossover(Strategy):
    """
    EMA crossover with a higher-timeframe trend filter.

    Features:
    - 8 EMA / 34 EMA crossover
    - 200 EMA trend filter
    - EMA separation filter
    - Trend slope filter
    - Long-only (shorts disabled for testing)
    """

    def __init__(
        self,
        contract,
        fast_period: int = 8,
        slow_period: int = 34,
        trend_period: int = 200,
        min_ema_distance: float = 1.5,
        **params,
    ):
        super().__init__(
            contract,
            fast_period=fast_period,
            slow_period=slow_period,
            trend_period=trend_period,
            min_ema_distance=min_ema_distance,
            **params,
        )

        self.fast_period = fast_period
        self.slow_period = slow_period
        self.trend_period = trend_period
        self.min_ema_distance = min_ema_distance

        self.warmup_bars = trend_period + 5

    def on_bar(
        self,
        bars: Sequence[Bar],
        position: Optional[Position],
    ) -> Signal:

        if len(bars) < self.warmup_bars:
            return self.hold(
                f"Warming up ({len(bars)}/{self.warmup_bars})"
            )

        closes = [bar.close for bar in bars]

        fast = ema_series(closes, self.fast_period)
        slow = ema_series(closes, self.slow_period)
        trend = ema_series(closes, self.trend_period)

        fast_now = fast[-1]
        fast_prev = fast[-2]

        slow_now = slow[-1]
        slow_prev = slow[-2]

        trend_now = trend[-1]

        price = closes[-1]

        crossed_up = (
            fast_prev <= slow_prev
            and fast_now > slow_now
        )

        crossed_down = (
            fast_prev >= slow_prev
            and fast_now < slow_now
        )

        bullish_trend = price > trend_now
        bearish_trend = price < trend_now

        trend_rising = trend[-1] > trend[-5]
        trend_falling = trend[-1] < trend[-5]

        ema_distance = abs(fast_now - slow_now)

        if ema_distance < self.min_ema_distance:
            return self.hold(
                f"EMAs too close ({ema_distance:.2f})"
            )

        if position is not None:

            if (
                position.side == Side.LONG
                and crossed_down
            ):
                return self.exit("EMA reversal exit")

            if (
                position.side == Side.SHORT
                and crossed_up
            ):
                return self.exit("EMA reversal exit")

            return self.hold("Holding position")

        if (
            crossed_up
            and bullish_trend
            and trend_rising
        ):
            return self.enter_long(
                "Bullish EMA cross above 200 EMA",
                fast=float(fast_now),
                slow=float(slow_now),
                trend=float(trend_now),
                distance=float(ema_distance),
            )

        # Shorts intentionally disabled while testing.
        # Uncomment below if you want to enable them again.

        # if (
        #     crossed_down
        #     and bearish_trend
        #     and trend_falling
        # ):
        #     return self.enter_short(
        #         "Bearish EMA cross below 200 EMA",
        #         fast=float(fast_now),
        #         slow=float(slow_now),
        #         trend=float(trend_now),
        #         distance=float(ema_distance),
        #     )

        return self.hold("No setup")