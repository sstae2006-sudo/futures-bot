"""Shared harness for Context -> Strategy validation experiments.

Every experiment script (see `experiment_context_trend_pullback_risk_state.py`
for the first one) defines its own baseline/gated `Strategy` factories and its
own pre-registered hypothesis text, then calls `run_experiment()` here to
execute the shared pipeline: `compare_context_impact` over the full dataset,
a chronological train/validation split run through `research/safety.py` for
both variants, and a context-mode-aware commission-sensitivity shock.
Factored out once a second experiment needed the same ~130-line harness the
first one already had -- this codebase's own "no duplicate code" rule
applied to its own research tooling. Not part of the installable package,
same as `tools/benchmark_context_engine.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional, Sequence

from futures_bot.api.analytics import compute_excursions
from futures_bot.backtest.context_comparison import ContextComparisonReport, compare_context_impact
from futures_bot.backtest.metrics import BacktestMetrics
from futures_bot.backtest.runner import CountingJournal, run_backtest, split_bars
from futures_bot.config import BrokerSettings, LoggingSettings, RiskSettings, Settings
from futures_bot.engine import ContextMode
from futures_bot.models import Bar
from futures_bot.research.features import build_trade_records
from futures_bot.research.regime import compute_regimes
from futures_bot.research.safety import Finding, SafetyReport, build_safety_report
from futures_bot.research.trade_store import TradeStore
from futures_bot.strategy.base import Strategy


def make_mes_settings(journal_dir: Path) -> Settings:
    """`config.yaml`'s real defaults -- not an arbitrary/tuned set, so every
    experiment starts from the same, honest baseline configuration."""
    return Settings(
        contract="MES",
        mode="paper",
        risk=RiskSettings(
            contracts_per_trade=1,
            stop_loss_points=Decimal("5"),
            take_profit_points=Decimal("10"),
            daily_max_loss=Decimal("300"),
            max_trades_per_session=20,
            account_size=Decimal("2500"),
        ),
        broker=BrokerSettings(starting_cash=Decimal("2500")),
        logging=LoggingSettings(directory=journal_dir, level="WARNING", log_every_decision=False),
    )


def commission_shock_finding(
    settings: Settings,
    factory: Callable[[], Strategy],
    bars: Sequence[Bar],
    context_mode: ContextMode,
    journal_dir: Path,
    multiplier: Decimal = Decimal("2"),
) -> list[Finding]:
    """The same check `research.safety.check_commission_sensitivity` does,
    but with `context_mode` threaded through -- that function always runs at
    the default `ContextMode.OFF`, which would silently test the
    *unfiltered* behavior for a context-gated factory (it never sees a
    context in OFF mode, so it never vetoes). Reimplemented here rather than
    editing `research/safety.py`, which is exercised by its own test suite
    and every optimizer run -- these experiment scripts are a separate,
    additive consumer, not a case for touching a shared, already-tested
    module."""
    baseline = run_backtest(settings, factory(), bars, journal_dir=journal_dir, context_mode=context_mode)
    if baseline.net_pnl <= 0 or baseline.trade_count == 0:
        return []

    shocked_broker = settings.broker.model_copy(
        update={"commission_per_side": settings.broker.commission_per_side * multiplier}
    )
    shocked_settings = settings.model_copy(update={"broker": shocked_broker})
    shocked = run_backtest(shocked_settings, factory(), bars, journal_dir=journal_dir, context_mode=context_mode)

    if shocked.net_pnl <= 0:
        return [Finding(
            f"At {multiplier}x commission, net P&L flips from ${baseline.net_pnl} to "
            f"${shocked.net_pnl} -- the edge is mostly a function of today's cost assumptions.",
            severe=True,
        )]
    drop = (Decimal("1") - (shocked.net_pnl / baseline.net_pnl)) * 100
    if drop > 50:
        return [Finding(
            f"At {multiplier}x commission, net P&L drops {drop:.0f}% (${baseline.net_pnl} -> "
            f"${shocked.net_pnl}) -- high sensitivity to trading costs.",
            severe=False,
        )]
    return []


def _run_and_maybe_persist(
    settings: Settings,
    factory: Callable[[], Strategy],
    bars: Sequence[Bar],
    context_mode: ContextMode,
    store: Optional[TradeStore],
    run_id: Optional[str],
    strategy_label: str,
) -> BacktestMetrics:
    """Runs one backtest slice, and -- only when both `store` and `run_id`
    are given -- persists its trades the same way
    `api/services.py::_persist_completed_run` does for a real API/CLI
    backtest run: `compute_excursions` (MAE/MFE, `api/analytics.py`) ->
    `compute_regimes` (`research/regime.py`) -> `build_trade_records`
    (`research/features.py`) -> `store.insert_trades`. Uses a
    `CountingJournal` (not the plain journal `run_backtest` defaults to)
    because `build_trade_records` needs the entry-decision records it
    buffers to join against closed trades.

    Deliberately does not also call `store.create_run`/`complete_run` --
    those exist for the Trade Explorer's run-summary view, which nothing
    here needs; the regime-performance and MAE/MFE queries this harness's
    callers want both read the `trades` table directly.
    """
    journal = CountingJournal(settings.logging.directory, settings.logging.log_every_decision)
    metrics = run_backtest(settings, factory(), bars, journal=journal, context_mode=context_mode)

    if store is not None and run_id is not None and metrics.trades:
        excursions = compute_excursions(metrics.trades, bars)
        regimes = compute_regimes(metrics.trades, bars)
        records = build_trade_records(
            metrics.trades, journal.entries,
            run_id=run_id, contract=settings.contract, strategy=strategy_label,
            strategy_params={},
            excursions=excursions, regimes=regimes,
        )
        store.insert_trades(records)

    return metrics


def format_metrics(label: str, m: BacktestMetrics) -> str:
    return (
        f"  {label}: trades={m.trade_count} net_pnl=${m.net_pnl} "
        f"win_rate={m.win_rate if m.win_rate is None else f'{m.win_rate:.1%}'} "
        f"profit_factor={m.profit_factor if m.profit_factor is None else f'{m.profit_factor:.2f}'} "
        f"expectancy={m.expectancy if m.expectancy is None else f'${m.expectancy:.2f}'} "
        f"max_drawdown=${m.max_drawdown}"
    )


@dataclass(frozen=True)
class ExperimentResult:
    comparison: ContextComparisonReport
    baseline_train: BacktestMetrics
    baseline_val: BacktestMetrics
    gated_train: BacktestMetrics
    gated_val: BacktestMetrics
    baseline_safety: SafetyReport
    gated_safety: SafetyReport


def run_experiment(
    *,
    settings: Settings,
    baseline_factory: Callable[[], Strategy],
    gated_factory: Callable[[], Strategy],
    bars: Sequence[Bar],
    journal_dir: Path,
    train_fraction: Decimal = Decimal("0.7"),
    store: Optional[TradeStore] = None,
    run_id_prefix: str = "expreplay",
    strategy_label: str = "unknown",
) -> ExperimentResult:
    """``store``/``run_id_prefix``/``strategy_label`` default to no-ops --
    every existing call site (every experiment script's first draft, and
    any future one that doesn't pass them) behaves exactly as before.
    When ``store`` is given, the four train/validation backtests below are
    persisted via `_run_and_maybe_persist`, tagged
    ``{run_id_prefix}-{baseline|gated}-{train|validation}`` -- distinct
    from any real live/paper/API-driven run_id, so an exploratory replay
    like this can never be mistaken for one. The full-dataset
    `compare_context_impact` comparison below is deliberately NOT
    persisted -- it's a different (non-split) backtest than the four that
    are, and the train/validation trades already give the regime-
    performance and MAE/MFE queries this exists for everything they need.
    """
    train_bars, validation_bars = split_bars(bars, train_fraction=train_fraction)

    comparison = compare_context_impact(settings, gated_factory, bars, journal_dir=journal_dir)

    def run_id(suffix: str) -> Optional[str]:
        return f"{run_id_prefix}-{suffix}" if store is not None else None

    baseline_train = _run_and_maybe_persist(
        settings, baseline_factory, train_bars, ContextMode.OFF,
        store, run_id("baseline-train"), strategy_label,
    )
    baseline_val = _run_and_maybe_persist(
        settings, baseline_factory, validation_bars, ContextMode.OFF,
        store, run_id("baseline-validation"), strategy_label,
    )
    gated_train = _run_and_maybe_persist(
        settings, gated_factory, train_bars, ContextMode.ENABLED,
        store, run_id("gated-train"), f"{strategy_label}+context_gate",
    )
    gated_val = _run_and_maybe_persist(
        settings, gated_factory, validation_bars, ContextMode.ENABLED,
        store, run_id("gated-validation"), f"{strategy_label}+context_gate",
    )

    baseline_commission = commission_shock_finding(settings, baseline_factory, bars, ContextMode.OFF, journal_dir)
    gated_commission = commission_shock_finding(settings, gated_factory, bars, ContextMode.ENABLED, journal_dir)

    baseline_safety = build_safety_report(
        train=baseline_train, validation=baseline_val, commission_check=baseline_commission,
    )
    gated_safety = build_safety_report(
        train=gated_train, validation=gated_val, commission_check=gated_commission,
    )

    return ExperimentResult(
        comparison=comparison,
        baseline_train=baseline_train, baseline_val=baseline_val,
        gated_train=gated_train, gated_val=gated_val,
        baseline_safety=baseline_safety, gated_safety=gated_safety,
    )


def print_report(result: ExperimentResult) -> None:
    c = result.comparison
    print("=== A/B comparison over the full dataset (compare_context_impact) ===")
    print(f"  baseline (unfiltered): trades={c.baseline.total_trades} "
          f"net_profit=${c.baseline.net_profit} win_rate={c.baseline.win_rate}")
    print(f"  gated:                 trades={c.with_context.total_trades} "
          f"net_profit=${c.with_context.net_profit} win_rate={c.with_context.win_rate}")
    print(f"  trades changed by the filter: {c.trades_changed}\n")

    print("=== Train/validation split, both variants (research/safety.py inputs) ===")
    print("Baseline (unfiltered):")
    print(format_metrics("train     ", result.baseline_train))
    print(format_metrics("validation", result.baseline_val))
    print("Gated:")
    print(format_metrics("train     ", result.gated_train))
    print(format_metrics("validation", result.gated_val))
    print()

    print("=== research/safety.py verdicts ===")
    print(f"Baseline confidence: {result.baseline_safety.confidence}")
    print(result.baseline_safety.format())
    print()
    print(f"Gated confidence: {result.gated_safety.confidence}")
    print(result.gated_safety.format())
