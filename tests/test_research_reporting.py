"""Tests for `research.reporting`: curve data, heatmap, best/worst
conditions, and parameter sensitivity."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.backtest.metrics import BacktestMetrics
from futures_bot.contracts import CME_TZ
from futures_bot.models import Side, Trade
from futures_bot.research.optimizer import OptimizationTrial
from futures_bot.research.reporting import (
    best_worst_days,
    best_worst_hours,
    drawdown_curve_data,
    equity_curve_data,
    format_advanced_report,
    format_heatmap_grid,
    parameter_sensitivity,
    weekday_hour_heatmap,
)


def make_trade(
    net: Decimal, when: datetime, side: Side = Side.LONG, commission: Decimal = Decimal("0")
) -> Trade:
    return Trade(
        side=side, quantity=1,
        entry_price=Decimal("7500"), exit_price=Decimal("7500") + net,
        entry_time=when, exit_time=when + timedelta(minutes=15),
        gross_pnl=net, commission=commission, exit_reason="take_profit",
    )


def make_metrics(trades: list[Trade], starting_equity: Decimal = Decimal("5000")) -> BacktestMetrics:
    first = trades[0].entry_time if trades else datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
    last = trades[-1].exit_time if trades else first
    return BacktestMetrics(
        trades=trades, starting_equity=starting_equity, bars_processed=100,
        first_bar=first, last_bar=last,
    )


class TestEquityAndDrawdownCurves:
    def test_equity_curve_starts_at_starting_equity(self):
        when = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
        metrics = make_metrics([make_trade(Decimal("10"), when)], starting_equity=Decimal("1000"))
        rows = equity_curve_data(metrics)
        assert rows[0]["trade_number"] == 0
        assert rows[0]["equity"] == Decimal("1000")
        assert rows[1]["equity"] == Decimal("1010")

    def test_equity_curve_length_matches_trade_count_plus_one(self):
        when = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
        trades = [make_trade(Decimal("5"), when + timedelta(hours=i)) for i in range(4)]
        rows = equity_curve_data(make_metrics(trades))
        assert len(rows) == 5

    def test_drawdown_curve_is_never_positive(self):
        when = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
        trades = [
            make_trade(Decimal("10"), when),
            make_trade(Decimal("-30"), when + timedelta(hours=1)),
            make_trade(Decimal("5"), when + timedelta(hours=2)),
        ]
        rows = drawdown_curve_data(make_metrics(trades))
        assert all(r["drawdown"] <= 0 for r in rows)
        assert min(r["drawdown"] for r in rows) == Decimal("-30")

    def test_empty_trades_does_not_crash(self):
        metrics = make_metrics([])
        assert equity_curve_data(metrics)[0]["equity"] == metrics.starting_equity
        assert drawdown_curve_data(metrics) == [
            {"trade_number": 0, "timestamp": metrics.first_bar, "drawdown": Decimal("0")}
        ]


class TestWeekdayHourHeatmap:
    def test_buckets_by_day_and_hour(self):
        # A Tuesday 09:00 CT entry and a Wednesday 14:00 CT entry.
        t1 = make_trade(Decimal("10"), datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ))
        t2 = make_trade(Decimal("-5"), datetime(2026, 7, 22, 14, 0, tzinfo=CME_TZ))
        cells = weekday_hour_heatmap(make_metrics([t1, t2]))

        assert len(cells) == 2
        by_key = {(c.day_of_week, c.hour): c for c in cells}
        assert by_key[("Tuesday", 9)].net_pnl == Decimal("10")
        assert by_key[("Wednesday", 14)].net_pnl == Decimal("-5")

    def test_multiple_trades_same_bucket_aggregate(self):
        when = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
        trades = [make_trade(Decimal("10"), when), make_trade(Decimal("-4"), when + timedelta(days=7))]
        cells = weekday_hour_heatmap(make_metrics(trades))
        assert len(cells) == 1
        assert cells[0].net_pnl == Decimal("6")
        assert cells[0].trade_count == 2
        assert cells[0].win_rate == Decimal("50")

    def test_format_heatmap_grid_handles_empty(self):
        assert "No trades" in format_heatmap_grid([])

    def test_format_heatmap_grid_renders_known_values(self):
        when = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
        cells = weekday_hour_heatmap(make_metrics([make_trade(Decimal("123"), when)]))
        text = format_heatmap_grid(cells)
        assert "Tuesday" in text
        assert "123" in text


class TestBestWorstConditions:
    def test_best_worst_hours_ranked_correctly(self):
        base = datetime(2026, 7, 21, 0, 0, tzinfo=CME_TZ)
        trades = [
            make_trade(Decimal("100"), base.replace(hour=9)),
            make_trade(Decimal("-50"), base.replace(hour=14)),
            make_trade(Decimal("20"), base.replace(hour=10)),
        ]
        result = best_worst_hours(make_metrics(trades), top_n=2)
        assert result["best"][0].label == "09:00 CT"
        assert result["worst"][0].label == "14:00 CT"

    def test_best_worst_days_ranked_correctly(self):
        trades = [
            make_trade(Decimal("100"), datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)),  # Tuesday
            make_trade(Decimal("-50"), datetime(2026, 7, 24, 9, 0, tzinfo=CME_TZ)),  # Friday
        ]
        result = best_worst_days(make_metrics(trades), top_n=2)
        assert result["best"][0].label == "Tuesday"
        assert result["worst"][0].label == "Friday"

    def test_no_trades_returns_empty_lists(self):
        result = best_worst_hours(make_metrics([]))
        assert result == {"best": [], "worst": []}


def make_trial(params: dict, net_pnl: Decimal) -> OptimizationTrial:
    when = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
    metrics = make_metrics([make_trade(net_pnl, when)])
    return OptimizationTrial(params=params, train_metrics=metrics)


class TestParameterSensitivity:
    def test_reports_only_parameters_that_varied(self):
        trials = [
            make_trial({"fast": 3, "slow": 20}, Decimal("100")),
            make_trial({"fast": 5, "slow": 20}, Decimal("50")),
            make_trial({"fast": 8, "slow": 20}, Decimal("-10")),
        ]
        sensitivity = parameter_sensitivity(trials)
        assert "fast" in sensitivity
        assert "slow" not in sensitivity  # held fixed at 20 across every trial

    def test_averages_scores_by_value(self):
        trials = [
            make_trial({"fast": 3}, Decimal("100")),
            make_trial({"fast": 3}, Decimal("50")),
            make_trial({"fast": 8}, Decimal("-10")),
        ]
        sensitivity = parameter_sensitivity(trials)
        rows = {r["value"]: r for r in sensitivity["fast"]}
        assert rows[3]["avg_score"] == Decimal("75")
        assert rows[3]["count"] == 2
        assert rows[8]["avg_score"] == Decimal("-10")

    def test_empty_trials_returns_empty_dict(self):
        assert parameter_sensitivity([]) == {}


class TestFormatAdvancedReport:
    def test_includes_required_sections(self):
        trades = [
            make_trade(Decimal("100"), datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)),
            make_trade(Decimal("-30"), datetime(2026, 7, 22, 14, 0, tzinfo=CME_TZ)),
        ]
        text = format_advanced_report(make_metrics(trades))
        for section in ("WEEKDAY x HOUR", "BEST / WORST HOURS", "BEST / WORST DAYS"):
            assert section in text

    def test_includes_parameter_sensitivity_when_trials_given(self):
        trades = [make_trade(Decimal("10"), datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ))]
        trials = [make_trial({"fast": 3}, Decimal("10")), make_trial({"fast": 8}, Decimal("-5"))]
        text = format_advanced_report(make_metrics(trades), all_trials=trials)
        assert "PARAMETER SENSITIVITY" in text
        assert "fast" in text

    def test_no_trials_omits_sensitivity_section(self):
        trades = [make_trade(Decimal("10"), datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ))]
        text = format_advanced_report(make_metrics(trades))
        assert "PARAMETER SENSITIVITY" not in text
