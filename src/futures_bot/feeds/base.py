"""The live bar feed interface.

Deliberately the narrowest possible surface: one method, "give me every
fully-closed bar I haven't handed you yet, in order." Nothing about polling
interval, connection lifecycle, or how a source decides a bar is closed is
part of the interface -- that is each source's own concern, the same way
`Broker` doesn't say how an adapter talks to its exchange.

"Fully closed" is the one invariant every implementation must uphold: a bar
whose time window hasn't finished yet (the in-progress 5-minute candle right
now) must never be returned, only the ones before it. `TradingEngine.on_bar`
has no way to "revise" a bar it already saw -- feeding it a bar that's still
forming and could still change is a subtler version of the lookahead bias
`strategy/base.py`'s docstring warns strategies about, just introduced from
the data side instead of the strategy side.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from ..models import Bar


class BarFeed(ABC):
    """A source of new, closed bars for live/paper trading."""

    @abstractmethod
    def poll_new_bars(self, now: Optional[datetime] = None) -> list[Bar]:
        """Every fully-closed bar since the last call, oldest first.

        ``now`` defaults to the real wall clock; implementations should
        accept an explicit value too, so tests can poll a fixed moment
        instead of depending on when they happen to run.

        Returns an empty list if nothing new has closed yet -- that is the
        normal, frequent case between bar closes, not an error. Must never
        return the same bar twice, and must never return a bar out of
        chronological order relative to bars already returned (the caller is
        typically feeding these straight into `TradingEngine.on_bar`, which
        assumes strictly increasing timestamps the same way
        `backtest.runner.run_backtest` does).
        """
