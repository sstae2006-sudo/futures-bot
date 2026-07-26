"""Backtest report formatting.

Ordered so the caveats are impossible to skip past on the way to the P&L. The
temptation with a favourable backtest is to read the headline and stop; putting
the reasons for doubt at the end, after the numbers, is how that happens.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..config import Settings
from .data import DataQualityReport
from .metrics import BacktestMetrics


def _money(value: Optional[Decimal]) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _pct(value: Optional[Decimal], places: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{places}f}%"


def _num(value: Optional[Decimal], places: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def plain_english_summary(metrics: BacktestMetrics, settings: Settings) -> list[str]:
    """Translates this specific backtest's numbers into a sentence each,
    rather than a generic glossary -- "Profit Factor: 1.25" means something
    different from "Profit Factor: 0.76", and the point is to read the
    number that's actually on the screen, not memorize a definition. Written
    for someone who has never seen these terms before; the numbers are
    already shown above in the normal report sections.
    """
    lines: list[str] = []

    pf = metrics.profit_factor
    if pf is not None:
        lines.append(
            f"Profit Factor ({pf:.2f}): for every $1 this strategy lost, it made "
            f"${pf:.2f} in gross profit. {'Above 1.00 means it made more than it lost.' if pf >= 1 else 'Below 1.00 means it lost money overall, before considering commission.'}"
        )

    if metrics.win_rate is not None:
        lines.append(
            f"Win Rate ({metrics.win_rate:.1f}%): {metrics.win_rate:.1f}% of trades closed profitable. "
            f"A low win rate is not automatically bad -- a strategy can win less often than it loses "
            f"and still make money, if its average win is bigger than its average loss (see Expectancy "
            f"below)."
        )

    if metrics.expectancy is not None:
        direction = "made" if metrics.expectancy >= 0 else "lost"
        lines.append(
            f"Expectancy ({_money(metrics.expectancy)}/trade): on average, this strategy {direction} "
            f"{_money(abs(metrics.expectancy))} per trade across all {metrics.trade_count} trades. This "
            f"is the single number that matters most for whether trading more of this strategy helps or "
            f"hurts -- a positive expectancy means size and time are on your side; a negative one means "
            f"they work against you."
        )

    if metrics.sharpe_ratio is not None:
        lines.append(
            f"Sharpe Ratio ({metrics.sharpe_ratio:.2f}, per trade): return per trade divided by how much "
            f"trade-to-trade results bounced around. Higher is smoother/more consistent for the same "
            f"average result; it says nothing about how big the numbers themselves are."
        )
    if metrics.sortino_ratio is not None:
        lines.append(
            f"Sortino Ratio ({metrics.sortino_ratio:.2f}, per trade): the same idea as Sharpe, but only "
            f"counts the bad swings (downside variance) against the strategy -- big wins don't get "
            f"penalized the way they do under Sharpe."
        )

    if metrics.max_drawdown > 0:
        pct = f" ({metrics.max_drawdown_pct:.0f}% of starting equity)" if metrics.max_drawdown_pct else ""
        lines.append(
            f"Maximum Drawdown ({_money(metrics.max_drawdown)}{pct}): the worst peak-to-trough dip the "
            f"account equity ever took during this run. This is the number that answers 'could I "
            f"actually have sat through this' -- not the average, the worst single stretch."
        )

    risk_per_trade = settings.risk_per_trade
    exp_r = metrics.expectancy_r(risk_per_trade)
    if exp_r is not None:
        made_or_lost = "made" if exp_r >= 0 else "lost"
        lines.append(
            f"Expectancy in R ({exp_r:+.2f}R): the same expectancy as above, but expressed as a multiple "
            f"of what was risked per trade (${risk_per_trade}) instead of a dollar amount. An R Multiple "
            f"of 2.0 on one trade means it made twice what was risked on it; on average, this strategy "
            f"{made_or_lost} {abs(exp_r):.2f}x its own per-trade risk. The advantage over a dollar figure: "
            f"it still means the same thing if the account size or position size changes."
        )

    return lines


def format_report(
    metrics: BacktestMetrics,
    settings: Settings,
    data_report: Optional[DataQualityReport] = None,
    width: int = 68,
) -> str:
    line = "=" * width
    thin = "-" * width
    out: list[str] = []

    out.append(line)
    out.append("BACKTEST RESULTS".center(width))
    out.append(line)

    spec = settings.contract_spec
    out.append(f"Contract      : {spec.symbol}   Strategy: {settings.strategy_name}")
    if metrics.first_bar and metrics.last_bar:
        span_days = (metrics.last_bar - metrics.first_bar).days
        out.append(
            f"Period        : {metrics.first_bar:%Y-%m-%d} to {metrics.last_bar:%Y-%m-%d}"
            f"  ({span_days} days, {metrics.bars_processed:,} bars)"
        )
    out.append(
        f"Risk settings : {settings.risk.stop_loss_points} pt stop / "
        f"{settings.risk.take_profit_points} pt target, "
        f"{settings.risk.contracts_per_trade} contract, "
        f"${settings.risk.daily_max_loss} daily limit"
    )
    out.append(
        f"Costs modelled: ${settings.broker.commission_per_side}/side, "
        f"{settings.broker.slippage_ticks} tick adverse slippage"
    )

    if data_report and data_report.warnings():
        out.append("")
        out.append("DATA QUALITY")
        out.append(thin)
        for w in data_report.warnings():
            out.append(f"  ! {w}")

    out.append("")
    out.append("PERFORMANCE")
    out.append(thin)
    out.append(f"  Net P&L            {_money(metrics.net_pnl):>16}   ({_pct(metrics.return_pct)})")
    out.append(f"  Gross P&L          {_money(metrics.gross_pnl):>16}")
    out.append(
        f"  Commission         {_money(-metrics.total_commission):>16}"
        f"   ({_pct(metrics.commission_drag_pct, 0)} of gross)"
    )
    out.append(f"  Starting equity    {_money(metrics.starting_equity):>16}")
    out.append(f"  Ending equity      {_money(metrics.ending_equity):>16}")

    out.append("")
    out.append("TRADES")
    out.append(thin)
    out.append(f"  Total              {metrics.trade_count:>16}")
    out.append(
        f"  Wins / losses      {len(metrics.wins)} / {len(metrics.losses):<12}"
        f"   (win rate {_pct(metrics.win_rate)})"
    )
    out.append(f"  Profit factor      {_num(metrics.profit_factor):>16}")
    out.append(f"  Expectancy/trade   {_money(metrics.expectancy):>16}")
    out.append(f"  Average win        {_money(metrics.average_win):>16}")
    out.append(f"  Average loss       {_money(metrics.average_loss and -metrics.average_loss):>16}")
    out.append(f"  Largest win        {_money(metrics.largest_win):>16}")
    out.append(f"  Largest loss       {_money(metrics.largest_loss):>16}")
    out.append(f"  Max consec. losses {metrics.max_consecutive_losses:>16}")
    out.append(f"  Max consec. wins   {metrics.max_consecutive_wins:>16}")
    duration = metrics.average_trade_duration
    if duration is not None:
        minutes = duration.total_seconds() / 60
        out.append(f"  Avg trade duration {f'{minutes:.0f} min':>16}")

    out.append("")
    out.append("RISK-ADJUSTED")
    out.append(thin)
    out.append(f"  Sharpe (per trade) {_num(metrics.sharpe_ratio):>16}")
    out.append(f"  Sortino (per trade){_num(metrics.sortino_ratio):>16}")
    risk_per_trade = settings.risk_per_trade
    avg_r = metrics.average_r_multiple(risk_per_trade)
    exp_r = metrics.expectancy_r(risk_per_trade)
    out.append(f"  Average R-multiple {_num(avg_r):>16}   (vs ${risk_per_trade} configured risk)")
    out.append(f"  Expectancy (R)     {_num(exp_r):>16}")
    if settings.strategy_name in ("trend_pullback",):
        out.append(
            "    Note: this strategy sizes stops from ATR, so per-trade risk varies -- "
            "R-multiples above approximate using the configured stop_loss_points instead."
        )

    out.append("")
    out.append("RISK")
    out.append(thin)
    out.append(
        f"  Max drawdown       {_money(metrics.max_drawdown):>16}"
        f"   ({_pct(metrics.max_drawdown_pct, 0)} of equity)"
    )
    if metrics.largest_win_share_pct is not None:
        out.append(f"  Best trade share   {_pct(metrics.largest_win_share_pct, 0):>16}   of net profit")
    out.append(f"  Signals blocked    {metrics.blocked_signals:>16}   by risk rules")
    if metrics.strategy_errors:
        out.append(f"  Strategy errors    {metrics.strategy_errors:>16}   forced to HOLD -- see caveats")

    if metrics.session_summaries:
        out.append("")
        out.append("SESSIONS")
        out.append(thin)
        summaries = metrics.session_summaries
        stopped_on_profit = sum(1 for s in summaries if s.stopped_on_profit)
        stopped_on_loss = sum(1 for s in summaries if s.stopped_on_loss)
        stopped_on_streak = sum(1 for s in summaries if s.stopped_on_consecutive_losses)
        missed = sum(s.missed_opportunities for s in summaries)
        out.append(f"  Session days       {len(summaries):>16}")
        out.append(f"  Stopped on target  {stopped_on_profit:>16}")
        out.append(f"  Stopped on loss    {stopped_on_loss:>16}")
        if any(s.halt_category == "consecutive_losses" for s in summaries):
            out.append(f"  Stopped on streak  {stopped_on_streak:>16}   consecutive-loss cap")
        if missed:
            out.append(f"  Missed after stop  {missed:>16}   entries blocked once halted")

    if metrics.exit_reasons:
        out.append("")
        out.append("EXITS")
        out.append(thin)
        for reason, count in metrics.exit_reasons.items():
            pct = count / metrics.trade_count * 100 if metrics.trade_count else 0
            out.append(f"  {reason:<26} {count:>5}   ({pct:.0f}%)")

    if metrics.by_side:
        out.append("")
        out.append("BY DIRECTION")
        out.append(thin)
        for side, stats in metrics.by_side.items():
            out.append(
                f"  {side:<8} {stats['count']:>4} trades   "
                f"{_money(stats['net_pnl']):>12}   win rate {stats['win_rate']:.1f}%"
            )

    if metrics.pnl_by_weekday:
        out.append("")
        out.append("PROFIT BY WEEKDAY")
        out.append(thin)
        for day, pnl in metrics.pnl_by_weekday.items():
            out.append(f"  {day:<10} {_money(pnl):>12}")

    if metrics.pnl_by_hour:
        out.append("")
        out.append("PROFIT BY HOUR (entry time, bar timezone)")
        out.append(thin)
        for hour, pnl in metrics.pnl_by_hour.items():
            out.append(f"  {hour:02d}:00      {_money(pnl):>12}")

    if metrics.pnl_by_month:
        out.append("")
        out.append("PROFIT BY MONTH")
        out.append(thin)
        for month, pnl in metrics.pnl_by_month.items():
            out.append(f"  {month:<10} {_money(pnl):>12}")

    if metrics.trades:
        out.append("")
        out.append("TRADE DISTRIBUTION")
        out.append(thin)
        out.extend(_pnl_histogram(metrics, width))

    if metrics.trades:
        out.append("")
        out.append("WHAT THESE NUMBERS MEAN")
        out.append(thin)
        for line_text in plain_english_summary(metrics, settings):
            out.extend(_wrap(f"  {line_text}", width))

    caveats = metrics.caveats()
    if caveats:
        out.append("")
        out.append("READ THIS BEFORE BELIEVING THE NUMBERS ABOVE")
        out.append(thin)
        for c in caveats:
            wrapped = _wrap(f"  - {c}", width)
            out.extend(wrapped)

    out.append(line)
    return "\n".join(out)


def _pnl_histogram(metrics: BacktestMetrics, width: int, buckets: int = 8) -> list[str]:
    """A text bucket-count histogram of per-trade net P&L.

    Bucket boundaries are computed as evenly-spaced *floats* even though the
    P&L values are Decimal -- histogram bucketing is display-only and doesn't
    feed back into any money calculation, so the float rounding here can't
    silently affect the report's actual numbers.
    """
    values = [float(t.net_pnl) for t in metrics.trades]
    lo, hi = min(values), max(values)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    span = hi - lo
    step = span / buckets

    counts = [0] * buckets
    for v in values:
        idx = int((v - lo) / step)
        idx = min(idx, buckets - 1)
        counts[idx] += 1

    max_count = max(counts) or 1
    bar_width = max(10, width - 26)
    lines = []
    for i, count in enumerate(counts):
        bucket_lo = lo + i * step
        bucket_hi = bucket_lo + step
        bar = "#" * max(1, round(count / max_count * bar_width)) if count else ""
        label = f"{bucket_lo:>7.0f}..{bucket_hi:<7.0f}"
        lines.append(f"  {label} {bar:<{bar_width}} {count}")
    return lines


def _wrap(text: str, width: int, indent: str = "    ") -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = indent + word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
