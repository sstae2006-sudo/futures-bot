"""Context -> Strategy Phase 2, experiment 5: does gating `ema_crossover`
entries on `market_regime` (avoid RANGING) hold up?

Same pipeline as experiments 1-4 (`ContextGatedStrategy` ->
`compare_context_impact` -> `research.safety.build_safety_report`), shared
harness (`tools/context_experiment_lib.py`). See
`docs/EXPERIMENT_EMA_CROSSOVER_REGIME.md` for the write-up.

PRE-REGISTERED HYPOTHESIS (stated before this script was ever run):

    ema_crossover is a plain fast/slow EMA cross with a 200-EMA trend
    filter -- a well-known failure mode for crossover systems is whipsawing
    in range-bound, choppy conditions (the fast/slow EMAs repeatedly cross
    back and forth with no real trend to ride). `context/regime.py`'s
    `classify_regime` has a dedicated `RANGING` state for exactly this
    condition (ADX below the trending threshold, no dominant volatility
    signal). Requiring `market_regime != RANGING` at entry is hypothesized
    to filter out whipsaw-prone crosses and improve win rate or expectancy,
    at some cost in trade count. Note: `ema_crossover` is long-only in this
    codebase (shorts intentionally disabled, see the strategy's own
    comment) -- this experiment inherits that constraint rather than
    working around it.

This script does not decide whether the hypothesis is true -- it reports
whatever compare_context_impact/build_safety_report actually find.

Usage::

    .venv/Scripts/python.exe tools/experiment_context_ema_crossover_regime.py
"""

from __future__ import annotations

from pathlib import Path

from futures_bot.backtest.data import load_bars_from_db
from futures_bot.context import MarketRegime
from futures_bot.contracts import MES
from futures_bot.research.trade_store import TradeStore
from futures_bot.strategy.context_gate import ContextGatedStrategy, reject_if
from futures_bot.strategy.ema_crossover import EmaCrossover

from context_experiment_lib import make_mes_settings, print_report, run_experiment

JOURNAL_DIR = Path("logs/experiments/context_ema_crossover_regime")
EXPERIMENTS_DB = Path("research_experiments.db")

REASON = (
    "market_regime is RANGING -- pre-registered hypothesis: crossover systems whipsaw "
    "in range-bound conditions"
)


def baseline_factory() -> EmaCrossover:
    """Unmodified ema_crossover, default parameters."""
    return EmaCrossover(MES)


def gated_factory() -> ContextGatedStrategy:
    return ContextGatedStrategy(
        MES,
        inner=EmaCrossover(MES),
        scorer=reject_if(lambda ctx: ctx.market_regime == MarketRegime.RANGING, REASON),
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
        store=store, run_id_prefix="ema-crossover-regime", strategy_label="ema_crossover",
    )
    print_report(result)


if __name__ == "__main__":
    main()
