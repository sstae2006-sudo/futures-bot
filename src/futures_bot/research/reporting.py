"""Advanced reporting: curve data, a weekday x hour heatmap, best/worst
conditions, and parameter sensitivity.

Built almost entirely from data `BacktestMetrics` already computes
(`equity_curve`, `pnl_by_weekday`, `pnl_by_hour`, `pnl_by_month`); this
module's job is exporting that as chart-ready rows and picking out the
extremes -- plus two things that don't exist yet: a weekday x hour matrix
finer than either axis alone (a strategy can look fine on Tuesdays only
because of one profitable Tuesday hour), and a parameter-sensitivity table
fed by an optimizer batch's trials.

Every function here answers a piece of "when does this strategy actually
work?" -- none of them run a new backtest or change any figure `metrics.py`
already reports; they only regroup and rank it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from ..backtest.metrics import BacktestMetrics
from ..contracts import to_ct
from ..models import Trade

if TYPE_CHECKING:
    from .optimizer import OptimizationTrial


def equity_curve_data(metrics: BacktestMetrics) -> list[dict]:
    """One row per trade: running equity after it closed, chart-ready."""
    curve = metrics.equity_curve
    rows: list[dict] = [{"trade_number": 0, "timestamp": metrics.first_bar, "equity": curve[0]}]
    for i, trade in enumerate(metrics.trades, start=1):
        rows.append({"trade_number": i, "timestamp": trade.exit_time, "equity": curve[i]})
    return rows


def drawdown_curve_data(metrics: BacktestMetrics) -> list[dict]:
    """Running peak-to-current drawdown (<=0) after each trade -- reads
    `BacktestMetrics.drawdown_curve` (the one shared computation
    `max_drawdown` and the HTML report's chart also use) and just adds the
    per-point trade_number/timestamp labels this API response needs."""
    rows: list[dict] = []
    for i, drawdown in enumerate(metrics.drawdown_curve):
        timestamp = metrics.trades[i - 1].exit_time if i > 0 else metrics.first_bar
        rows.append({"trade_number": i, "timestamp": timestamp, "drawdown": drawdown})
    return rows


@dataclass(frozen=True)
class HeatmapCell:
    day_of_week: str
    hour: int
    net_pnl: Decimal
    trade_count: int
    win_rate: Optional[Decimal]


_WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _group_trades(trades: Sequence[Trade], key_fn: Callable[[Trade], object]) -> dict:
    buckets: dict = {}
    for t in trades:
        buckets.setdefault(key_fn(t), []).append(t)
    return buckets


def _win_rate(trades: Sequence[Trade]) -> Optional[Decimal]:
    if not trades:
        return None
    wins = sum(1 for t in trades if t.net_pnl > 0)
    return Decimal(wins) / Decimal(len(trades)) * 100


def weekday_hour_heatmap(metrics: BacktestMetrics) -> list[HeatmapCell]:
    """Net P&L bucketed by (entry weekday, entry hour), CT."""
    buckets = _group_trades(metrics.trades, lambda t: (to_ct(t.entry_time).strftime("%A"), to_ct(t.entry_time).hour))
    cells = [
        HeatmapCell(
            day_of_week=day, hour=hour,
            net_pnl=sum((t.net_pnl for t in trades), Decimal("0")),
            trade_count=len(trades), win_rate=_win_rate(trades),
        )
        for (day, hour), trades in buckets.items()
    ]
    cells.sort(key=lambda c: (_WEEKDAY_ORDER.index(c.day_of_week), c.hour))
    return cells


def format_heatmap_grid(cells: Sequence[HeatmapCell]) -> str:
    """Text grid: rows are weekdays present in the data, columns are hours
    present in the data, cells are net P&L (blank where no trades happened)."""
    if not cells:
        return "No trades to build a heatmap from."

    days = sorted({c.day_of_week for c in cells}, key=_WEEKDAY_ORDER.index)
    hours = sorted({c.hour for c in cells})
    by_key = {(c.day_of_week, c.hour): c for c in cells}

    header = "        " + "".join(f"{h:>8d}" for h in hours)
    lines = [header]
    for day in days:
        row = f"{day[:8]:<8}"
        for hour in hours:
            cell = by_key.get((day, hour))
            row += f"{'.':>8}" if cell is None else f"{cell.net_pnl:>8.0f}"
        lines.append(row)
    return "\n".join(lines)


@dataclass(frozen=True)
class ConditionSummary:
    label: str
    net_pnl: Decimal
    trade_count: int
    win_rate: Optional[Decimal]


def _summarize(label: str, trades: Sequence[Trade]) -> ConditionSummary:
    return ConditionSummary(
        label=label, net_pnl=sum((t.net_pnl for t in trades), Decimal("0")),
        trade_count=len(trades), win_rate=_win_rate(trades),
    )


def best_worst_hours(metrics: BacktestMetrics, top_n: int = 3) -> dict[str, list[ConditionSummary]]:
    buckets = _group_trades(metrics.trades, lambda t: to_ct(t.entry_time).hour)
    summaries = sorted(
        (_summarize(f"{hour:02d}:00 CT", trades) for hour, trades in buckets.items()),
        key=lambda s: s.net_pnl, reverse=True,
    )
    return {"best": summaries[:top_n], "worst": list(reversed(summaries[-top_n:])) if summaries else []}


def best_worst_days(metrics: BacktestMetrics, top_n: int = 3) -> dict[str, list[ConditionSummary]]:
    buckets = _group_trades(metrics.trades, lambda t: to_ct(t.entry_time).strftime("%A"))
    summaries = sorted(
        (_summarize(day, trades) for day, trades in buckets.items()),
        key=lambda s: s.net_pnl, reverse=True,
    )
    return {"best": summaries[:top_n], "worst": list(reversed(summaries[-top_n:])) if summaries else []}


def parameter_sensitivity(
    all_trials: Sequence["OptimizationTrial"],
    score_key: Optional[Callable[[BacktestMetrics], Decimal]] = None,
) -> dict[str, list[dict]]:
    """For each parameter that actually varied across ``all_trials``,
    average/min/max training score grouped by that parameter's value --
    "does this parameter matter, and in which direction" at a glance.

    Parameters held fixed across every trial are omitted: there's nothing to
    show sensitivity *to* for a value that never changed.
    """
    if score_key is None:
        from .optimizer import score_by_net_pnl
        score_key = score_by_net_pnl

    if not all_trials:
        return {}

    param_keys = {key for t in all_trials for key in t.params}
    sensitivity: dict[str, list[dict]] = {}

    for key in sorted(param_keys):
        by_value: dict = {}
        for t in all_trials:
            if key not in t.params:
                continue
            by_value.setdefault(t.params[key], []).append(score_key(t.train_metrics))

        if len(by_value) < 2:
            continue  # didn't vary -- nothing to report sensitivity to

        rows = [
            {
                "value": value,
                "avg_score": sum(scores) / Decimal(len(scores)),
                "min_score": min(scores),
                "max_score": max(scores),
                "count": len(scores),
            }
            for value, scores in sorted(by_value.items(), key=lambda kv: str(kv[0]))
        ]
        sensitivity[key] = rows

    return sensitivity


def format_advanced_report(
    metrics: BacktestMetrics,
    all_trials: Optional[Sequence["OptimizationTrial"]] = None,
    width: int = 78,
) -> str:
    """Ties the pieces above together: "when does this strategy actually work?" """
    line = "=" * width
    thin = "-" * width
    out: list[str] = [line, "ADVANCED REPORT".center(width), line]

    cells = weekday_hour_heatmap(metrics)
    out += ["", "WEEKDAY x HOUR NET P&L (CT)", thin, format_heatmap_grid(cells)]

    hours = best_worst_hours(metrics)
    days = best_worst_days(metrics)

    def _fmt_conditions(title: str, summaries: list[ConditionSummary]) -> list[str]:
        rows = [f"  {title}", thin]
        if not summaries:
            rows.append("  (no trades)")
        for s in summaries:
            wr = f"{s.win_rate:.0f}%" if s.win_rate is not None else "n/a"
            rows.append(f"  {s.label:<12} ${s.net_pnl:>10,.2f}   {s.trade_count:>4} trades   win rate {wr}")
        return rows

    out += ["", "BEST / WORST HOURS"]
    out += _fmt_conditions("Best", hours["best"])
    out += _fmt_conditions("Worst", hours["worst"])

    out += ["", "BEST / WORST DAYS"]
    out += _fmt_conditions("Best", days["best"])
    out += _fmt_conditions("Worst", days["worst"])

    if all_trials:
        sensitivity = parameter_sensitivity(all_trials)
        out += ["", "PARAMETER SENSITIVITY (training score by value)", thin]
        if not sensitivity:
            out.append("  No parameter varied across the trials tried.")
        for key, rows in sensitivity.items():
            out.append(f"  {key}:")
            for row in rows:
                out.append(
                    f"    {row['value']!s:<10} avg {row['avg_score']:>+10.2f}   "
                    f"range [{row['min_score']:>+.2f}, {row['max_score']:>+.2f}]   ({row['count']} trial(s))"
                )

    out.append(line)
    return "\n".join(out)
