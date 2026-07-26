"""Config-driven parameter grid search.

Generalizes the pattern the root `optimize.py` script already proved out
(search on a training slice, judge only on a held-out slice the search never
saw, report the distribution rather than just the peak, count how many
combinations were tried) into a reusable module driven by data rather than a
hardcoded ``GRIDS`` dict per strategy: any ``strategy_params`` value that is
a list becomes a sweep dimension, exactly the shape in the Phase 3 spec.

Every combination that completes a training backtest is kept in
``OptimizationResult.all_trials`` and, if a :class:`TradeStore` is given,
persisted to ``optimization_trials`` -- "record every configuration tested"
is a strict requirement, not just a report footnote, because a search that
only remembers its winner cannot be audited for how much of that win was
luck.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Callable, Optional, Sequence
from uuid import uuid4

from ..backtest.metrics import MIN_TRADES_FOR_SIGNIFICANCE, BacktestMetrics
from ..backtest.runner import rolling_walk_forward, run_backtest, split_bars
from ..config import Settings
from ..journal import LOGGER_NAME
from ..models import Bar
from ..strategy.base import StrategyRegistry
from .safety import SafetyReport, build_safety_report, check_commission_sensitivity
from .trade_store import TradeStore

# Explicitly imported here (not left to whatever the caller already
# imported) so `StrategyRegistry` is fully populated inside a
# `ProcessPoolExecutor` worker too. A spawned worker is a fresh
# interpreter that only imports what's needed to unpickle the function
# it's asked to call -- it does not inherit the parent process's already-
# imported state. Without this, every combo would fail in the worker with
# "unknown strategy" even though the identical call works fine in-process.
# `strategy/__init__.py` imports (and thereby registers) every bundled
# strategy; matches the single import `cli.py`/`api/services.py` use for
# the same reason, rather than each maintaining its own duplicate list.
from .. import strategy as _strategy  # noqa: F401

log = logging.getLogger(LOGGER_NAME)

ScoreKey = Callable[[BacktestMetrics], Decimal]


def score_by_net_pnl(m: BacktestMetrics) -> Decimal:
    return m.net_pnl


#: Sentinel for `score_by_profit_factor`'s all-wins case -- higher than any
#: real profit factor could plausibly reach, so an all-winning combo always
#: ranks above one with any real losses, without conflating it with the
#: zero-trades case below (see that function's docstring).
_ALL_WINS_PROFIT_FACTOR_SENTINEL = Decimal("999")


def score_by_profit_factor(m: BacktestMetrics) -> Decimal:
    """`BacktestMetrics.profit_factor` is `None` in two very different
    situations -- zero losing trades (excellent) and zero trades at all
    (worthless) -- because it's `gross_wins / gross_losses` and both leave
    `gross_losses == 0`. Conflating them (as an earlier version of this
    function did, always returning -1) ranked a genuinely all-winning combo
    as the worst possible result, tied with one that never traded at all.
    """
    if m.profit_factor is not None:
        return m.profit_factor
    if m.trade_count == 0:
        return Decimal("-1")
    return _ALL_WINS_PROFIT_FACTOR_SENTINEL


def expand_param_grid(strategy_params: dict) -> list[dict]:
    """Any list-valued entry is a sweep dimension (Cartesian product across
    all of them); scalar-valued entries are held fixed on every combination.

    ``{"range_minutes": [15, 30, 45], "min_range_points": 2}`` expands to
    three combinations, each with ``min_range_points: 2`` and one of the
    three ``range_minutes`` values -- the shape shown in the Phase 3 spec.

    Unsupported: a parameter whose *value* is itself list-shaped even when
    not being swept -- `trend_pullback`'s ``trading_sessions``, e.g.
    ``[["08:30","10:30"], ["13:30","15:00"]]`` -- can't be told apart from
    an actual sweep over those two session windows by shape alone, so
    rather than silently guessing (and previously always guessing "it's a
    sweep", which is wrong whenever the list was meant as one fixed
    compound value) this raises. There is currently no way to hold such a
    parameter fixed at a non-default value through the grid; exclude it
    from ``strategy_params`` to use the strategy's own default, or wait for
    a more explicit ``{"sweep": [...]}`` form.
    """
    fixed = {k: v for k, v in strategy_params.items() if not isinstance(v, list)}
    sweep_keys = [k for k, v in strategy_params.items() if isinstance(v, list)]

    nested = {k for k in sweep_keys if any(isinstance(v, (list, dict)) for v in strategy_params[k])}
    if nested:
        raise ValueError(
            f"Parameter(s) {sorted(nested)} have list values whose own elements are lists or dicts "
            f"(e.g. a session-window list like [['08:30','10:30'], ['13:30','15:00']]) -- refusing to "
            f"guess whether that's a sweep over those elements or one fixed compound value. Exclude "
            f"{sorted(nested)} from strategy_params to use the strategy's default instead."
        )

    empty_sweeps = [k for k in sweep_keys if not strategy_params[k]]
    if empty_sweeps:
        raise ValueError(f"Parameter(s) {empty_sweeps} have an empty list -- nothing to sweep.")

    if not sweep_keys:
        return [dict(fixed)]

    combos = []
    for values in itertools.product(*(strategy_params[k] for k in sweep_keys)):
        combo = dict(fixed)
        combo.update(zip(sweep_keys, values))
        combos.append(combo)
    return combos


@dataclass(frozen=True)
class OptimizationTrial:
    params: dict
    train_metrics: BacktestMetrics
    validation_metrics: Optional[BacktestMetrics] = None
    rank: Optional[int] = None


@dataclass(frozen=True)
class OptimizationResult:
    batch_id: str
    strategy: str
    contract: str
    combos_tried: int
    #: Every combination that completed a training backtest, sorted best
    #: training score first. Validation is only run for the top `top_n`.
    all_trials: list[OptimizationTrial]
    #: The top `top_n` trials, with `.validation_metrics` and `.rank` filled in.
    ranked_trials: list[OptimizationTrial]
    best: Optional[OptimizationTrial]
    safety: Optional[SafetyReport]
    #: How many of `combos_tried` were restored from a prior (possibly
    #: interrupted) run of this same `batch_id` rather than re-run --
    #: always 0 for a fresh batch id (every existing caller today, and
    #: anyone constructing this dataclass directly without passing it).
    combos_cached: int = 0


@dataclass(frozen=True)
class CachedTrialMetrics:
    """Stand-in for `BacktestMetrics`, used only for a combo restored from
    a prior (possibly interrupted) run of the same batch -- reconstructed
    from the handful of summary fields `trade_store.optimization_trials`
    persists, not a full re-run of the backtest.

    Deliberately narrow: exposes exactly the attributes every current
    consumer of a trial's *training* metrics reads --
    `research/safety.py`'s `check_trade_counts`/`check_degradation`/
    `check_unrealistic_gains`, `format_optimization_report`, and the API's
    `TrialOut` schema -- traced exhaustively, not a general `BacktestMetrics`
    substitute. `wins` is a list of the right *length* only (`[True] *
    win_count`), since every current use of `.wins` is a truthy/length
    check, never an individual `Trade`'s own fields. `expectancy` is
    derived (`net_pnl / trade_count`) rather than stored, since it needs
    nothing beyond what's already here.
    """

    trade_count: int
    net_pnl: Decimal
    profit_factor: Optional[Decimal]
    max_drawdown: Decimal
    gross_losses: Decimal
    wins: list
    largest_win: Optional[Decimal]

    @property
    def expectancy(self) -> Optional[Decimal]:
        if self.trade_count == 0:
            return None
        return self.net_pnl / Decimal(self.trade_count)

    @property
    def largest_win_share_pct(self) -> Optional[Decimal]:
        if self.net_pnl <= 0 or not self.wins or self.largest_win is None:
            return None
        return self.largest_win / self.net_pnl * 100

    @classmethod
    def from_stored_row(cls, row: dict) -> "CachedTrialMetrics":
        return cls(
            trade_count=row["train_trades"],
            net_pnl=row["train_net_pnl"],
            profit_factor=row["train_profit_factor"],
            max_drawdown=row["train_max_drawdown"],
            gross_losses=row["train_gross_losses"] or Decimal("0"),
            wins=[True] * (row["train_win_count"] or 0),
            largest_win=row["train_largest_win"],
        )


def _evaluate_combo(strategy_name: str, params: dict, settings: Settings, train_bars: Sequence[Bar]) -> BacktestMetrics:
    """Runs one combo's training backtest -- a plain module-level function
    (not a closure) so `ProcessPoolExecutor` can pickle a reference to it
    and call it in a worker process. Used identically for the sequential
    fallback (small grids -- see `run_optimization`), just called directly
    instead of through a pool. Raises straight through on an invalid combo
    (e.g. fast_period >= slow_period); the caller catches it the same way
    whether that's around a direct call or around a `Future.result()`.
    """
    strategy_cls = StrategyRegistry.get(strategy_name)
    strategy = strategy_cls(settings.contract_spec, **params)
    return run_backtest(settings, strategy, train_bars)


def _combo_key(params: dict) -> str:
    """Canonical dedup/resume lookup key -- must match
    `trade_store.TradeStore.insert_optimization_trial`'s `combo_key`
    encoding exactly, since a resumed run compares this function's output
    against what an earlier run persisted."""
    return json.dumps(params, sort_keys=True, default=str)


def _combine_window_metrics(windows: list[dict], starting_equity: Decimal) -> Optional[BacktestMetrics]:
    """Concatenates trades from disjoint, sequential rolling-walk-forward
    windows into one synthetic `BacktestMetrics`. Valid because the windows
    never overlap and are already in chronological order -- equivalent to
    having run one continuous backtest over their combined span."""
    if not windows:
        return None
    trades, bars_processed, blocked, errors, ambiguous = [], 0, 0, 0, 0
    for w in windows:
        m = w["metrics"]
        trades.extend(m.trades)
        bars_processed += m.bars_processed
        blocked += m.blocked_signals
        errors += m.strategy_errors
        ambiguous += m.ambiguous_bars
    return BacktestMetrics(
        trades=trades, starting_equity=starting_equity,
        ambiguous_bars=ambiguous, bars_processed=bars_processed,
        blocked_signals=blocked, strategy_errors=errors,
        first_bar=windows[0]["start"], last_bar=windows[-1]["end"],
    )


def run_optimization(
    settings: Settings,
    strategy_name: str,
    param_grid: dict,
    bars: Sequence[Bar],
    *,
    train_fraction: Decimal = Decimal("0.7"),
    top_n: int = 10,
    score_key: ScoreKey = score_by_net_pnl,
    rolling: bool = False,
    assess_commission_sensitivity: bool = True,
    store: Optional[TradeStore] = None,
    batch_id: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, Optional[dict]], None]] = None,
    max_workers: Optional[int] = None,
    min_trades: int = MIN_TRADES_FOR_SIGNIFICANCE,
) -> OptimizationResult:
    """Runs every combination in ``param_grid`` on the training slice, ranks
    by ``score_key``, and re-tests the top ``top_n`` on the held-out
    validation slice.

    ``min_trades`` (default: the same `MIN_TRADES_FOR_SIGNIFICANCE` threshold
    `BacktestMetrics.caveats()`/`research.safety.check_trade_counts` already
    use elsewhere) keeps a combo with too few training trades to mean
    anything from ever being ranked above one with a real sample size,
    regardless of ``score_key`` -- a single lucky trade can otherwise score
    higher than a genuinely good combo with hundreds of trades and a
    slightly lower ratio. Every combo is still kept in ``all_trials`` (never
    hidden, only ranked below the ones that clear the floor) and
    `research.safety.check_trade_counts` still separately warns on the
    winner's own sample size. Pass ``0`` to fully restore the previous
    score-only ranking.

    ``rolling=True`` validates each top candidate with
    `backtest.runner.rolling_walk_forward` over the validation slice
    (multiple windows, concatenated into one result) instead of a single
    static test -- a more robust out-of-sample estimate at the cost of
    roughly `top_n` times as many backtests as the default single split.

    A search is a batch of backtests against exactly the same risk/session/
    broker configuration in ``settings``; only ``strategy_params`` varies
    per combination.

    ``max_workers`` controls how many training backtests run at once via
    `concurrent.futures.ProcessPoolExecutor` -- ``None`` (the default) uses
    every available CPU core (`os.cpu_count()`). A grid with only one combo
    left to run, or ``max_workers=1``, skips the pool entirely and runs
    in-process, avoiding process-startup overhead for trivial grids.

    ``store`` + ``batch_id`` together enable resume: if ``batch_id``
    already has trials persisted from a prior (possibly interrupted) call,
    those combos are restored from the store instead of being re-run, and
    every trial newly computed in *this* call is persisted immediately as
    it completes (not batched at the end) -- so killing this process loses
    at most whatever was still in flight, and calling this again with the
    same ``batch_id`` continues rather than restarting. Every existing
    caller always generates a fresh ``batch_id`` per call, so resume is
    purely additive and invisible unless something deliberately reuses one.

    ``progress_callback``, if given, is called after each combination
    completes (including cached ones, restored near-instantly at the
    start, and ones whose training backtest was skipped for being invalid)
    as ``callback(combos_completed, combos_total, current_best)``, where
    ``combos_total`` is the *full* grid size (cached + freshly run) and
    ``current_best`` is ``{"params": dict, "score": Decimal}`` for the best
    trial seen *so far* by ``score_key``, or ``None`` before any trial has
    completed. Never called at all when omitted -- every existing caller is
    unaffected. Percent-complete and ETA are deliberately not part of this
    callback's payload -- both are trivially derivable by the consumer from
    ``(combos_completed, combos_total)`` plus its own wall-clock start time,
    so adding them here would just be duplicated bookkeeping.
    """
    strategy_cls = StrategyRegistry.get(strategy_name)
    combos = expand_param_grid(param_grid)
    if not combos:
        raise ValueError("Parameter grid produced no combinations to test.")
    #: `expand_param_grid`'s output order is deterministic for a given
    #: `param_grid` -- used only to break ties in the ranking sort below, so
    #: two combos that score identically (e.g. both zero trades) always rank
    #: in the same relative order on every rerun, regardless of which
    #: finished first in the `ProcessPoolExecutor` (wall-clock completion
    #: order is not reproducible and must never affect ranking).
    combo_order = {_combo_key(params): i for i, params in enumerate(combos)}

    train, validation_bars = split_bars(bars, train_fraction)
    batch_id = batch_id or uuid4().hex[:12]
    total = len(combos)

    cached_by_key: dict[str, dict] = store.fetch_completed_combos(batch_id) if store is not None else {}

    all_trials: list[OptimizationTrial] = []
    best_so_far: Optional[OptimizationTrial] = None
    combos_cached = 0
    done = 0

    def _note_best(trial: OptimizationTrial) -> None:
        nonlocal best_so_far
        if best_so_far is None or score_key(trial.train_metrics) > score_key(best_so_far.train_metrics):
            best_so_far = trial

    def _report_progress() -> None:
        if progress_callback is None:
            return
        current_best = (
            {"params": best_so_far.params, "score": score_key(best_so_far.train_metrics)}
            if best_so_far is not None else None
        )
        progress_callback(done, total, current_best)

    to_run: list[dict] = []
    for params in combos:
        row = cached_by_key.get(_combo_key(params))
        if row is None:
            to_run.append(params)
            continue
        trial = OptimizationTrial(params=params, train_metrics=CachedTrialMetrics.from_stored_row(row))
        all_trials.append(trial)
        combos_cached += 1
        _note_best(trial)
        done += 1
        _report_progress()

    def _persist_fresh(params: dict, metrics: BacktestMetrics) -> None:
        if store is None:
            return
        largest_win = max((t.net_pnl for t in metrics.wins), default=None) if metrics.wins else None
        store.insert_optimization_trial(
            batch_id=batch_id, strategy=strategy_name, params=params,
            train_trades=metrics.trade_count, train_net_pnl=metrics.net_pnl,
            train_profit_factor=metrics.profit_factor, train_max_drawdown=metrics.max_drawdown,
            train_gross_losses=metrics.gross_losses, train_win_count=len(metrics.wins),
            train_largest_win=largest_win,
        )

    def _handle_fresh_result(params: dict, metrics: Optional[BacktestMetrics], exc: Optional[BaseException]) -> None:
        nonlocal done
        done += 1
        if exc is not None:
            log.warning("Skipping %s %s during optimization: %s", strategy_name, params, exc)
            _report_progress()
            return
        trial = OptimizationTrial(params=params, train_metrics=metrics)
        all_trials.append(trial)
        _note_best(trial)
        _persist_fresh(params, metrics)  # continuous checkpointing -- see docstring
        _report_progress()

    resolved_workers = max_workers if max_workers is not None else (os.cpu_count() or 1)
    if len(to_run) > 1 and resolved_workers > 1:
        with ProcessPoolExecutor(max_workers=resolved_workers) as executor:
            future_to_params = {
                executor.submit(_evaluate_combo, strategy_name, params, settings, train): params
                for params in to_run
            }
            for future in as_completed(future_to_params):
                params = future_to_params[future]
                try:
                    metrics = future.result()
                except Exception as exc:  # noqa: BLE001
                    _handle_fresh_result(params, None, exc)
                else:
                    _handle_fresh_result(params, metrics, None)
    else:
        for params in to_run:
            try:
                metrics = _evaluate_combo(strategy_name, params, settings, train)
            except Exception as exc:  # noqa: BLE001
                _handle_fresh_result(params, None, exc)
            else:
                _handle_fresh_result(params, metrics, None)

    if not all_trials:
        raise ValueError(
            f"No parameter combination for {strategy_name!r} completed a training backtest "
            f"successfully, out of {len(combos)} tried."
        )

    all_trials.sort(
        key=lambda t: (
            t.train_metrics.trade_count >= min_trades,
            score_key(t.train_metrics),
            -combo_order[_combo_key(t.params)],
        ),
        reverse=True,
    )

    ranked_trials: list[OptimizationTrial] = []
    for i, trial in enumerate(all_trials[:top_n], start=1):
        def strategy_factory(p=trial.params):
            return strategy_cls(settings.contract_spec, **p)

        try:
            if rolling:
                windows = rolling_walk_forward(settings, strategy_factory, validation_bars)
                validation_metrics = _combine_window_metrics(windows, settings.broker.starting_cash)
            else:
                validation_metrics = run_backtest(settings, strategy_factory(), validation_bars)
        except Exception as exc:
            log.warning("Validation failed for %s %s: %s", strategy_name, trial.params, exc)
            validation_metrics = None

        ranked_trials.append(replace(trial, validation_metrics=validation_metrics, rank=i))

    best = ranked_trials[0] if ranked_trials else None

    safety = None
    if best is not None:
        commission_findings = None
        if assess_commission_sensitivity:
            def best_factory(p=best.params):
                return strategy_cls(settings.contract_spec, **p)
            commission_findings = check_commission_sensitivity(settings, best_factory, train)

        safety = build_safety_report(
            train=best.train_metrics,
            validation=best.validation_metrics,
            winner_score=score_key(best.train_metrics),
            all_scores=[score_key(t.train_metrics) for t in all_trials],
            commission_check=commission_findings,
        )

    if store is not None:
        # Training-phase rows for every trial (fresh or restored from
        # cache) already exist in the store by this point -- fresh ones
        # via `_persist_fresh` as they completed above, cached ones from
        # whichever earlier call originally computed them. This pass only
        # adds the validation/rank fields the top `top_n` just produced,
        # via UPDATE against the row that's already there.
        for trial in ranked_trials:
            v = trial.validation_metrics
            store.update_optimization_trial_validation(
                batch_id=batch_id, combo_key=_combo_key(trial.params),
                validation_trades=v.trade_count if v else None,
                validation_net_pnl=v.net_pnl if v else None,
                validation_profit_factor=v.profit_factor if v else None,
                validation_max_drawdown=v.max_drawdown if v else None,
                rank=trial.rank,
            )

    return OptimizationResult(
        batch_id=batch_id, strategy=strategy_name, contract=settings.contract,
        combos_tried=len(combos), combos_cached=combos_cached,
        all_trials=all_trials, ranked_trials=ranked_trials,
        best=best, safety=safety,
    )


def _money(value: Optional[Decimal]) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _num(value: Optional[Decimal]) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_optimization_report(result: OptimizationResult) -> str:
    """The report shape from the Phase 3 spec: best configuration, training
    vs. validation figures, confidence, and warnings."""
    if result.best is None:
        return "No configuration completed a backtest successfully."

    best = result.best
    lines: list[str] = ["BEST CONFIGURATION", ""]
    lines += ["Strategy:", f"  {result.strategy}", ""]
    lines += ["Parameters:"] + [f"  {k}: {v}" for k, v in best.params.items()] + [""]

    lines += [
        "Training:",
        f"  Profit Factor: {_num(best.train_metrics.profit_factor)}",
        f"  Net P&L: {_money(best.train_metrics.net_pnl)}",
        f"  Max Drawdown: {_money(best.train_metrics.max_drawdown)}",
        f"  Trades: {best.train_metrics.trade_count}",
        "",
    ]

    if best.validation_metrics is not None:
        lines += [
            "Validation:",
            f"  Profit Factor: {_num(best.validation_metrics.profit_factor)}",
            f"  Net P&L: {_money(best.validation_metrics.net_pnl)}",
            f"  Max Drawdown: {_money(best.validation_metrics.max_drawdown)}",
            f"  Trades: {best.validation_metrics.trade_count}",
            "",
        ]
    else:
        lines += ["Validation:", "  (not available -- see warnings)", ""]

    if result.safety is not None:
        lines += ["Confidence:", f"  {result.safety.confidence}", ""]
        lines += ["Warnings:"]
        if result.safety.findings:
            lines += [f"  - {f.message}" for f in result.safety.findings]
        else:
            lines += ["  (none)"]
        lines += [""]

    lines.append(
        f"{result.combos_tried} combination(s) tried; top {len(result.ranked_trials)} "
        f"re-tested out-of-sample. Judge only by the Validation numbers above."
    )
    return "\n".join(lines)
