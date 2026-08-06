"""Context -> Strategy Phase 2, experiment 2: does requiring the Context
Engine's `market_regime` to agree with the entry direction improve
`trend_pullback`, where experiment 1's `RiskState` filter did not?

Same pipeline as experiment 1 (`strategy.context_gate.ContextGatedStrategy`
-> `backtest.context_comparison.compare_context_impact` ->
`research.safety.build_safety_report`), same shared harness
(`tools/context_experiment_lib.py`), a different dimension. See
`docs/EXPERIMENT_TREND_PULLBACK_REGIME_ALIGNMENT.md` for the write-up this
script's output feeds.

PRE-REGISTERED HYPOTHESIS (stated before this script was ever run):

    trend_pullback's own trend filter is a blunt, binary check
    (`ema50 > ema200 and close > ema200` for a long, the mirror for a
    short -- see strategy.py's `_handle_flat`) that says nothing about
    trend *strength* or whether volatility conditions still support calling
    it a real trend. The Context Engine's `market_regime`
    (context/regime.py's `classify_regime`: TRENDING_UP/TRENDING_DOWN/
    RANGING/HIGH_VOLATILITY/LOW_VOLATILITY, combining an ADX>=25 strength
    threshold, trend direction, and the volatility signal -- a materially
    different, more selective computation than the strategy's own bare EMA
    order) is hypothesized to be a stronger confirmation of a genuine
    trend. Requiring the Context Engine's regime to independently agree
    with the entry's direction (TRENDING_UP for a long, TRENDING_DOWN for a
    short) is hypothesized to filter out weak/borderline EMA-cross entries
    and improve win rate or expectancy, likely at a further cost in trade
    count on top of the strategy's own already-selective filters.

This script does not decide whether the hypothesis is true -- it reports
whatever compare_context_impact/build_safety_report actually find.

Usage::

    .venv/Scripts/python.exe tools/experiment_context_trend_pullback_regime_alignment.py
"""

from __future__ import annotations

from pathlib import Path

from futures_bot.backtest.data import load_bars_from_db
from futures_bot.context import MarketRegime, MarketContext
from futures_bot.contracts import MES
from futures_bot.models import Signal, SignalAction
from futures_bot.research.trade_store import TradeStore
from futures_bot.strategy.context_gate import ContextGatedStrategy, ContextScore
from futures_bot.strategy.trend_pullback.strategy import TrendPullbackStrategy

from context_experiment_lib import make_mes_settings, print_report, run_experiment

JOURNAL_DIR = Path("logs/experiments/context_trend_pullback_regime_alignment")
EXPERIMENTS_DB = Path("research_experiments.db")

#: Not expressible with reject_if/require_if (both take a context-only
#: predicate) since the required regime depends on the entry's own
#: direction -- ContextScorer's signature already supports reading the
#: Signal too, so a small custom scorer is the right tool here, not a
#: limitation of the interface.
def regime_alignment_scorer(signal: Signal, context: "MarketContext") -> ContextScore:
    if signal.action is SignalAction.ENTER_LONG:
        required = MarketRegime.TRENDING_UP
    else:
        required = MarketRegime.TRENDING_DOWN

    if context.market_regime is required:
        return ContextScore(
            1.0, f"market_regime={context.market_regime.value} agrees with the entry direction"
        )
    return ContextScore(
        0.0,
        f"market_regime={context.market_regime.value} does not confirm {required.value}; "
        f"the entry's own EMA-based trend read is not corroborated by the Context Engine's "
        f"regime classifier -- pre-registered hypothesis",
    )


def baseline_factory() -> TrendPullbackStrategy:
    return TrendPullbackStrategy(MES)


def gated_factory() -> ContextGatedStrategy:
    return ContextGatedStrategy(
        MES, inner=TrendPullbackStrategy(MES), scorer=regime_alignment_scorer, threshold=1.0,
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
        store=store, run_id_prefix="trend-pullback-regime-alignment", strategy_label="trend_pullback",
    )
    print_report(result)


if __name__ == "__main__":
    main()
