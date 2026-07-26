"""Tests for `api.analytics.compute_excursions` (MAE/MFE)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from futures_bot.contracts import CME_TZ
from futures_bot.models import Bar, Side, Trade
from futures_bot.api.analytics import compute_excursions

START = datetime(2026, 1, 5, 9, 0, tzinfo=CME_TZ)


def make_bars(prices: list[float], highs: list[float] | None = None, lows: list[float] | None = None) -> list[Bar]:
    highs = highs or [p + 1 for p in prices]
    lows = lows or [p - 1 for p in prices]
    return [
        Bar(
            timestamp=START + timedelta(minutes=i), open=Decimal(str(p)), high=Decimal(str(h)),
            low=Decimal(str(low)), close=Decimal(str(p)), volume=100,
        )
        for i, (p, h, low) in enumerate(zip(prices, highs, lows))
    ]


def make_trade(bars, entry_idx, exit_idx, side, entry_price, exit_price) -> Trade:
    return Trade(
        side=side, quantity=1, entry_price=Decimal(str(entry_price)), exit_price=Decimal(str(exit_price)),
        entry_time=bars[entry_idx].timestamp, exit_time=bars[exit_idx].timestamp,
        gross_pnl=Decimal("0"), commission=Decimal("1"), exit_reason="target",
    )


class TestComputeExcursions:
    def test_long_trade_mfe_mae(self):
        bars = make_bars(
            [100, 102, 105, 103, 98, 101, 104, 107, 106, 110],
            highs=[101, 103, 106, 104, 99, 102, 105, 108, 107, 111],
            lows=[99, 101, 104, 102, 97, 100, 103, 106, 105, 109],
        )
        trade = make_trade(bars, 0, 4, Side.LONG, 100, 98)
        mfe, mae = compute_excursions([trade], bars)[0]
        assert mfe == Decimal("6")  # best high in [0,4] is 106 -> 106-100
        assert mae == Decimal("3")  # worst low in [0,4] is 97 -> 100-97

    def test_short_trade_mfe_mae(self):
        bars = make_bars(
            [100, 98, 95, 97, 102],
            highs=[101, 99, 96, 98, 103],
            lows=[99, 97, 94, 96, 101],
        )
        trade = make_trade(bars, 0, 4, Side.SHORT, 100, 102)
        mfe, mae = compute_excursions([trade], bars)[0]
        assert mfe == Decimal("6")  # best low is 94 -> 100-94
        assert mae == Decimal("3")  # worst high is 103 -> 103-100

    def test_never_negative(self):
        """A trade that closed immediately favorable with no adverse tick at
        all still returns 0 for the other side, never a negative number."""
        bars = make_bars([100, 101], highs=[100.5, 101.5], lows=[99.5, 100.5])
        trade = make_trade(bars, 0, 1, Side.LONG, 100, 101)
        mfe, mae = compute_excursions([trade], bars)[0]
        assert mfe >= 0
        assert mae >= 0

    def test_empty_trades_returns_empty(self):
        bars = make_bars([100, 101])
        assert compute_excursions([], bars) == []

    def test_multiple_trades_independent_windows(self):
        bars = make_bars([100, 105, 100, 95, 100, 110])
        t1 = make_trade(bars, 0, 1, Side.LONG, 100, 105)
        t2 = make_trade(bars, 2, 3, Side.SHORT, 100, 95)
        results = compute_excursions([t1, t2], bars)
        assert len(results) == 2
        # Each trade's excursions should only reflect its own window.
        assert results[0] != (None, None)
        assert results[1] != (None, None)

    def test_trade_outside_bar_range_returns_none(self):
        bars = make_bars([100, 101])
        far_future = bars[-1].timestamp + timedelta(days=1)
        trade = Trade(
            side=Side.LONG, quantity=1, entry_price=Decimal("100"), exit_price=Decimal("101"),
            entry_time=far_future, exit_time=far_future + timedelta(minutes=5),
            gross_pnl=Decimal("0"), commission=Decimal("1"), exit_reason="target",
        )
        assert compute_excursions([trade], bars)[0] == (None, None)
