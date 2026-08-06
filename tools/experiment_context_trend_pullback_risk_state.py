"""Context -> Strategy Phase 2, experiment 1: does gating `trend_pullback`
entries on `RiskState` hold up under this platform's own skeptical checks?

Runs the full, already-built validation pipeline end to end for the first
time against real historical data: `strategy.context_gate.ContextGatedStrategy`
(Phase 1, 2026-08-04) -> `backtest.context_comparison.compare_context_impact`
(built 2026-07-27, never exercised on a real hypothesis before this) ->
`research.safety.build_safety_report` (the platform's existing overfitting/
degradation/commission-sensitivity guard). Shared harness in
`tools/context_experiment_lib.py`. See
`docs/EXPERIMENT_TREND_PULLBACK_RISK_STATE.md` for the write-up this script's
output feeds -- **result: hypothesis rejected**, see that doc for the numbers.

PRE-REGISTERED HYPOTHESIS (stated before this script was ever run):

    trend_pullback's pullback-continuation entries are hypothesized to
    perform worse when RiskState indicates elevated-or-extreme
    market-condition risk (context/risk.py's RiskState.ELEVATED/HIGH -- a
    composite of volatility_state + market_regime, meaning volatility is
    running hot relative to its own trailing average). Disorderly,
    gap/spike-prone conditions are a documented failure mode for
    continuation systems -- a pullback in that regime is more likely to be
    the start of a reversal than a resumption of the trend. Requiring
    RiskState.LOW at entry is hypothesized to filter these out and improve
    or at least preserve per-trade expectancy, likely at the cost of fewer
    total trades.

This script does not decide whether the hypothesis is true -- it reports
whatever compare_context_impact/build_safety_report actually find, including
if the hypothesis is wrong or the sample is too small to conclude anything.

Usage::

    .venv/Scripts/python.exe tools/experiment_context_trend_pullback_risk_state.py
"""

from __future__ import annotations

from pathlib import Path

from futures_bot.backtest.data import load_bars_from_db
from futures_bot.context import RiskState
from futures_bot.contracts import MES
from futures_bot.research.trade_store import TradeStore
from futures_bot.strategy.context_gate import ContextGatedStrategy, require_if
from futures_bot.strategy.trend_pullback.strategy import TrendPullbackStrategy

from context_experiment_lib import make_mes_settings, print_report, run_experiment

JOURNAL_DIR = Path("logs/experiments/context_trend_pullback_risk_state")
#: A dedicated scratch database, not the live `research.db` -- this is
#: exploratory replay data (Phase 0 of the professional-roadmap plan), not
#: real trades, and must never show up in Mission Control/the Trade
#: Explorer alongside genuine live/paper/API-driven runs.
EXPERIMENTS_DB = Path("research_experiments.db")

RISK_STATE_REASON = (
    "risk_state must be LOW (volatility running hot relative to its trailing "
    "average, per context/risk.py's volatility_state+market_regime composite) "
    "for a pullback-continuation entry -- pre-registered hypothesis"
)


def baseline_factory() -> TrendPullbackStrategy:
    """Unmodified trend_pullback, default parameters -- the strategy's own
    class defaults, not a tuned variant, to avoid cherry-picking."""
    return TrendPullbackStrategy(MES)


def gated_factory() -> ContextGatedStrategy:
    """The hypothesis under test: veto entries unless RiskState.LOW."""
    return ContextGatedStrategy(
        MES,
        inner=TrendPullbackStrategy(MES),
        scorer=require_if(lambda ctx: ctx.risk_state == RiskState.LOW, RISK_STATE_REASON),
        threshold=1.0,
    )


def main() -> None:
    print("Loading real MES 5-minute bars from market_data.db ...")
    bars, report = load_bars_from_db("MES", "5min")
    print(
        f"  {report.bars_loaded} bars, {report.first_timestamp} -> {report.last_timestamp} "
        f"({report.zero_volume_bars} zero-volume bars)"
    )
    if len(bars) < 500:
        raise SystemExit("Too few bars to run a meaningful experiment -- aborting.")

    settings = make_mes_settings(JOURNAL_DIR)
    store = TradeStore(EXPERIMENTS_DB)
    result = run_experiment(
        settings=settings, baseline_factory=baseline_factory, gated_factory=gated_factory,
        bars=bars, journal_dir=JOURNAL_DIR,
        store=store, run_id_prefix="trend-pullback-risk-state", strategy_label="trend_pullback",
    )
    print_report(result)


if __name__ == "__main__":
    main()
