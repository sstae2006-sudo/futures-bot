"""Context Analytics -- Market Context Engine Phase 8, Part 6
(2026-07-27).

Developer/research-validation analytics over a **sequence** of
already-built ``MarketContext`` snapshots (e.g. one per bar of a
backtest run, or a batch pulled together for research). Answers
questions like "what session/regime/volatility distribution did this
run actually see?" and "how often is each dimension `UNKNOWN`?" -- for
validating context *quality* during development and research, never
for any trading decision.

Pure, stateless functions over a list of ``MarketContext`` objects the
caller already has in hand -- no persistence, no UI (per this phase's
own "No UI required" instruction), no dependency on any other
`context/` module beyond `models.py` (the Enum/dataclass shapes it
reads). Not wired into ``ContextEngine`` -- this is a separate,
external, post-hoc analysis layer over contexts already built, the same
way ``market_data/validation.py`` is a separate, read-only checker over
data already stored rather than something the sync pipeline calls
itself.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import mean, median, pstdev
from typing import Sequence

from .models import MarketContext

#: Every MarketContext Enum dimension this report breaks down, paired
#: with the attribute name to read it from and the Enum member whose
#: `.value` marks "no data" -- one place to extend if a new dimension
#: is ever added.
_ENUM_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("session", "session"),
    ("market_regime", "market_regime"),
    ("volatility_state", "volatility_state"),
    ("trend_state", "trend_state"),
    ("liquidity_state", "liquidity_state"),
    ("risk_state", "risk_state"),
)


@dataclass(frozen=True)
class DistributionSummary:
    """A count/fraction breakdown of one Enum dimension across a batch
    of contexts. ``unknown_fraction`` is broken out specifically since
    "how often did we actually not know" is the single most useful
    quality signal for validating a batch of contexts."""

    counts: dict[str, int]
    fractions: dict[str, float]
    unknown_fraction: float
    total: int


def _distribution(values: Sequence[str]) -> DistributionSummary:
    total = len(values)
    if total == 0:
        return DistributionSummary(counts={}, fractions={}, unknown_fraction=0.0, total=0)
    counts = dict(Counter(values))
    fractions = {k: v / total for k, v in counts.items()}
    unknown_fraction = counts.get("UNKNOWN", 0) / total
    return DistributionSummary(counts=counts, fractions=fractions, unknown_fraction=unknown_fraction, total=total)


@dataclass(frozen=True)
class NumericSummary:
    """min/max/mean/median/population-stdev over a batch of numeric
    values (environment score or confidence). All zero for an empty
    batch -- never a fabricated value or a crash."""

    count: int
    minimum: float
    maximum: float
    mean: float
    median: float
    stdev: float


def _numeric_summary(values: Sequence[float]) -> NumericSummary:
    if not values:
        return NumericSummary(count=0, minimum=0.0, maximum=0.0, mean=0.0, median=0.0, stdev=0.0)
    return NumericSummary(
        count=len(values),
        minimum=min(values),
        maximum=max(values),
        mean=mean(values),
        median=median(values),
        stdev=pstdev(values) if len(values) > 1 else 0.0,
    )


@dataclass(frozen=True)
class ContextAnalyticsReport:
    """The complete distribution report over a batch of ``MarketContext``
    snapshots. Covers every dimension Phase 8's own instructions asked
    for: session/regime/volatility/trend/liquidity/risk distributions,
    environment-score distribution, confidence distribution, and
    UNKNOWN-state frequency per dimension (``unknown_frequency``, a
    single consolidated view alongside each dimension's own
    ``unknown_fraction``)."""

    total_contexts: int
    session: DistributionSummary
    market_regime: DistributionSummary
    volatility_state: DistributionSummary
    trend_state: DistributionSummary
    liquidity_state: DistributionSummary
    risk_state: DistributionSummary
    environment_score: NumericSummary
    confidence: NumericSummary
    unknown_frequency: dict[str, float]

    def render(self) -> str:
        """A short, human-readable text report -- for a developer
        eyeballing a research run's context quality, the same audience
        ``market_data.validation.render_report`` targets."""
        lines = [f"Context Analytics Report -- {self.total_contexts} context(s)", ""]
        if self.total_contexts == 0:
            lines.append("(no contexts to analyze)")
            return "\n".join(lines)

        for label, dist in (
            ("Session", self.session),
            ("Market regime", self.market_regime),
            ("Volatility", self.volatility_state),
            ("Trend", self.trend_state),
            ("Liquidity", self.liquidity_state),
            ("Risk", self.risk_state),
        ):
            lines.append(f"{label} distribution:")
            for value, count in sorted(dist.counts.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {value:>16}: {count:>6}  ({dist.fractions[value]:.1%})")
            lines.append("")

        lines.append(
            f"Environment score: min={self.environment_score.minimum:.0f} "
            f"max={self.environment_score.maximum:.0f} mean={self.environment_score.mean:.1f} "
            f"median={self.environment_score.median:.1f} stdev={self.environment_score.stdev:.1f}"
        )
        lines.append(
            f"Confidence:        min={self.confidence.minimum:.2f} "
            f"max={self.confidence.maximum:.2f} mean={self.confidence.mean:.2f} "
            f"median={self.confidence.median:.2f} stdev={self.confidence.stdev:.2f}"
        )
        lines.append("")
        lines.append("UNKNOWN frequency by dimension:")
        for dim, frac in self.unknown_frequency.items():
            lines.append(f"  {dim:>16}: {frac:.1%}")

        return "\n".join(lines)


def analyze_context_batch(contexts: Sequence[MarketContext]) -> ContextAnalyticsReport:
    """Builds a ``ContextAnalyticsReport`` from a batch of already-built
    ``MarketContext`` snapshots. Empty input is handled safely (every
    summary comes back zeroed/empty, never an exception) -- the same
    "missing data handled safely" discipline every other module in this
    package already follows.
    """
    dimension_values: dict[str, list[str]] = {name: [] for _, name in _ENUM_DIMENSIONS}
    scores: list[float] = []
    confidences: list[float] = []

    for ctx in contexts:
        for _, attr in _ENUM_DIMENSIONS:
            dimension_values[attr].append(getattr(ctx, attr).value)
        if ctx.environment_score is not None:
            scores.append(float(ctx.environment_score.score))
        confidences.append(ctx.confidence)

    distributions = {attr: _distribution(dimension_values[attr]) for _, attr in _ENUM_DIMENSIONS}
    unknown_frequency = {attr: distributions[attr].unknown_fraction for _, attr in _ENUM_DIMENSIONS}

    return ContextAnalyticsReport(
        total_contexts=len(contexts),
        session=distributions["session"],
        market_regime=distributions["market_regime"],
        volatility_state=distributions["volatility_state"],
        trend_state=distributions["trend_state"],
        liquidity_state=distributions["liquidity_state"],
        risk_state=distributions["risk_state"],
        environment_score=_numeric_summary(scores),
        confidence=_numeric_summary(confidences),
        unknown_frequency=unknown_frequency,
    )
