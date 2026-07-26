"""Market-regime classification at trade entry time: trend, volatility, and
session-of-day. Feeds `api.services`' trade persistence (each backtest
trade is labeled once, at completion), `live_trade_journal.LiveTradeJournal`
(each live/paper trade, when regime labeling is turned on), and the
regime-performance endpoint (`GET /api/regime/performance`), which groups
stored trades by these labels to answer "when does this strategy actually
work?"

None of this changes any trading decision -- it's read-only research
labeling applied *after* a trade closes, over bars already seen. Lives
under `research/`, not `api/`, so both `api/` (Phase 6B) and
`research_server/` (Phase 8B) can depend on it without either depending on
the other.
"""

from __future__ import annotations

import bisect
from datetime import time
from decimal import Decimal
from typing import Optional, Sequence

from ..contracts import to_ct
from ..models import Bar, Trade
from ..strategy.indicators import atr_series

#: Trend classification looks back this many closes from the entry bar.
_TREND_LOOKBACK = 20
#: A move smaller than this fraction of the starting price over the
#: lookback window doesn't count as a trend either direction.
_TREND_THRESHOLD = Decimal("0.002")

#: CME regular-trading-hours session buckets, Central Time. Anything
#: outside these (the overnight Globex session) is labeled "overnight"
#: rather than forced into one of the four RTH buckets.
_SESSION_BOUNDS: tuple[tuple[time, time, str], ...] = (
    (time(8, 30), time(9, 30), "open"),
    (time(9, 30), time(11, 0), "morning"),
    (time(11, 0), time(13, 0), "lunch"),
    (time(13, 0), time(16, 0), "close"),
)


def classify_session(entry_time) -> str:
    ct = to_ct(entry_time).time()
    for start, end, label in _SESSION_BOUNDS:
        if start <= ct < end:
            return label
    return "overnight"


def classify_trend(closes: Sequence[Decimal]) -> str:
    """Direction of the last `_TREND_LOOKBACK` closes leading up to (and
    including) the entry bar. A simple start-to-end percentage move rather
    than a full indicator -- this labels *market condition for later
    grouping*, not a trading signal, so it deliberately doesn't reuse a
    strategy's own EMA setup (which would tie the label to one strategy's
    definition of trend)."""
    if len(closes) < 2:
        return "sideways"
    window = closes[-_TREND_LOOKBACK:]
    if window[0] == 0:
        return "sideways"
    change = (window[-1] - window[0]) / window[0]
    if change > _TREND_THRESHOLD:
        return "bullish"
    if change < -_TREND_THRESHOLD:
        return "bearish"
    return "sideways"


def classify_volatility(current_atr: Optional[float], low_cut: Optional[float], high_cut: Optional[float]) -> str:
    if current_atr is None or low_cut is None or high_cut is None:
        return "medium"  # not enough history to judge -- neutral default, never fabricated as low/high
    if current_atr <= low_cut:
        return "low"
    if current_atr >= high_cut:
        return "high"
    return "medium"


def compute_regimes(
    trades: Sequence[Trade], bars: Sequence[Bar], atr_period: int = 14,
) -> list[tuple[str, str, str]]:
    """One ``(trend, volatility, session)`` label per trade, in the same
    order as ``trades``. Volatility terciles are computed once across the
    whole ``bars`` series (a trade's ATR is "high" relative to *this
    dataset*, not to some fixed absolute threshold that wouldn't transfer
    between contracts)."""
    if not trades:
        return []

    closes = [b.close for b in bars]
    timestamps = [b.timestamp for b in bars]

    atr_values = atr_series(bars, period=atr_period)
    # atr_series(bars, period)[k] lines up with bars[period + k] -- see that
    # function's own accumulation (the first value is seeded from
    # true_ranges[:period], i.e. bars[1..period], landing on bars[period]).
    low_cut = high_cut = None
    if atr_values:
        ordered = sorted(atr_values)
        low_cut = ordered[len(ordered) // 3]
        high_cut = ordered[(2 * len(ordered)) // 3]

    results: list[tuple[str, str, str]] = []
    for trade in trades:
        # Index of the bar this trade entered on (bisect_right - 1: the
        # last bar at or before entry_time; entry_time always matches a
        # bar's timestamp exactly for a trade produced by this backtest).
        idx = bisect.bisect_right(timestamps, trade.entry_time) - 1
        idx = max(idx, 0)

        trend = classify_trend(closes[: idx + 1])

        current_atr = None
        if idx >= atr_period and atr_values:
            atr_idx = idx - atr_period
            if 0 <= atr_idx < len(atr_values):
                current_atr = atr_values[atr_idx]
        volatility = classify_volatility(current_atr, low_cut, high_cut)

        session = classify_session(trade.entry_time)

        results.append((trend, volatility, session))

    return results
