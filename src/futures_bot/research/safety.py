"""Guards against fake performance.

Extends `BacktestMetrics.caveats()`'s existing philosophy -- state in plain
language why a result should not be trusted at face value -- to the
*cross-run* checks a single backtest can't make about itself: how a
candidate degrades from training to out-of-sample validation, whether the
winning parameter combination is a robust region or an isolated lucky spike
in the grid, and whether the edge survives higher trading costs.

Every check returns a :class:`Finding` (a message plus whether it's severe
enough to force low confidence on its own) rather than raising -- these are
judgment calls about whether to trust a result, not correctness errors, the
same distinction `config.py`'s docstring draws between its errors and its
warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional, Sequence

from ..backtest.metrics import MIN_TRADES_FOR_SIGNIFICANCE, BacktestMetrics
from ..config import Settings
from ..models import Bar
from ..strategy.base import Strategy

#: Below this many out-of-sample trades, a validation result is close to
#: meaningless regardless of how good it looks.
MIN_VALIDATION_TRADES = 20
#: A train -> validation drop larger than this is treated as a warning sign
#: rather than ordinary sampling variance.
DEGRADATION_WARNING_PCT = Decimal("30")
DEGRADATION_SEVERE_PCT = Decimal("60")
#: A profit factor above this, on a small sample, reads as "too clean to be
#: real" rather than as a strategy to trust.
SUSPICIOUSLY_HIGH_PROFIT_FACTOR = Decimal("4")
#: If commission is doubled and net P&L falls by more than this fraction,
#: the edge is mostly a function of today's cost assumptions, not the signal.
COMMISSION_SENSITIVITY_WARNING_PCT = Decimal("50")


@dataclass(frozen=True)
class Finding:
    message: str
    severe: bool = False


@dataclass(frozen=True)
class SafetyReport:
    """The required "do not trust this result because..." section.

    ``confidence`` is derived, not asserted: "High" with zero findings,
    "Medium" with only non-severe findings, "Low" the moment any single
    finding is severe (a validation collapse, near-zero out-of-sample trades,
    etc. -- one severe problem is enough to distrust a result regardless of
    how clean everything else looks).
    """

    confidence: str
    findings: list[Finding]

    @property
    def reasons(self) -> list[str]:
        return [f.message for f in self.findings]

    def format(self) -> str:
        if not self.findings:
            return "No warnings. This does not mean the result is correct -- only that none of the automated checks fired."
        lines = ["Do not trust this result at face value because:"]
        lines += [f"  - {f.message}" for f in self.findings]
        return "\n".join(lines)


def _confidence_from(findings: Sequence[Finding]) -> str:
    if not findings:
        return "High"
    if any(f.severe for f in findings):
        return "Low"
    return "Medium"


def check_trade_counts(train: BacktestMetrics, validation: Optional[BacktestMetrics]) -> list[Finding]:
    findings: list[Finding] = []
    if train.trade_count < MIN_TRADES_FOR_SIGNIFICANCE:
        findings.append(
            Finding(
                f"Only {train.trade_count} training trades (below {MIN_TRADES_FOR_SIGNIFICANCE}) -- "
                f"the parameters were ranked on very little evidence.",
                severe=train.trade_count < MIN_TRADES_FOR_SIGNIFICANCE // 2,
            )
        )
    if validation is None:
        findings.append(
            Finding("No out-of-sample validation was run -- this is a purely in-sample result.", severe=True)
        )
    elif validation.trade_count < MIN_VALIDATION_TRADES:
        findings.append(
            Finding(
                f"Only {validation.trade_count} validation trades (below {MIN_VALIDATION_TRADES}) -- "
                f"the out-of-sample check itself is close to noise.",
                severe=validation.trade_count < MIN_VALIDATION_TRADES // 2,
            )
        )
    return findings


def check_degradation(train: BacktestMetrics, validation: Optional[BacktestMetrics]) -> list[Finding]:
    """How much the result falls off from training to validation."""
    if validation is None:
        return []
    findings: list[Finding] = []

    if train.net_pnl > 0:
        if validation.net_pnl <= 0:
            findings.append(
                Finding(
                    f"Profitable in training (${train.net_pnl}) but not out-of-sample "
                    f"(${validation.net_pnl}) -- the edge did not survive contact with unseen data.",
                    severe=True,
                )
            )
        else:
            # Per-trade expectancy, not raw net P&L: a validation slice is
            # normally a smaller fraction of the data than training (a 70/30
            # split, say), so its total P&L is *always* lower regardless of
            # quality. Comparing totals would flag nearly every healthy
            # validation run as "degraded" for no reason but having less data.
            train_exp, val_exp = train.expectancy, validation.expectancy
            if train_exp is not None and train_exp > 0 and val_exp is not None:
                degradation = (Decimal("1") - (val_exp / train_exp)) * 100
                if degradation > DEGRADATION_WARNING_PCT:
                    findings.append(
                        Finding(
                            f"Performance degraded {degradation:.0f}% out of sample "
                            f"(training expectancy ${train_exp:.2f}/trade -> "
                            f"validation ${val_exp:.2f}/trade; "
                            f"training ${train.net_pnl} over {train.trade_count} trades, "
                            f"validation ${validation.net_pnl} over {validation.trade_count} trades).",
                            severe=degradation > DEGRADATION_SEVERE_PCT,
                        )
                    )

    if train.profit_factor is not None and validation.profit_factor is not None:
        if train.profit_factor > 1 and validation.profit_factor < 1:
            findings.append(
                Finding(
                    f"Profit factor fell below 1.0 out-of-sample ({validation.profit_factor:.2f}, "
                    f"training was {train.profit_factor:.2f}) -- validation lost money overall.",
                    severe=True,
                )
            )

    return findings


def check_unrealistic_gains(metrics: BacktestMetrics) -> list[Finding]:
    findings: list[Finding] = []
    if metrics.gross_losses == 0 and metrics.wins:
        findings.append(
            Finding(
                "No losing trades at all. On real data that almost always indicates a data or "
                "logic problem rather than a strategy without risk.",
                severe=True,
            )
        )
    elif metrics.profit_factor is not None and metrics.profit_factor > SUSPICIOUSLY_HIGH_PROFIT_FACTOR:
        findings.append(
            Finding(
                f"Profit factor of {metrics.profit_factor:.2f} is unusually high -- "
                f"treat as a sign to double-check the data and fill assumptions, not as an edge this strong.",
                severe=metrics.trade_count < MIN_TRADES_FOR_SIGNIFICANCE,
            )
        )
    share = metrics.largest_win_share_pct
    if share is not None and share > 40:
        findings.append(
            Finding(
                f"The single best trade produced {share:.0f}% of net profit -- remove it and the "
                f"result looks materially different.",
                severe=share > 70,
            )
        )
    return findings


def check_parameter_fitting(
    winner_score: Decimal, all_scores: Sequence[Decimal], neighbor_fraction: Decimal = Decimal("0.5")
) -> list[Finding]:
    """Is the winner part of a robust region, or an isolated spike?

    Crude but honest: counts how many of the OTHER combinations tried scored
    within ``neighbor_fraction`` of the winner (only meaningful when the
    winner is profitable -- an all-negative grid has no "robust region" to
    speak of). A winner with few or no close competitors was found by
    searching, not by the parameter genuinely mattering.
    """
    others = [s for s in all_scores if s != winner_score]
    if not others or winner_score <= 0:
        return []

    threshold = winner_score * (Decimal("1") - neighbor_fraction)
    close_competitors = sum(1 for s in others if s >= threshold)
    fraction = Decimal(close_competitors) / Decimal(len(others))

    if fraction < Decimal("0.1") and len(others) >= 5:
        return [
            Finding(
                f"Only {close_competitors} of {len(others)} other combinations tried scored anywhere "
                f"close to the winner -- this looks like an isolated spike in the grid, not a robust "
                f"region. A parameter that genuinely matters usually has profitable neighbors.",
                severe=False,
            )
        ]
    return []


def check_commission_sensitivity(
    settings: Settings,
    strategy_factory: Callable[[], Strategy],
    bars: Sequence[Bar],
    multiplier: Decimal = Decimal("2"),
) -> list[Finding]:
    """Re-runs the same bars/params at ``multiplier`` times the configured
    commission and compares net P&L. Import is local to avoid a module-level
    cycle (`backtest.runner` doesn't import `research`)."""
    from ..backtest.runner import run_backtest

    baseline = run_backtest(settings, strategy_factory(), bars)
    if baseline.net_pnl <= 0 or baseline.trade_count == 0:
        return []

    shocked_broker = settings.broker.model_copy(
        update={"commission_per_side": settings.broker.commission_per_side * multiplier}
    )
    shocked_settings = settings.model_copy(update={"broker": shocked_broker})
    shocked = run_backtest(shocked_settings, strategy_factory(), bars)

    if shocked.net_pnl <= 0:
        return [
            Finding(
                f"At {multiplier}x commission, net P&L flips from ${baseline.net_pnl} to "
                f"${shocked.net_pnl} -- the edge is mostly a function of today's cost assumptions.",
                severe=True,
            )
        ]

    drop = (Decimal("1") - (shocked.net_pnl / baseline.net_pnl)) * 100
    if drop > COMMISSION_SENSITIVITY_WARNING_PCT:
        return [
            Finding(
                f"At {multiplier}x commission, net P&L drops {drop:.0f}% (${baseline.net_pnl} -> "
                f"${shocked.net_pnl}) -- high sensitivity to trading costs.",
                severe=False,
            )
        ]
    return []


def build_safety_report(
    *,
    train: BacktestMetrics,
    validation: Optional[BacktestMetrics] = None,
    winner_score: Optional[Decimal] = None,
    all_scores: Optional[Sequence[Decimal]] = None,
    commission_check: Optional[list[Finding]] = None,
) -> SafetyReport:
    """Combines every check that has data available into one report.

    Parameter-fitting and commission-sensitivity checks need more than a
    single train/validation pair (a full grid of scores, or the ability to
    re-run a backtest), so they're passed in already computed rather than
    this function reaching out to run more backtests itself -- keeps this a
    pure aggregator, callable from a test with plain `BacktestMetrics`
    objects and no strategy/bars required.
    """
    findings: list[Finding] = []
    findings += check_trade_counts(train, validation)
    findings += check_degradation(train, validation)
    findings += check_unrealistic_gains(train)
    if validation is not None:
        findings += check_unrealistic_gains(validation)
    if winner_score is not None and all_scores is not None:
        findings += check_parameter_fitting(winner_score, all_scores)
    if commission_check:
        findings += commission_check

    return SafetyReport(confidence=_confidence_from(findings), findings=findings)
