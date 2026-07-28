"""Context OFF vs ENABLED A/B comparison.

Runs the same strategy/parameters/dataset/date-range **twice** through the
exact same :func:`~futures_bot.backtest.runner.run_backtest` /
:class:`~futures_bot.engine.TradingEngine` execution path every other
backtest already uses -- no duplicate pipeline, no second replay loop:

1. Once in :attr:`~futures_bot.engine.ContextMode.OBSERVE` -- **decision-
   identical to OFF** by construction (``OBSERVE`` never sets
   ``Strategy.context``, so nothing a strategy does can differ; see
   ``ContextMode``'s own docstring and
   ``tests/test_engine_context_integration.py`` for the proof), but every
   trade carries its entry ``MarketContext`` -- exactly the annotation this
   comparison needs to explain *why* something changed, without a third,
   redundant "bare OFF" run.
2. Once in :attr:`~futures_bot.engine.ContextMode.ENABLED` -- the strategy
   *may* consult ``self.context``, if it declared ``uses_context = True``.
   For any strategy that hasn't, this run is decision-identical to the
   first one too (see ``ContextMode.ENABLED``'s own docstring) -- running
   an existing, unmodified strategy through this comparison is expected to
   (and, per the tests, does) report zero changed trades.

The two runs' trade lists are diffed and every changed trade is classified
(``REMOVED_BY_CONTEXT``, ``ADDED_BY_CONTEXT``, ``ENTERED_DIFFERENTLY``,
``EXITED_DIFFERENTLY``), each carrying the ``MarketContext``/
``EnvironmentScore`` that was in effect at the relevant entry, so it's
clear *why* the context-enabled run behaved differently.

**A path-dependence caveat, inherent to comparing two sequential,
stateful trading runs, not a defect in the diff logic below:** only the
*first* point where the two runs diverge is guaranteed to be directly
explained by whatever the context-aware strategy's own rule was. Once one
run skips an entry the other took, the two runs' open-position timelines
can drift apart -- the skipping run stays flat and may re-evaluate (and
enter on) bars the other run spent "busy" holding a position through.
Every trade after that first divergence is a *downstream consequence* of
it, not necessarily independently explained by the same rule. Treat the
diff as an accurate trade-by-trade record of what changed and its
associated context, not as proof that every single change shares one
root cause.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional, Sequence

from ..config import Settings
from ..context import EnvironmentScore, MarketContext
from ..engine import ContextMode
from ..models import Bar, Trade
from ..strategy.base import Strategy
from .metrics import BacktestMetrics
from .runner import run_backtest


class TradeChangeKind(str, Enum):
    """How one trade differs (or doesn't) between the baseline and
    context-enabled runs."""

    UNCHANGED = "unchanged"
    REMOVED_BY_CONTEXT = "removed_by_context"
    ADDED_BY_CONTEXT = "added_by_context"
    ENTERED_DIFFERENTLY = "entered_differently"
    EXITED_DIFFERENTLY = "exited_differently"


@dataclass(frozen=True)
class TradeChange:
    """One entry in the trade-level diff. ``baseline_trade``/
    ``enabled_trade`` are ``None`` exactly when the trade doesn't exist on
    that side (``ADDED_BY_CONTEXT``/``REMOVED_BY_CONTEXT``). ``market_context``/
    ``environment_score`` are whichever side's entry context is available
    (the context-enabled trade's, or the baseline's for a removed trade --
    both runs generate and attach context, since both run in ``OBSERVE``/
    ``ENABLED``, never bare ``OFF``)."""

    kind: TradeChangeKind
    baseline_trade: Optional[Trade]
    enabled_trade: Optional[Trade]
    market_context: Optional[MarketContext]
    environment_score: Optional[EnvironmentScore]
    explanation: str


@dataclass(frozen=True)
class MetricsSummary:
    """The specific figures this comparison reports, read directly off an
    existing :class:`~futures_bot.backtest.metrics.BacktestMetrics` --
    nothing here is recomputed independently."""

    net_profit: Decimal
    win_rate: Optional[Decimal]
    profit_factor: Optional[Decimal]
    expectancy: Optional[Decimal]
    max_drawdown: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    average_trade: Optional[Decimal]
    average_winner: Optional[Decimal]
    average_loser: Optional[Decimal]
    largest_winner: Optional[Decimal]
    largest_loser: Optional[Decimal]

    @classmethod
    def from_metrics(cls, metrics: BacktestMetrics) -> "MetricsSummary":
        return cls(
            net_profit=metrics.net_pnl,
            win_rate=metrics.win_rate,
            profit_factor=metrics.profit_factor,
            expectancy=metrics.expectancy,
            max_drawdown=metrics.max_drawdown,
            total_trades=metrics.trade_count,
            winning_trades=len(metrics.wins),
            losing_trades=len(metrics.losses),
            average_trade=metrics.expectancy,
            average_winner=metrics.average_win,
            average_loser=metrics.average_loss,
            largest_winner=metrics.largest_win,
            largest_loser=metrics.largest_loss,
        )


@dataclass(frozen=True)
class ContextComparisonReport:
    """The complete OFF-vs-ENABLED comparison: both runs' metrics side by
    side, plus the full trade-level diff."""

    baseline: MetricsSummary
    with_context: MetricsSummary
    changes: tuple[TradeChange, ...]

    @property
    def trades_changed(self) -> int:
        return sum(1 for c in self.changes if c.kind is not TradeChangeKind.UNCHANGED)


def _match_key(trade: Trade) -> tuple:
    return (trade.side, trade.entry_time)


def _context_of(trade: Optional[Trade]) -> tuple[Optional[MarketContext], Optional[EnvironmentScore]]:
    if trade is None or trade.entry_context is None:
        return None, None
    return trade.entry_context, trade.entry_context.environment_score


def _diff_trades(baseline: Sequence[Trade], enabled: Sequence[Trade]) -> list[TradeChange]:
    """Classifies every trade as unchanged, removed/added by context, or
    entered/exited differently.

    Pass 1 matches trades with the exact same ``(side, entry_time)`` --
    the strongest possible signal that "this is the same trade" -- and
    compares their exits. Pass 2 pairs up whatever's left, per side, in
    chronological order, as the best available explanation for "entered
    differently" (a heuristic: this framework has no other way to know
    that a context-enabled entry at 09:07 instead of 09:05 is 'the same
    trade, shifted' rather than two unrelated trades, so nearest-in-time
    same-side pairing is the most defensible reading). Anything left after
    both passes has no plausible counterpart at all -- a genuine add/remove.
    """
    changes: list[TradeChange] = []

    baseline_by_key: dict[tuple, list[Trade]] = {}
    for t in baseline:
        baseline_by_key.setdefault(_match_key(t), []).append(t)
    enabled_by_key: dict[tuple, list[Trade]] = {}
    for t in enabled:
        enabled_by_key.setdefault(_match_key(t), []).append(t)

    matched_baseline_ids: set[int] = set()
    matched_enabled_ids: set[int] = set()

    for key, b_list in baseline_by_key.items():
        e_list = enabled_by_key.get(key, [])
        for b, e in zip(b_list, e_list):
            matched_baseline_ids.add(id(b))
            matched_enabled_ids.add(id(e))
            market_context, environment_score = _context_of(e) if e.entry_context else _context_of(b)
            if (b.exit_time, b.exit_price, b.exit_reason) == (e.exit_time, e.exit_price, e.exit_reason):
                changes.append(TradeChange(
                    TradeChangeKind.UNCHANGED, b, e, market_context, environment_score,
                    "Identical entry and exit.",
                ))
            else:
                changes.append(TradeChange(
                    TradeChangeKind.EXITED_DIFFERENTLY, b, e, market_context, environment_score,
                    f"Same entry ({b.side.value} @ {b.entry_time}), different exit: "
                    f"{b.exit_reason} @ {b.exit_price} (baseline) vs {e.exit_reason} @ {e.exit_price} (context-enabled).",
                ))

    remaining_baseline = [t for t in baseline if id(t) not in matched_baseline_ids]
    remaining_enabled = [t for t in enabled if id(t) not in matched_enabled_ids]

    remaining_baseline_by_side: dict = {}
    for t in remaining_baseline:
        remaining_baseline_by_side.setdefault(t.side, []).append(t)
    remaining_enabled_by_side: dict = {}
    for t in remaining_enabled:
        remaining_enabled_by_side.setdefault(t.side, []).append(t)

    still_unmatched_baseline: list[Trade] = []
    still_unmatched_enabled: list[Trade] = []
    for side in set(remaining_baseline_by_side) | set(remaining_enabled_by_side):
        b_list = sorted(remaining_baseline_by_side.get(side, []), key=lambda t: t.entry_time)
        e_list = sorted(remaining_enabled_by_side.get(side, []), key=lambda t: t.entry_time)
        for b, e in zip(b_list, e_list):
            market_context, environment_score = _context_of(e) if e.entry_context else _context_of(b)
            changes.append(TradeChange(
                TradeChangeKind.ENTERED_DIFFERENTLY, b, e, market_context, environment_score,
                f"Same side ({side.value}), different entry time: "
                f"{b.entry_time} (baseline) vs {e.entry_time} (context-enabled).",
            ))
        still_unmatched_baseline.extend(b_list[len(e_list):])
        still_unmatched_enabled.extend(e_list[len(b_list):])

    for b in still_unmatched_baseline:
        market_context, environment_score = _context_of(b)
        changes.append(TradeChange(
            TradeChangeKind.REMOVED_BY_CONTEXT, b, None, market_context, environment_score,
            f"Baseline entered {b.side.value} @ {b.entry_time}; the context-enabled run did not take this trade.",
        ))
    for e in still_unmatched_enabled:
        market_context, environment_score = _context_of(e)
        changes.append(TradeChange(
            TradeChangeKind.ADDED_BY_CONTEXT, None, e, market_context, environment_score,
            f"The context-enabled run entered {e.side.value} @ {e.entry_time}; the baseline did not take this trade.",
        ))

    changes.sort(key=lambda c: (c.baseline_trade or c.enabled_trade).entry_time)
    return changes


def compare_context_impact(
    settings: Settings,
    strategy_factory: Callable[[], Strategy],
    bars: Sequence[Bar],
    **run_backtest_kwargs,
) -> ContextComparisonReport:
    """Runs ``strategy_factory()`` twice over the same ``bars`` -- once
    ``OBSERVE`` (the baseline), once ``ENABLED`` -- and returns the full
    comparison.

    ``strategy_factory`` is a callable, not a single ``Strategy`` instance,
    because a strategy commonly carries mutable state across a run (e.g. an
    "have I entered yet" flag) -- reusing one instance across both runs
    would let the second run start from wherever the first left off, an
    entirely avoidable correctness bug. Call it twice yourself
    (``strategy_factory()``) if you need to inspect either instance
    afterward.

    ``**run_backtest_kwargs`` is forwarded to both
    :func:`~futures_bot.backtest.runner.run_backtest` calls (``journal_dir``,
    ``broker``, ``progress_callback``, ...) -- do not include
    ``context_mode``/``context_engine``/``strategy``/``bars``/``settings``,
    which this function already supplies.
    """
    baseline_metrics = run_backtest(
        settings, strategy_factory(), bars, context_mode=ContextMode.OBSERVE, **run_backtest_kwargs,
    )
    enabled_metrics = run_backtest(
        settings, strategy_factory(), bars, context_mode=ContextMode.ENABLED, **run_backtest_kwargs,
    )
    changes = _diff_trades(baseline_metrics.trades, enabled_metrics.trades)
    return ContextComparisonReport(
        baseline=MetricsSummary.from_metrics(baseline_metrics),
        with_context=MetricsSummary.from_metrics(enabled_metrics),
        changes=tuple(changes),
    )
