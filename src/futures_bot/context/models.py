"""The Market Context data shape.

Foundation phase only (2026-07-27) -- see docs/ARCHITECTURE.md's "Market
Context Engine" section for the target architecture
(``Market Data -> Context Engine -> Strategy Engine -> Risk Engine ->
Execution``), the exact integration point (``engine.TradingEngine.on_bar``,
not wired in yet), and why real classification logic belongs in
``research/regime.py``/``strategy/indicators.py`` reuse rather than here.

``MarketContext`` describes market conditions; it never decides anything.
Every state field is an ``Enum`` with an ``UNKNOWN`` member specifically so
a context can always be constructed safely -- with everything unknown, if
nothing else -- rather than requiring callers to have every classification
in hand before they can build one at all. That's what "missing values
handled safely" means for this object: an omitted field is a real,
representable state (``UNKNOWN``, confidence 0.0), never a construction
error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Optional

if TYPE_CHECKING:
    # Deferred: session.py/volatility.py/regime.py import from *this*
    # module (e.g. VolatilityState, MarketRegime), so a real
    # (non-TYPE_CHECKING) import here would be circular. Safe only
    # because `from __future__ import annotations` above means every
    # annotation in this file is a string at runtime -- never actually
    # evaluated, just available to static type checkers/IDEs.
    from .regime import RegimeContext
    from .session import SessionContext
    from .timeframe import TimeframeAlignment
    from .volatility import VolatilityContext


class SessionPhase(str, Enum):
    """The seven futures-market session phases (see context/session.py,
    which classifies a timestamp into one of these using this codebase's
    existing CME market-calendar logic -- contracts.py -- rather than a
    new one). Boundaries reuse ``research.regime``'s exact RTH buckets
    and ``contracts.py``'s exact maintenance-halt window; see
    session.py's module docstring for the full mapping."""

    OVERNIGHT = "OVERNIGHT"
    PRE_MARKET = "PRE_MARKET"
    OPENING_RANGE = "OPENING_RANGE"
    MORNING_SESSION = "MORNING_SESSION"
    LUNCH_SESSION = "LUNCH_SESSION"
    POWER_HOUR = "POWER_HOUR"
    MARKET_CLOSE = "MARKET_CLOSE"
    UNKNOWN = "UNKNOWN"


class MarketRegime(str, Enum):
    """Five mutually-exclusive market-behavior regimes (see
    context/regime.py, which classifies a timestamp+bars into one of
    these using this codebase's existing ADX/trend/volatility
    primitives rather than new ones). Redefined this phase (2026-07-27)
    from Phase 1's placeholder set (``TRENDING``/``RANGING``/
    ``VOLATILE``) to this exact taxonomy -- confirmed zero usages
    outside ``context/``'s own tests before changing it, same
    discipline as ``SessionPhase``'s Phase 2a rename."""

    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNKNOWN = "UNKNOWN"


class VolatilityState(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


class TrendState(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class LiquidityState(str, Enum):
    THIN = "THIN"
    NORMAL = "NORMAL"
    DEEP = "DEEP"
    UNKNOWN = "UNKNOWN"


class RiskState(str, Enum):
    """Market-condition risk (e.g. "unusually dangerous conditions right
    now"), unrelated to and never consulted by ``risk.manager.RiskManager``
    (the account/session risk gate) -- naming collision only, no code
    relationship. See docs/ARCHITECTURE.md."""

    LOW = "LOW"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


#: Every field name that has a corresponding *_state/regime/session Enum
#: above -- used by ``MarketContext.confidence_scores`` validation and by
#: ``from_dict`` to know which keys need Enum coercion. Kept as one place
#: to extend if a new classification dimension is added later.
_ENUM_FIELDS: Mapping[str, type[Enum]] = {
    "session": SessionPhase,
    "market_regime": MarketRegime,
    "volatility_state": VolatilityState,
    "trend_state": TrendState,
    "liquidity_state": LiquidityState,
    "risk_state": RiskState,
}


@dataclass(frozen=True)
class MarketContext:
    """A snapshot of market conditions at one point in time, for one
    symbol/timeframe. Immutable, like ``models.Bar`` -- a strategy (once a
    future phase actually passes this to one) must not mutate history.

    Provides information *to* strategies. Never makes a trade decision,
    never holds a reference to a broker/risk manager/engine -- the same
    hard boundary ``Strategy`` itself is held to
    (see docs/ARCHITECTURE.md's "Why strategies cannot execute trades
    directly").
    """

    timestamp: datetime
    symbol: str
    timeframe: str
    session: SessionPhase = SessionPhase.UNKNOWN
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    volatility_state: VolatilityState = VolatilityState.UNKNOWN
    trend_state: TrendState = TrendState.UNKNOWN
    liquidity_state: LiquidityState = LiquidityState.UNKNOWN
    risk_state: RiskState = RiskState.UNKNOWN
    #: The rich session classification (minutes_since_open,
    #: liquidity_expectation, is_market_open) from context/session.py's
    #: classify_session(). ``None`` until a caller sets it -- ``session``
    #: above (the bare enum) is kept in sync separately for anything that
    #: only needs the phase, not the full detail; see
    #: ContextEngine._classify_session.
    session_context: Optional["SessionContext"] = None
    #: The rich volatility snapshot (current_atr, average_atr,
    #: volatility_ratio, realized_volatility) from context/volatility.py's
    #: analyze_volatility(). ``None`` until a caller sets it --
    #: ``volatility_state`` above (the bare enum) is kept in sync
    #: separately, same pattern as ``session``/``session_context``.
    volatility_context: Optional["VolatilityContext"] = None
    #: The rich regime snapshot (adx, trend_direction, volatility_ratio,
    #: and the same confidence recorded in confidence_scores["regime"])
    #: from context/regime.py's classify_regime(). ``None`` until a
    #: caller sets it -- ``market_regime`` above (the bare enum) is kept
    #: in sync separately, same pattern as session/volatility.
    regime_context: Optional["RegimeContext"] = None
    #: Trend agreement across the five canonical timeframes (1m/5m/15m/
    #: 1h/1d) from context/timeframe.py's classify_timeframe_alignment().
    #: ``None`` until a caller sets it.
    timeframe_alignment: Optional["TimeframeAlignment"] = None
    #: Per-dimension confidence in [0.0, 1.0], keyed by the same names as
    #: the Enum fields above (e.g. {"market_regime": 0.82}). Missing a key
    #: means "no confidence recorded for that dimension" -- see
    #: ``confidence`` below for how that's summarized, never fabricated.
    confidence_scores: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("MarketContext.timestamp must be timezone-aware")
        if not self.symbol:
            raise ValueError("MarketContext.symbol must not be empty")
        if not self.timeframe:
            raise ValueError("MarketContext.timeframe must not be empty")
        for key, value in self.confidence_scores.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"confidence_scores[{key!r}] = {value} is out of range [0.0, 1.0]"
                )

    @property
    def confidence(self) -> float:
        """Overall confidence -- the mean of whatever's in
        ``confidence_scores``, or 0.0 if nothing's been recorded. 0.0 is
        the same "nothing known yet" value an all-UNKNOWN context already
        uses everywhere else, not a fabricated middle-ground guess."""
        if not self.confidence_scores:
            return 0.0
        return sum(self.confidence_scores.values()) / len(self.confidence_scores)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation -- every Enum becomes its ``.value``
        string, ``timestamp`` becomes ISO 8601. Round-trips through
        ``from_dict``."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "session": self.session.value,
            "market_regime": self.market_regime.value,
            "volatility_state": self.volatility_state.value,
            "trend_state": self.trend_state.value,
            "liquidity_state": self.liquidity_state.value,
            "risk_state": self.risk_state.value,
            "session_context": self.session_context.to_dict() if self.session_context else None,
            "volatility_context": (
                self.volatility_context.to_dict() if self.volatility_context else None
            ),
            "regime_context": self.regime_context.to_dict() if self.regime_context else None,
            "timeframe_alignment": (
                self.timeframe_alignment.to_dict() if self.timeframe_alignment else None
            ),
            "confidence_scores": dict(self.confidence_scores),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MarketContext":
        """Inverse of ``to_dict``. Missing optional keys fall back to the
        same ``UNKNOWN``/empty defaults the constructor itself uses --
        this is the "handles missing values safely" contract applied to
        deserialization, not just direct construction."""
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        session_context_data = data.get("session_context")
        session_context = None
        if session_context_data is not None:
            from .session import SessionContext  # local: see the TYPE_CHECKING import above

            session_context = SessionContext.from_dict(session_context_data)

        volatility_context_data = data.get("volatility_context")
        volatility_context = None
        if volatility_context_data is not None:
            from .volatility import VolatilityContext  # local: see the TYPE_CHECKING import above

            volatility_context = VolatilityContext.from_dict(volatility_context_data)

        regime_context_data = data.get("regime_context")
        regime_context = None
        if regime_context_data is not None:
            from .regime import RegimeContext  # local: see the TYPE_CHECKING import above

            regime_context = RegimeContext.from_dict(regime_context_data)

        timeframe_alignment_data = data.get("timeframe_alignment")
        timeframe_alignment = None
        if timeframe_alignment_data is not None:
            from .timeframe import TimeframeAlignment  # local: see the TYPE_CHECKING import above

            timeframe_alignment = TimeframeAlignment.from_dict(timeframe_alignment_data)

        kwargs: dict[str, Any] = {
            "timestamp": timestamp,
            "symbol": data["symbol"],
            "session_context": session_context,
            "volatility_context": volatility_context,
            "regime_context": regime_context,
            "timeframe_alignment": timeframe_alignment,
            "timeframe": data["timeframe"],
            "confidence_scores": dict(data.get("confidence_scores", {})),
        }
        for field_name, enum_cls in _ENUM_FIELDS.items():
            raw = data.get(field_name)
            kwargs[field_name] = enum_cls(raw) if raw is not None else enum_cls.UNKNOWN
        return cls(**kwargs)


def unknown_context(timestamp: datetime, symbol: str, timeframe: str) -> MarketContext:
    """A context with every classification UNKNOWN and zero confidence --
    the explicit "we have a timestamp/symbol/timeframe but haven't
    classified anything yet" state. Equivalent to ``MarketContext(timestamp,
    symbol, timeframe)`` with no other arguments; exists as a named,
    self-documenting call site for that specific, common case (e.g. a
    strategy running before ``ContextEngine`` has any history to work
    with) rather than callers re-deriving what "all defaults" means."""
    return MarketContext(timestamp=timestamp, symbol=symbol, timeframe=timeframe)
