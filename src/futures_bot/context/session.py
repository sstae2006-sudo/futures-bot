"""Session Context: what part of the futures trading day is occurring
right now, and what that implies for expected liquidity.

Input: a timestamp and a symbol. Output: a ``SessionContext``. No new
market calendar is built here -- every weekend/holiday/maintenance-halt
rule is ``contracts.py``'s existing CME session arithmetic
(``is_weekend_closure``, ``is_cme_holiday``, ``in_maintenance_halt``,
``is_market_open``), reused directly, not re-implemented.
Deliberately NOT ``session_date()`` for this module's own "session
start" clock -- see ``classify_session``'s inline comment for why that
function's halt-moment attribution is the wrong thing to measure
elapsed minutes against. Feeds ``MarketContext.session``/``session_context`` (see
models.py) and, like the rest of ``context/``, only describes -- it never
decides anything or reaches into risk/broker/engine code (see
models.MarketContext's docstring).

**Session boundaries reuse three independent, already-established
conventions instead of inventing new numbers:**

1. 08:30 CT as the regular-trading-hours open -- ``research/regime.py``'s
   own ``_SESSION_BOUNDS`` and ``strategy/opening_range_breakout.py``'s
   ``session_start_ct`` default agree on this identically.
2. ``research/regime.py``'s exact RTH bucket boundaries -- (08:30, 09:30),
   (09:30, 11:00), (11:00, 13:00), (13:00, 16:00) -- reused verbatim
   below (``_RTH_BOUNDS``) under this module's own phase names, not
   re-derived.
3. ``contracts.py``'s own ``SESSION_OPEN``/``SESSION_CLOSE`` (17:00/16:00
   CT) and ``in_maintenance_halt`` -- ``MARKET_CLOSE`` below *is* the
   maintenance halt, not a new concept.

The one genuinely new boundary is where ``PRE_MARKET`` starts -- there is
no existing convention for this anywhere in the codebase. It is a named,
documented, overridable parameter (``premarket_start_ct``, default 08:00
CT, 30 minutes ahead of the RTH open every other boundary here is
anchored to), not a hardcoded literal buried in logic.

Weekends and holidays are not given an eighth "closed" ``SessionPhase`` --
the task names exactly seven phases. They classify as ``OVERNIGHT`` (the
closest phase in spirit: long, low-liquidity, not an active RTH session),
with ``is_market_open=False`` and ``liquidity_expectation="NONE"`` as the
unambiguous signal that nothing is actually tradeable -- a caller should
check that flag, not try to infer closure from the session label alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Mapping

from ..contracts import (
    SESSION_CLOSE,
    SESSION_OPEN,
    in_maintenance_halt,
    is_cme_holiday,
    is_market_open as _is_market_open,
    is_weekend_closure,
    to_ct,
)
from .models import SessionPhase

#: The one new boundary (see module docstring): 30 minutes ahead of the
#: 08:30 CT regular-trading-hours open.
DEFAULT_PREMARKET_START_CT = time(8, 0)

#: research/regime.py's exact RTH boundaries, reused verbatim under this
#: module's own phase names -- (start, end, phase), all Central Time.
_RTH_BOUNDS: tuple[tuple[time, time, SessionPhase], ...] = (
    (time(8, 30), time(9, 30), SessionPhase.OPENING_RANGE),
    (time(9, 30), time(11, 0), SessionPhase.MORNING_SESSION),
    (time(11, 0), time(13, 0), SessionPhase.LUNCH_SESSION),
    (time(13, 0), SESSION_CLOSE, SessionPhase.POWER_HOUR),
)

#: Descriptive, session-time-derived liquidity expectation -- not
#: measured from real volume/order-book data (that's a genuinely new
#: future phase; see ROADMAP.md's "Market Context Engine (phased)"
#: Phase 3, `liquidity_state`). Overridden to "NONE" whenever
#: `is_market_open` is False, regardless of this table, since nothing
#: trades then irrespective of which phase label applies.
_LIQUIDITY_EXPECTATION: dict[SessionPhase, str] = {
    SessionPhase.OVERNIGHT: "LOW",
    SessionPhase.PRE_MARKET: "LOW",
    SessionPhase.OPENING_RANGE: "HIGH",
    SessionPhase.MORNING_SESSION: "NORMAL",
    SessionPhase.LUNCH_SESSION: "LOW",
    SessionPhase.POWER_HOUR: "HIGH",
    SessionPhase.MARKET_CLOSE: "NONE",
    SessionPhase.UNKNOWN: "UNKNOWN",
}


@dataclass(frozen=True)
class SessionContext:
    """What part of the trading day ``timestamp`` falls in, for
    ``symbol``. Immutable, like every other context/ value object --
    describes, never decides (see models.MarketContext's docstring)."""

    timestamp: datetime
    symbol: str
    session: SessionPhase
    minutes_since_open: int
    liquidity_expectation: str
    is_market_open: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "session": self.session.value,
            "minutes_since_open": self.minutes_since_open,
            "liquidity_expectation": self.liquidity_expectation,
            "is_market_open": self.is_market_open,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionContext":
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        return cls(
            timestamp=timestamp,
            symbol=data["symbol"],
            session=SessionPhase(data["session"]) if data.get("session") else SessionPhase.UNKNOWN,
            minutes_since_open=int(data.get("minutes_since_open", 0)),
            liquidity_expectation=data.get("liquidity_expectation", "UNKNOWN"),
            is_market_open=bool(data.get("is_market_open", False)),
        )


def _minutes_from_session_open(t: time) -> int:
    """Minutes from SESSION_OPEN (17:00 CT) forward to clock-time ``t``,
    treating ``t`` as falling on the session's second calendar day (true
    for every boundary this module uses except SESSION_OPEN itself,
    which is 0 by definition). Computed from the actual time objects, not
    hand-counted, so an overridden ``premarket_start_ct`` (or any future
    change to the RTH boundaries/SESSION_OPEN/SESSION_CLOSE) is reflected
    automatically."""
    open_minutes = SESSION_OPEN.hour * 60 + SESSION_OPEN.minute
    t_minutes = t.hour * 60 + t.minute
    return (24 * 60 - open_minutes) + t_minutes


def _phase_boundaries(premarket_start_ct: time) -> list[tuple[int, SessionPhase]]:
    """Ordered (minutes-since-session-open, phase) boundaries covering
    the full ~24-hour session continuously -- no gaps, no overlaps."""
    bounds = [
        (0, SessionPhase.OVERNIGHT),
        (_minutes_from_session_open(premarket_start_ct), SessionPhase.PRE_MARKET),
    ]
    for start, _end, phase in _RTH_BOUNDS:
        bounds.append((_minutes_from_session_open(start), phase))
    bounds.append((_minutes_from_session_open(SESSION_CLOSE), SessionPhase.MARKET_CLOSE))
    return sorted(bounds, key=lambda pair: pair[0])


def classify_session(
    timestamp: datetime,
    symbol: str,
    premarket_start_ct: time = DEFAULT_PREMARKET_START_CT,
) -> SessionContext:
    """Classifies ``timestamp`` into one of the seven session phases.

    ``symbol`` is accepted (and carried onto the result) for API-shape
    stability -- every registered contract (``contracts.CONTRACTS``)
    shares identical CME equity-index-futures session boundaries today,
    so it doesn't change the classification yet. A future contract with
    a genuinely different calendar would need this parameter to actually
    matter; it is not decorative, it is a placeholder for that.

    ``minutes_since_open`` is minutes since the *current phase's own*
    start (matching the task's example: session="OPENING_RANGE",
    minutes_since_open=12 means 12 minutes into that specific phase, not
    12 minutes into the overall ~24-hour session).

    Raises ``ValueError`` on a naive (non-timezone-aware) ``timestamp`` --
    the same contract ``contracts.to_ct``/``models.Bar`` already hold
    callers to elsewhere in this codebase, not a new requirement invented
    here.
    """
    if timestamp.tzinfo is None:
        raise ValueError("classify_session requires a timezone-aware timestamp")

    ct = to_ct(timestamp)
    ct_time = ct.time()
    market_open = _is_market_open(timestamp)

    # The most recent SESSION_OPEN (17:00 CT) at or before this moment.
    # Deliberately NOT session_date() -- that function attributes a
    # maintenance-halt moment (16:00-17:00 CT) to the session about to
    # open (see its own docstring), which is the *wrong* session to
    # measure "minutes elapsed" against precisely during the halt itself
    # (the halt is still part of the session that's ending, not the one
    # that hasn't started yet) -- confirmed by manual testing at 16:30 CT,
    # which produced minutes_since_open=0 instead of 30 before this fix.
    session_start = ct.replace(hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0)
    if session_start > ct:
        session_start -= timedelta(days=1)
    elapsed_minutes = int((ct - session_start).total_seconds() // 60)

    if is_weekend_closure(timestamp) or is_cme_holiday(timestamp):
        session = SessionPhase.OVERNIGHT
    elif in_maintenance_halt(timestamp):
        session = SessionPhase.MARKET_CLOSE
    else:
        session = SessionPhase.OVERNIGHT
        for start, end, phase in _RTH_BOUNDS:
            if start <= ct_time < end:
                session = phase
                break
        else:
            if premarket_start_ct <= ct_time < time(8, 30):
                session = SessionPhase.PRE_MARKET

    # Each phase appears exactly once per ~24h cycle in `_phase_boundaries`
    # (OVERNIGHT always at minute 0, the others each at their one fixed
    # clock-time boundary) -- a direct lookup, not a search for "the last
    # matching occurrence" (there's only ever one).
    phase_start_minutes = dict(
        (phase, minutes) for minutes, phase in _phase_boundaries(premarket_start_ct)
    ).get(session, 0)
    minutes_since_open = max(0, elapsed_minutes - phase_start_minutes)

    liquidity_expectation = (
        "NONE" if not market_open else _LIQUIDITY_EXPECTATION[session]
    )

    return SessionContext(
        timestamp=timestamp,
        symbol=symbol,
        session=session,
        minutes_since_open=minutes_since_open,
        liquidity_expectation=liquidity_expectation,
        is_market_open=market_open,
    )
