"""Context -> Strategy Phase 2, experiment 3: does gating `opening_range_breakout`
entries on `LiquidityState` (skip thin-liquidity breakouts) hold up?

Same pipeline as experiments 1-2 (`ContextGatedStrategy` ->
`compare_context_impact` -> `research.safety.build_safety_report`), shared
harness (`tools/context_experiment_lib.py`), a different strategy and
dimension. See `docs/EXPERIMENT_ORB_LIQUIDITY.md` for the write-up.

PRE-REGISTERED HYPOTHESIS (stated before this script was ever run):

    Opening Range Breakout entries without adequate volume confirmation are
    more prone to false breakouts -- a documented, generally-accepted
    failure mode for breakout systems (a break with no real participation
    behind it is more likely to fail than one accompanied by real volume).
    `context/liquidity.py`'s `LiquidityState` classifies current-bar volume
    relative to its own trailing average (THIN/NORMAL/DEEP). Requiring
    `LiquidityState != THIN` at the breakout bar is hypothesized to filter
    out the weakest, least-confirmed breaks and improve win rate or
    expectancy, at some cost in trade count.

This script does not decide whether the hypothesis is true -- it reports
whatever compare_context_impact/build_safety_report actually find.

Usage::

    .venv/Scripts/python.exe tools/experiment_context_orb_liquidity.py
"""

from __future__ import annotations

from pathlib import Path

from futures_bot.backtest.data import load_bars_from_db
from futures_bot.context import LiquidityState
from futures_bot.contracts import MES
from futures_bot.research.trade_store import TradeStore
from futures_bot.strategy.context_gate import ContextGatedStrategy, reject_if
from futures_bot.strategy.opening_range_breakout import OpeningRangeBreakout

from context_experiment_lib import make_mes_settings, print_report, run_experiment

JOURNAL_DIR = Path("logs/experiments/context_orb_liquidity")
EXPERIMENTS_DB = Path("research_experiments.db")

REASON = (
    "liquidity_state is THIN (below-average relative volume) at the breakout bar -- "
    "pre-registered hypothesis: thin-liquidity breakouts are more prone to false breaks"
)


def baseline_factory() -> OpeningRangeBreakout:
    """Unmodified opening_range_breakout, default parameters."""
    return OpeningRangeBreakout(MES)


def gated_factory() -> ContextGatedStrategy:
    return ContextGatedStrategy(
        MES,
        inner=OpeningRangeBreakout(MES),
        scorer=reject_if(lambda ctx: ctx.liquidity_state == LiquidityState.THIN, REASON),
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
        store=store, run_id_prefix="orb-liquidity", strategy_label="opening_range_breakout",
    )
    print_report(result)


if __name__ == "__main__":
    main()
