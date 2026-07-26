"""Tests for `research.regime`: trend/volatility/session classification."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal

from futures_bot.contracts import CME_TZ
from futures_bot.models import Bar, Side, Trade
from futures_bot.research.regime import classify_session, classify_trend, classify_volatility, compute_regimes


def bar_at(ct_hour, ct_minute, price=100.0):
    ts = datetime(2026, 1, 5, ct_hour, ct_minute, tzinfo=CME_TZ)
    return Bar(timestamp=ts, open=Decimal(str(price)), high=Decimal(str(price + 1)),
               low=Decimal(str(price - 1)), close=Decimal(str(price)), volume=100)


class TestClassifySession:
    def test_open(self):
        assert classify_session(bar_at(8, 45).timestamp) == "open"

    def test_morning(self):
        assert classify_session(bar_at(10, 0).timestamp) == "morning"

    def test_lunch(self):
        assert classify_session(bar_at(12, 0).timestamp) == "lunch"

    def test_close(self):
        assert classify_session(bar_at(14, 30).timestamp) == "close"

    def test_overnight(self):
        assert classify_session(bar_at(20, 0).timestamp) == "overnight"

    def test_boundary_is_exclusive_on_the_end(self):
        assert classify_session(bar_at(9, 30).timestamp) == "morning"  # 9:30 starts morning, not open


class TestClassifyTrend:
    def test_rising_closes_are_bullish(self):
        closes = [Decimal(str(100 + i)) for i in range(25)]
        assert classify_trend(closes) == "bullish"

    def test_falling_closes_are_bearish(self):
        closes = [Decimal(str(100 - i)) for i in range(25)]
        assert classify_trend(closes) == "bearish"

    def test_flat_closes_are_sideways(self):
        closes = [Decimal("100") for _ in range(25)]
        assert classify_trend(closes) == "sideways"

    def test_tiny_move_is_sideways(self):
        closes = [Decimal("100"), Decimal("100.01")]
        assert classify_trend(closes) == "sideways"

    def test_single_close_is_sideways(self):
        assert classify_trend([Decimal("100")]) == "sideways"


class TestClassifyVolatility:
    def test_below_low_cut_is_low(self):
        assert classify_volatility(1.0, low_cut=2.0, high_cut=5.0) == "low"

    def test_above_high_cut_is_high(self):
        assert classify_volatility(6.0, low_cut=2.0, high_cut=5.0) == "high"

    def test_between_cuts_is_medium(self):
        assert classify_volatility(3.0, low_cut=2.0, high_cut=5.0) == "medium"

    def test_missing_data_defaults_to_medium_not_fabricated(self):
        assert classify_volatility(None, None, None) == "medium"


class TestComputeRegimes:
    def _bars(self, n=200, seed=1):
        rng = random.Random(seed)
        start = datetime(2026, 1, 5, 8, 30, tzinfo=CME_TZ)
        bars = []
        price = Decimal("100")
        for i in range(n):
            price += Decimal(str(round(rng.uniform(-1, 1), 2)))
            bars.append(Bar(
                timestamp=start + timedelta(minutes=i), open=price,
                high=price + Decimal(str(round(rng.uniform(0, 2), 2))),
                low=price - Decimal(str(round(rng.uniform(0, 2), 2))),
                close=price, volume=rng.randint(100, 500),
            ))
        return bars

    def test_returns_one_label_triple_per_trade(self):
        bars = self._bars()
        trades = [
            Trade(side=Side.LONG, quantity=1, entry_price=bars[50].close, exit_price=bars[55].close,
                  entry_time=bars[50].timestamp, exit_time=bars[55].timestamp,
                  gross_pnl=Decimal("0"), commission=Decimal("1"), exit_reason="target"),
            Trade(side=Side.SHORT, quantity=1, entry_price=bars[100].close, exit_price=bars[105].close,
                  entry_time=bars[100].timestamp, exit_time=bars[105].timestamp,
                  gross_pnl=Decimal("0"), commission=Decimal("1"), exit_reason="target"),
        ]
        regimes = compute_regimes(trades, bars)
        assert len(regimes) == 2
        for trend, vol, session in regimes:
            assert trend in ("bullish", "bearish", "sideways")
            assert vol in ("low", "medium", "high")
            assert session in ("open", "morning", "lunch", "close", "overnight")

    def test_empty_trades_returns_empty(self):
        assert compute_regimes([], self._bars()) == []

    def test_session_label_matches_entry_time_directly(self):
        bars = self._bars()
        # bars[50].timestamp = 08:30 + 50min = 09:20 CT -> "open"
        trade = Trade(
            side=Side.LONG, quantity=1, entry_price=bars[50].close, exit_price=bars[52].close,
            entry_time=bars[50].timestamp, exit_time=bars[52].timestamp,
            gross_pnl=Decimal("0"), commission=Decimal("1"), exit_reason="target",
        )
        _, _, session = compute_regimes([trade], bars)[0]
        assert session == classify_session(bars[50].timestamp)
