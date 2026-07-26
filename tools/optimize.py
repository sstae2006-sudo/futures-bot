"""
Multi-strategy optimizer CLI -- a thin wrapper around
``futures_bot.research.optimizer``, the one place the actual grid search,
train/validation split, and safety checks live (see that module's docstring).

This script's only remaining job is the piece ``research.optimizer`` doesn't
do on its own: sweep several strategies' default parameter grids in one run
and print a single leaderboard across all of them, ranked in-sample with the
out-of-sample column as the honest check. For a single strategy driven by
``config.yaml``'s own ``strategy_params``, prefer:

    python -m futures_bot.cli --config config.yaml --optimize data.csv

Usage:
    python optimize.py data_MES_full.csv
    python optimize.py data_MES_full.csv --strategy trend_pullback
    python optimize.py data_MES_full.csv --top 20
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from futures_bot.backtest.data import load_bars
from futures_bot.config import load_settings
from futures_bot.research.optimizer import OptimizationTrial, run_optimization
from futures_bot.strategy.base import StrategyRegistry
# Register all strategies.
from futures_bot.strategy import ema_crossover  # noqa: F401
from futures_bot.strategy import opening_range_breakout  # noqa: F401
from futures_bot.strategy import vwap_reversion  # noqa: F401
from futures_bot.strategy.trend_pullback import strategy as trend_pullback  # noqa: F401


# Default parameter grids, used only when a strategy isn't already carrying
# list-valued `strategy_params` in --config. Kept deliberately small per
# strategy: a coarse grid that finds a robust *region* is worth more than a
# fine grid that finds a lucky *point*. Expand only after a coarse pass shows
# something promising.
GRIDS = {
    "ema_crossover": {
        "fast_period": [5, 9, 12],
        "slow_period": [21, 30, 50],
    },
    "opening_range_breakout": {
        "range_minutes": [15, 30, 60],
        "min_range_points": [Decimal("2"), Decimal("4")],
        "require_close_beyond": [True, False],
    },
    "vwap_reversion": {
        "std_devs": [Decimal("1.5"), Decimal("2"), Decimal("2.5")],
        "min_bars": [15, 20, 30],
    },
    "trend_pullback": {
        "adx_min": [15, 20, 25],
        "rsi_long_min": [50, 55, 60],
        "atr_stop_mult": [1.0, 1.5, 2.0],
        "atr_target_mult": [2.0, 3.0],
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path)
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    ap.add_argument("--strategy", help="limit to one strategy (default: all)")
    ap.add_argument("--top", type=int, default=15, help="Combos to validate out-of-sample per strategy.")
    args = ap.parse_args()

    settings = load_settings(args.config)
    bars, _ = load_bars(args.csv)
    if not bars:
        print("No bars loaded.")
        return 1

    if args.strategy and args.strategy not in StrategyRegistry.names():
        print(f"Unknown strategy {args.strategy!r}. Registered: {', '.join(StrategyRegistry.names())}")
        return 1

    which = [args.strategy] if args.strategy else list(GRIDS)

    combos_tried = 0
    rows: list[tuple[str, OptimizationTrial]] = []
    for strat_name in which:
        grid = GRIDS.get(strat_name, {})
        print(f"Searching {strat_name} ({len(grid)} swept parameter(s))...")
        try:
            result = run_optimization(settings, strat_name, grid, bars, top_n=args.top)
        except ValueError as exc:
            print(f"  skip {strat_name}: {exc}")
            continue
        combos_tried += result.combos_tried
        rows.extend((strat_name, trial) for trial in result.ranked_trials)
        print(f"  {result.combos_tried} combo(s) tried, top {len(result.ranked_trials)} validated out-of-sample.")

    if not rows:
        print("No results.")
        return 1

    # Rank by IN-SAMPLE score across every strategy searched, then read
    # out-of-sample from the column already computed by `run_optimization`.
    rows.sort(key=lambda r: r[1].train_metrics.net_pnl, reverse=True)

    print(f"\nTried {combos_tried} combination(s) across {len(which)} strategy/strategies. "
          f"Judge ONLY by the Out P&L column.\n")

    print("=" * 100)
    print("TOP COMBINATIONS  (ranked in-sample; out-of-sample is the honest test)".center(100))
    print("=" * 100)
    print(f"{'Strategy':<24} {'In P&L':>10} {'In Trd':>7} {'Out P&L':>10} {'Out Trd':>8}  Params")
    print("-" * 100)
    for strat_name, trial in rows:
        params_str = ", ".join(f"{k}={v}" for k, v in trial.params.items())
        val = trial.validation_metrics
        out_pnl = f"{val.net_pnl:+.2f}" if val is not None else "n/a"
        out_trd = val.trade_count if val is not None else 0
        print(f"{strat_name:<24} {trial.train_metrics.net_pnl:>+10.2f} {trial.train_metrics.trade_count:>7} "
              f"{out_pnl:>10} {out_trd:>8}  {params_str}")

    out_positive = [
        r for r in rows if r[1].validation_metrics is not None and r[1].validation_metrics.net_pnl > 0
    ]
    print("\n" + "=" * 100)
    print("HOW TO READ THIS")
    print("=" * 100)
    print(f"  - Tried {combos_tried} combinations. The more you try, the more likely the best")
    print(f"    in-sample result is luck. Judge ONLY by the Out P&L column.")
    print(f"  - {len(out_positive)} of the {len(rows)} validated rows stayed positive out-of-sample.")
    if len(out_positive) == 0:
        print("    -> ZERO survived. No combination in this search has a real edge.")
        print("       That is the honest answer: stop, don't trade any of these.")
    elif len(out_positive) <= 2:
        print("    -> Only 1-2 survived. That is almost certainly luck, not edge --")
        print("       a real region shows up as a CLUSTER, not one or two points.")
    else:
        print("    -> Several survived. Check whether they share similar parameters")
        print("       (a robust region = believable) or are scattered (likely noise).")
        print("       Even then: confirm on MORE data before risking a dollar.")
    print("  - Out-of-sample trade counts under ~30 make that row's number noise")
    print("    regardless of the dollar figure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
