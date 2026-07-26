"""Multi-strategy comparison and leaderboard.

Almost pure aggregation: `BacktestMetrics` already computes every figure the
leaderboard needs (win rate, expectancy, Sharpe, Sortino, profit factor,
average trade, worst streak, monthly P&L) -- this module's job is running
several strategies side by side under an identical risk/session/broker
configuration and presenting the results ranked, not computing anything new.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional, Sequence

from ..backtest.metrics import BacktestMetrics
from ..backtest.runner import run_backtest
from ..config import Settings
from ..models import Bar
from ..strategy.base import StrategyRegistry

RankKey = Callable[[BacktestMetrics], Decimal]


@dataclass(frozen=True)
class ComparisonEntry:
    strategy: str
    strategy_params: dict
    metrics: BacktestMetrics


def rank_by_net_pnl(m: BacktestMetrics) -> Decimal:
    return m.net_pnl


def rank_by_profit_factor(m: BacktestMetrics) -> Decimal:
    """Undefined (no losing trades, or no trades) sorts last, not first --
    a profit factor of "infinite because nothing ever lost" is exactly the
    kind of number `BacktestMetrics.caveats()` already warns is suspicious,
    not a result that should win a leaderboard."""
    return m.profit_factor if m.profit_factor is not None else Decimal("-1")


def compare_strategies(
    settings: Settings,
    bars: Sequence[Bar],
    strategies: Sequence[tuple[str, dict]],
    rank_key: RankKey = rank_by_net_pnl,
    journal_root: Optional[Path] = None,
) -> list[ComparisonEntry]:
    """Runs each ``(strategy_name, strategy_params)`` pair over the same
    ``bars`` under the same ``settings.risk``/``session``/``broker``, so the
    comparison isolates the strategy rather than also varying the risk
    configuration underneath it. Returns entries ranked best-first by
    ``rank_key``.

    Each strategy's `decisions.jsonl` is written to its own subdirectory
    (under ``journal_root`` if given, otherwise a discarded temp directory)
    -- `DecisionJournal` appends, so strategies sharing one directory would
    otherwise interleave their decision logs into one unreadable file.
    """
    entries: list[ComparisonEntry] = []

    with tempfile.TemporaryDirectory(prefix="compare-journals-") as tmpdir:
        for name, params in strategies:
            strategy_cls = StrategyRegistry.get(name)
            strategy = strategy_cls(settings.contract_spec, **params)
            journal_dir = (journal_root or Path(tmpdir)) / name
            metrics = run_backtest(settings, strategy, bars, journal_dir=journal_dir)
            entries.append(ComparisonEntry(strategy=name, strategy_params=dict(params), metrics=metrics))

    entries.sort(key=lambda e: rank_key(e.metrics), reverse=True)
    return entries


def _money(value: Optional[Decimal]) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _pct(value: Optional[Decimal], places: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{places}f}%"


def _num(value: Optional[Decimal]) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_leaderboard(entries: Sequence[ComparisonEntry], width: int = 100) -> str:
    """Text leaderboard: rank, strategy, and the headline figures, followed
    by a per-strategy detail block for the numbers that don't fit a table
    row (worst streak, monthly breakdown)."""
    line = "=" * width
    thin = "-" * width
    out: list[str] = [line, "STRATEGY LEADERBOARD".center(width), line]

    if not entries:
        out.append("No strategies to compare.")
        out.append(line)
        return "\n".join(out)

    header = (
        f"{'#':>2}  {'Strategy':<24} {'Net P&L':>12} {'PF':>6} {'Drawdown':>12} "
        f"{'Trades':>7} {'Win%':>6} {'Sharpe':>7} {'Sortino':>8}"
    )
    out.append(header)
    out.append(thin)
    for rank, entry in enumerate(entries, start=1):
        m = entry.metrics
        out.append(
            f"{rank:>2}. {entry.strategy:<23} {_money(m.net_pnl):>12} {_num(m.profit_factor):>6} "
            f"{_money(m.max_drawdown):>12} {m.trade_count:>7} {_pct(m.win_rate, 0):>6} "
            f"{_num(m.sharpe_ratio):>7} {_num(m.sortino_ratio):>8}"
        )

    out.append("")
    out.append("DETAIL")
    out.append(thin)
    for rank, entry in enumerate(entries, start=1):
        m = entry.metrics
        out.append(f"{rank}. {entry.strategy}  {entry.strategy_params}")
        out.append(
            f"   Expectancy/trade {_money(m.expectancy)}   Avg win {_money(m.average_win)}   "
            f"Avg loss {_money(m.average_loss and -m.average_loss)}"
        )
        out.append(
            f"   Worst streak: {m.max_consecutive_losses} losses in a row   "
            f"Best streak: {m.max_consecutive_wins} wins in a row"
        )
        if m.pnl_by_month:
            months = "  ".join(f"{month}: {_money(pnl)}" for month, pnl in m.pnl_by_month.items())
            out.append(f"   Monthly: {months}")
        out.append("")

    out.append(line)
    return "\n".join(out)
