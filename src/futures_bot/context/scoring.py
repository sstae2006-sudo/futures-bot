"""Context Scoring System -- Market Context Engine Phase 7 (2026-07-27),
made configurable in Phase 8 (2026-07-27).

Combines every ``MarketContext`` dimension into a single 0-100 "Market
Environment Score": how favorable current conditions look for a
systematic strategy to operate in generally -- clear trend structure,
normal (not dead, not chaotic) volatility, a liquid session, confirmed
price structure, ample liquidity, manageable risk. It is **not** a
directional (bullish/bearish) signal, and it never decides a trade:
``score_environment`` reads an already-built ``MarketContext`` and
produces information only.

**Important, matching this phase's own instructions exactly: this score
does NOT decide trades. It is information only. Strategies will consume
it later** (once a future, explicit-approval-gated phase wires
``MarketContext`` into ``Strategy``/``TradingEngine`` at all --
``EnvironmentScore`` carries no broker/risk-manager/engine reference of
any kind, the same hard boundary every other file in ``context/`` is
held to).

**Configurable weighting (Phase 8):** every dimension's maximum
contribution is a field on ``ScoringConfig``, not a hardcoded constant --
``trend_weight``/``volatility_weight``/``session_weight``/
``structure_weight``/``liquidity_weight``/``risk_weight``. This supports
future experimentation (different weightings, research into which
dimensions actually predict performance) without changing any code here:
construct a ``ScoringConfig`` with different values and pass it to
``score_environment``/``with_environment_score``. ``DEFAULT_SCORING_CONFIG``
holds the exact values this phase's own worked example was built
against (Trend 20, Volatility 15, Session 10, Structure 20, Liquidity
15, Risk -10 -> Environment Score 70/100 -- ``20 + 15 + 10 + 20 + 15 -
10 == 70`` exactly) -- calling ``score_environment(context)`` with no
``config`` argument reproduces every pre-Phase-8 test's behavior
exactly, verified directly by
``tests/test_context_scoring.py``'s ``TestConfigurableScoring``.

A dimension with no data available yet (``UNKNOWN``, or the whole
sub-context missing) contributes exactly ``0.0`` and is left out of
both the ``reasons`` explanation and the ``confidence`` calculation --
never a fabricated middle-ground guess, regardless of which
``ScoringConfig`` is in effect.

``confidence`` is the fraction of the six dimensions that actually had
data (``[0.0, 1.0]``) -- a measure of how complete the picture behind
the score is, independent of whether the score itself is high or low.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping, Optional

from .models import LiquidityState, MarketContext, MarketRegime, RiskState, TrendState, VolatilityState


@dataclass(frozen=True)
class ScoringConfig:
    """Centralizes every tunable weight ``score_environment`` uses.
    Immutable, like every other value object in this package --
    experimenting with a different weighting means constructing a new
    ``ScoringConfig``, never mutating one in place.

    Field values default to exactly what Phase 7's worked example was
    built against; see the module docstring. ``risk_weight`` is a
    magnitude -- risk only ever *subtracts*, so its contribution range
    is ``[-risk_weight, 0]``, never positive (see ``_score_risk``).
    """

    trend_weight: float = 20.0
    volatility_weight: float = 15.0
    session_weight: float = 10.0
    structure_weight: float = 20.0
    liquidity_weight: float = 15.0
    risk_weight: float = 10.0


#: The weighting this package has used since Phase 7 -- passing no
#: ``config`` argument to ``score_environment``/``with_environment_score``
#: is exactly equivalent to passing this explicitly.
DEFAULT_SCORING_CONFIG = ScoringConfig()

#: Every dimension's name, in the same order they're scored -- also the
#: key vocabulary of ``EnvironmentScore.breakdown`` and the denominator
#: for ``confidence`` (fraction of these six that had real data).
DIMENSIONS: tuple[str, ...] = ("trend", "volatility", "session", "structure", "liquidity", "risk")


@dataclass(frozen=True)
class _Contribution:
    """Internal -- one dimension's signed contribution, its (optional)
    human-readable reason, and whether it had real data at all."""

    name: str
    value: float
    reason: Optional[str]
    known: bool


def _score_trend(context: MarketContext, config: ScoringConfig) -> _Contribution:
    """Averages whichever trend-strength signals are available --
    ``regime_context.confidence`` (only meaningful when the regime
    itself is directional) and ``timeframe_alignment.alignment_score``
    -- rather than picking just one. Trend *clarity* scores positively
    regardless of direction: this is an environment-quality score, not
    a bullish/bearish signal."""
    signals: list[float] = []
    if context.regime_context is not None and context.market_regime in (
        MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN,
    ):
        signals.append(context.regime_context.confidence)
    if context.timeframe_alignment is not None and context.timeframe_alignment.alignment:
        signals.append(context.timeframe_alignment.alignment_score)

    if not signals:
        return _Contribution("trend", 0.0, None, False)

    strength = sum(signals) / len(signals)
    if strength >= 0.7:
        reason = "Strong trend alignment"
    elif strength >= 0.4:
        reason = "Moderate trend alignment"
    else:
        reason = "Weak or unclear trend"
    return _Contribution("trend", strength * config.trend_weight, reason, True)


def _score_volatility(context: MarketContext, config: ScoringConfig) -> _Contribution:
    """VolatilityState -> (contribution, reason). NORMAL is the ideal
    reading (full positive weight); EXTREME is the worst (full negative
    weight) -- a systematic strategy generally wants neither a dead nor
    a chaotic market. UNKNOWN is intentionally absent (handled as "no
    data")."""
    w = config.volatility_weight
    scores: Mapping[VolatilityState, tuple[float, str]] = {
        VolatilityState.NORMAL: (w, "Normal volatility"),
        VolatilityState.LOW: (w / 3, "Low volatility"),
        VolatilityState.HIGH: (-w / 3, "Elevated volatility"),
        VolatilityState.EXTREME: (-w, "Extreme volatility"),
    }
    entry = scores.get(context.volatility_state)
    if entry is None:
        return _Contribution("volatility", 0.0, None, False)
    value, reason = entry
    return _Contribution("volatility", value, reason, True)


def _score_session(context: MarketContext, config: ScoringConfig) -> _Contribution:
    """SessionContext.liquidity_expectation (a plain string -- see
    session.py) -> (contribution, reason)."""
    session_ctx = context.session_context
    if session_ctx is None:
        return _Contribution("session", 0.0, None, False)
    w = config.session_weight
    scores: Mapping[str, tuple[float, str]] = {
        "HIGH": (w, "Favorable session liquidity"),
        "NORMAL": (w * 0.6, "Normal session liquidity"),
        "LOW": (w * 0.3, "Reduced session liquidity"),
        "NONE": (0.0, "Market closed"),
    }
    entry = scores.get(session_ctx.liquidity_expectation)
    if entry is None:
        return _Contribution("session", 0.0, None, False)
    value, reason = entry
    return _Contribution("session", value, reason, True)


def _score_structure(context: MarketContext, config: ScoringConfig) -> _Contribution:
    structure_ctx = context.structure_context
    if structure_ctx is None or structure_ctx.trend is TrendState.UNKNOWN:
        return _Contribution("structure", 0.0, None, False)
    if structure_ctx.trend is TrendState.NEUTRAL:
        return _Contribution("structure", 0.0, "No clear price structure", True)
    reason = "Clear bullish structure" if structure_ctx.trend is TrendState.BULLISH else "Clear bearish structure"
    return _Contribution(
        "structure", structure_ctx.structure_confidence * config.structure_weight, reason, True,
    )


def _score_liquidity(context: MarketContext, config: ScoringConfig) -> _Contribution:
    """LiquidityState -> (contribution, reason)."""
    w = config.liquidity_weight
    scores: Mapping[LiquidityState, tuple[float, str]] = {
        LiquidityState.DEEP: (w, "Good liquidity"),
        LiquidityState.NORMAL: (w * 0.5, "Normal liquidity"),
        LiquidityState.THIN: (0.0, "Thin liquidity"),
    }
    entry = scores.get(context.liquidity_state)
    if entry is None:
        return _Contribution("liquidity", 0.0, None, False)
    value, reason = entry
    return _Contribution("liquidity", value, reason, True)


def _score_risk(context: MarketContext, config: ScoringConfig) -> _Contribution:
    """RiskState -> (contribution, reason). Risk is a pure-penalty
    dimension -- it can reduce the score but never adds to it, since
    "risk is low" is the absence of a problem, not a positive edge."""
    w = config.risk_weight
    scores: Mapping[RiskState, tuple[float, str]] = {
        RiskState.LOW: (0.0, "Risk conditions normal"),
        RiskState.ELEVATED: (-w / 2, "Elevated risk conditions"),
        RiskState.HIGH: (-w, "High risk conditions"),
    }
    entry = scores.get(context.risk_state)
    if entry is None:
        return _Contribution("risk", 0.0, None, False)
    value, reason = entry
    return _Contribution("risk", value, reason, True)


_SCORERS = (_score_trend, _score_volatility, _score_session, _score_structure, _score_liquidity, _score_risk)


@dataclass(frozen=True)
class EnvironmentScore:
    """A 0-100 market-environment score as of ``timestamp``, for one
    symbol. Immutable, matching every other ``*Context``/``*Score`` in
    this package. Carries no broker/risk-manager/engine reference of
    any kind -- information only, per this phase's own instructions.

    ``score`` is the sum of all six dimensions' signed contributions
    (scaled by whichever ``ScoringConfig`` was used), clamped to
    ``[0, 100]``. ``confidence`` is the fraction (``[0.0, 1.0]``) of the
    six dimensions that actually had data -- independent of whether the
    score itself is high or low. ``reasons`` is a short, human-readable
    explanation, one entry per dimension that had data (skipping
    ``UNKNOWN`` ones entirely -- there is nothing to explain about a
    dimension with no information). ``breakdown`` is each dimension's
    raw signed contribution before clamping, for transparency/
    debugging -- keyed by ``DIMENSIONS``.
    """

    timestamp: datetime
    symbol: str
    score: int
    confidence: float
    reasons: tuple[str, ...]
    breakdown: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "score": self.score,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "breakdown": dict(self.breakdown),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EnvironmentScore":
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        return cls(
            timestamp=timestamp,
            symbol=data["symbol"],
            score=data["score"],
            confidence=data.get("confidence", 0.0),
            reasons=tuple(data.get("reasons", ())),
            breakdown=dict(data.get("breakdown", {})),
        )


def score_environment(
    context: MarketContext, config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> EnvironmentScore:
    """Builds an ``EnvironmentScore`` from an already-built
    ``MarketContext`` -- this module combines what every other
    ``context/`` module already computed; it never re-derives anything
    from raw bars itself. Information only: see the module docstring.

    ``config`` defaults to ``DEFAULT_SCORING_CONFIG`` -- the exact
    weighting this package has used since Phase 7. Pass a different
    ``ScoringConfig`` to experiment with alternative weightings without
    touching this function.
    """
    contributions = [scorer(context, config) for scorer in _SCORERS]

    raw_total = sum(c.value for c in contributions)
    score = max(0, min(100, round(raw_total)))

    known = [c for c in contributions if c.known]
    confidence = len(known) / len(contributions) if contributions else 0.0

    reasons = tuple(c.reason for c in contributions if c.known and c.reason)
    breakdown = {c.name: c.value for c in contributions}

    return EnvironmentScore(
        timestamp=context.timestamp,
        symbol=context.symbol,
        score=score,
        confidence=confidence,
        reasons=reasons,
        breakdown=breakdown,
    )


def with_environment_score(
    context: MarketContext, config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> MarketContext:
    """Returns a copy of ``context`` with ``environment_score`` filled
    in -- a small helper since ``MarketContext`` is frozen and the score
    is necessarily computed *after* the rest of the context already
    exists (it reads every other dimension off of it)."""
    return replace(context, environment_score=score_environment(context, config))
