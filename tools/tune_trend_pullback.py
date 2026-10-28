"""Parameter tuning for `trend_pullback`'s own settings, on real MES
5-minute data -- run because Experiments 1-2 (see
`docs/EXPERIMENT_TREND_PULLBACK_RISK_STATE.md`/
`docs/EXPERIMENT_TREND_PULLBACK_REGIME_ALIGNMENT.md`) both found the
strategy's untuned, class-default baseline has no demonstrated edge on this
data -- any context filter on top of a strategy that doesn't work yet is
testing on an unstable foundation.

This is deliberately NOT a context experiment: no `ContextGatedStrategy`
involved. It uses `research/optimizer.py::run_optimization` exactly as any
existing caller (CLI `--optimize`, the API) would, which already runs the
full skeptical treatment internally (train/validation split, a
`min_trades` floor, commission-sensitivity assessment, and
`research/safety.py`'s parameter-fitting-robustness check via
`OptimizationResult.safety`) -- nothing reimplemented here.

The grid is deliberately modest -- 27 combinations across the two parameter
groups most likely to matter for an ATR-scaled trend-following system
(entry selectivity via `adx_min`, and risk:reward via
`atr_stop_mult`/`atr_target_mult`) -- not a sweep across all ~15
constructor parameters. A large, unprincipled grid search is exactly the
kind of "search until something looks good" behavior
`research/safety.py::check_parameter_fitting` exists to catch; keeping the
grid small and economically motivated is part of not defeating that check's
purpose.

Usage::

    .venv/Scripts/python.exe tools/tune_trend_pullback.py
"""

from __future__ import annotations

from pathlib import Path

from futures_bot.backtest.data import load_bars_from_db
from futures_bot.research.optimizer import format_optimization_report, run_optimization
from futures_bot.research.trade_store import TradeStore

from context_experiment_lib import make_mes_settings

JOURNAL_DIR = Path("logs/experiments/tune_trend_pullback")
#: Same dedicated scratch database the context experiments use -- not the
#: live `research.db`. See context_experiment_lib.py's EXPERIMENTS_DB note.
EXPERIMENTS_DB = Path("research_experiments.db")
BATCH_ID = "trend-pullback-tuning-2026-08-05"

PARAM_GRID = {
    "adx_min": [15, 20, 25],
    "atr_stop_mult": [1.0, 1.5, 2.0],
    "atr_target_mult": [2.0, 3.0, 4.0],
}


def main() -> None:
    print("Loading real MES 5-minute bars from market_data.db ...")
    bars, report = load_bars_from_db("MES", "5min")
    print(
        f"  {report.bars_loaded} bars, {report.first_timestamp} -> {report.last_timestamp} "
        f"({report.zero_volume_bars} zero-volume bars)\n"
    )

    settings = make_mes_settings(JOURNAL_DIR)
    store = TradeStore(EXPERIMENTS_DB)
    result = run_optimization(
        settings, "trend_pullback", PARAM_GRID, bars, store=store, batch_id=BATCH_ID, resolution="5min",
    )

    print(format_optimization_report(result))

    print("\n=== All trials, training score order (top 10) ===")
    for t in result.all_trials[:10]:
        print(f"  {t.params} -> trades={t.train_metrics.trade_count} "
              f"net_pnl=${t.train_metrics.net_pnl} pf={t.train_metrics.profit_factor}")


if __name__ == "__main__":
    main()
