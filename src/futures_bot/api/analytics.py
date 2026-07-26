"""Trade-level analytics computed from the bars a backtest already
replayed: maximum favorable/adverse excursion (MFE/MAE) per trade.

Deliberately post-hoc rather than tracked live inside the engine or
`PaperBroker` -- `trend_pullback/strategy.py`'s `_OpenTradeState.update_excursion`
already proves the live-tracking approach works, but doing it there for
every strategy would mean touching `engine.py`'s hot path and every
strategy's own state for a feature only the research API needs. Since
`entry_time`/`exit_time` on a completed `Trade` always line up with a bar
timestamp in the same `bars` list the backtest replayed (the engine sets
both directly from `bar.timestamp`), MFE/MAE can be recovered afterward by
re-scanning the bar window between them -- no engine changes required.
"""

from __future__ import annotations

import bisect
from decimal import Decimal
from typing import Optional, Sequence

from ..models import Bar, Side, Trade


def compute_excursions(
    trades: Sequence[Trade], bars: Sequence[Bar],
) -> list[tuple[Optional[Decimal], Optional[Decimal]]]:
    """For each trade, the ``(mfe_points, mae_points)`` seen between its
    entry and exit, both >= 0. Returns ``(None, None)`` for a trade whose
    window can't be found in ``bars`` (defensive only -- shouldn't happen
    for trades produced by replaying these same bars through `run_backtest`).

    ``bars`` must be sorted ascending by timestamp (every caller in this
    codebase already guarantees this -- `backtest.runner.run_backtest`
    refuses out-of-order bars outright).
    """
    if not trades:
        return []

    timestamps = [b.timestamp for b in bars]
    results: list[tuple[Optional[Decimal], Optional[Decimal]]] = []

    for trade in trades:
        start = bisect.bisect_left(timestamps, trade.entry_time)
        end = bisect.bisect_right(timestamps, trade.exit_time)
        window = bars[start:end]
        if not window:
            results.append((None, None))
            continue

        if trade.side is Side.LONG:
            mfe = max((b.high - trade.entry_price for b in window), default=Decimal("0"))
            mae = max((trade.entry_price - b.low for b in window), default=Decimal("0"))
        else:
            mfe = max((trade.entry_price - b.low for b in window), default=Decimal("0"))
            mae = max((b.high - trade.entry_price for b in window), default=Decimal("0"))

        results.append((max(mfe, Decimal("0")), max(mae, Decimal("0"))))

    return results
