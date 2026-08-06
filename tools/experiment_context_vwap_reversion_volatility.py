"""Context -> Strategy Phase 2, experiment 4: does gating `vwap_reversion`
entries on `VolatilityState` (avoid HIGH/EXTREME volatility) hold up?

Same pipeline as experiments 1-3 (`ContextGatedStrategy` ->
`compare_context_impact` -> `research.safety.build_safety_report`), shared
harness (`tools/context_experiment_lib.py`). See
`docs/EXPERIMENT_VWAP_REVERSION_VOLATILITY.md` for the write-up.

PRE-REGISTERED HYPOTHESIS (stated before this script was ever run):

    vwap_reversion.py's own module docstring already states its weakness
    plainly: "it does badly precisely when a market trends hard in one
    direction all session... no parameter setting removes that." A strong
    volatility expansion (context/volatility.py's VolatilityState.HIGH or
    EXTREME -- current ATR running hot relative to its trailing average) is
    hypothesized to correlate with exactly the trending/expansion
    conditions this strategy is documented to fail in, since mean reversion
    assumes range-bound, orderly price action. Requiring
    `volatility_state not in (HIGH, EXTREME)` at entry is hypothesized to
    filter these out and improve win rate or expectancy, at some cost in
    trade count.

This script does not decide whether the hypothesis is true -- it reports
whatever compare_context_impact/build_safety_report actually find.

Usage::

    .venv/Scripts/python.exe tools/experiment_context_vwap_reversion_volatility.py
"""

from __future__ import annotations

from pathlib import Path

from futures_bot.backtest.data import load_bars_from_db
from futures_bot.context import VolatilityState
from futures_bot.contracts import MES
from futures_bot.research.trade_store import TradeStore
from futures_bot.strategy.context_gate import ContextGatedStrategy, reject_if
from futures_bot.strategy.vwap_reversion import VwapReversion

from context_experiment_lib import make_mes_settings, print_report, run_experiment

JOURNAL_DIR = Path("logs/experiments/context_vwap_reversion_volatility")
EXPERIMENTS_DB = Path("research_experiments.db")

REASON = (
    "volatility_state is HIGH/EXTREME -- pre-registered hypothesis: mean reversion "
    "underperforms during volatility expansion, per this strategy's own documented weakness"
)


def baseline_factory() -> VwapReversion:
    """Unmodified vwap_reversion, default parameters."""
    return VwapReversion(MES)


def gated_factory() -> ContextGatedStrategy:
    return ContextGatedStrategy(
        MES,
        inner=VwapReversion(MES),
        scorer=reject_if(
            lambda ctx: ctx.volatility_state in (VolatilityState.HIGH, VolatilityState.EXTREME), REASON
        ),
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
        store=store, run_id_prefix="vwap-reversion-volatility", strategy_label="vwap_reversion",
    )
    print_report(result)


if __name__ == "__main__":
    main()
