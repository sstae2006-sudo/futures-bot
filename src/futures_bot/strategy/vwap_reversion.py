"""VWAP mean reversion.

VWAP is the price at which the session's volume actually transacted, which is
why institutional desks measure fills against it and why price tends to be
drawn back toward it intraday. This strategy fades extensions: when price
stretches a configured number of standard deviations away, take the other side
and target a return to VWAP.

Rules:

* Anchor VWAP to the session, resetting at the 17:00 CT boundary rather than
  midnight — that is what the exchange considers a new trade date.
* Wait ``min_bars`` into the session before trading anything. Early VWAP is
  computed from a handful of bars and moves erratically; the bands are close to
  meaningless until some volume has accumulated.
* Go short when price closes above the upper band, long when it closes below
  the lower band.
* Exit when price returns to VWAP.

The obvious weakness is worth stating plainly: mean reversion sells strength
and buys weakness, so it does badly precisely when a market trends hard in one
direction all session. Fading a trend day is how this strategy loses, and no
parameter setting removes that — it is what the strategy *is*. The daily loss
limit matters more here than with a trend-following approach.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from ..contracts import session_date
from ..models import Bar, Position, Side, Signal
from .base import Strategy, StrategyRegistry
from .indicators import IncrementalSessionVWAP


@StrategyRegistry.register("vwap_reversion")
class VwapReversion(Strategy):
    def __init__(
        self,
        contract,
        std_devs: Decimal = Decimal("2"),
        min_bars: int = 20,
        exit_at_vwap: bool = True,
        max_entries_per_session: int = 3,
        **params,
    ) -> None:
        super().__init__(contract, **params)
        if min_bars < 2:
            raise ValueError(f"min_bars must be at least 2, got {min_bars}")

        self.std_devs = Decimal(str(std_devs))
        self.min_bars = min_bars
        self.exit_at_vwap = exit_at_vwap
        self.max_entries_per_session = max_entries_per_session

        self._session: Optional[date] = None
        self._entries_taken = 0

        # Incremental, session-anchored VWAP -- updated one bar at a time
        # instead of rescanning the whole session on every call (see
        # `IncrementalSessionVWAP`'s docstring).
        self._ivwap = IncrementalSessionVWAP()

        self.warmup_bars = min_bars

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        current = bars[-1]
        sd = session_date(current.timestamp)

        if sd != self._session:
            self._session = sd
            self._entries_taken = 0

        # Fed every bar, warmup or not -- resets itself at the same session
        # boundary this strategy already tracks.
        self._ivwap.update(current)

        if self._ivwap.session_bar_count < self.min_bars:
            return self.hold(
                f"Only {self._ivwap.session_bar_count} bars into the session; VWAP needs "
                f"{self.min_bars} before its bands mean anything."
            )

        bands = self._ivwap.bands(self.std_devs)
        if bands is None:
            return self.hold("Not enough data to compute VWAP bands.")

        lower, centre, upper = bands
        price = current.close

        if position is not None:
            if not self.exit_at_vwap:
                return self.hold(f"Holding {position.side.value}; VWAP {centre:.2f}.")

            # Target is the mean itself — that was the whole premise.
            if position.side is Side.LONG and price >= centre:
                return self.exit(f"Price returned to VWAP ({price:.2f} >= {centre:.2f}).")
            if position.side is Side.SHORT and price <= centre:
                return self.exit(f"Price returned to VWAP ({price:.2f} <= {centre:.2f}).")

            distance = abs(price - centre)
            return self.hold(
                f"Holding {position.side.value}; {distance:.2f} pts from VWAP {centre:.2f}."
            )

        if self._entries_taken >= self.max_entries_per_session:
            return self.hold(
                f"Already took {self._entries_taken} entries this session "
                f"(limit {self.max_entries_per_session})."
            )

        if price > upper:
            self._entries_taken += 1
            return self.enter_short(
                f"Price {price:.2f} closed above the upper VWAP band {upper:.2f} "
                f"({self.std_devs} sd); fading back toward {centre:.2f}.",
                take_profit=centre if self.exit_at_vwap else None,
                vwap=float(centre),
                upper_band=float(upper),
                vwap_distance=float(price - centre),
            )

        if price < lower:
            self._entries_taken += 1
            return self.enter_long(
                f"Price {price:.2f} closed below the lower VWAP band {lower:.2f} "
                f"({self.std_devs} sd); fading back toward {centre:.2f}.",
                take_profit=centre if self.exit_at_vwap else None,
                vwap=float(centre),
                lower_band=float(lower),
                vwap_distance=float(price - centre),
            )

        return self.hold(
            f"Price {price:.2f} inside the VWAP bands {lower:.2f}–{upper:.2f}."
        )
