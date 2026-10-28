"""Tests for the metrics added on top of the original BacktestMetrics:
Sharpe/Sortino, R-multiples, win/loss streaks, and time-based breakdowns.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from futures_bot.backtest.metrics import BacktestMetrics
from futures_bot.contracts import CME_TZ
from futures_bot.models import Side, Trade


def trade(net: Decimal, when: datetime, side: Side = Side.LONG) -> Trade:
    return Trade(
        side=side, quantity=1,
        entry_price=Decimal("7500"), exit_price=Decimal("7500") + net / Decimal("5"),
        entry_time=when, exit_time=when + timedelta(minutes=30),
        gross_pnl=net, commission=Decimal("0"), exit_reason="take_profit",
    )


def ct(y, m, d, hh=10):
    return datetime(y, m, d, hh, 0, tzinfo=CME_TZ)


class TestStreaks:
    def test_max_consecutive_wins(self):
        pnls = ["10", "10", "-5", "10", "10", "10", "-5"]
        m = BacktestMetrics(trades=[trade(Decimal(p), ct(2026, 7, 21)) for p in pnls])
        assert m.max_consecutive_wins == 3

    def test_max_consecutive_losses_unaffected_by_wins_test(self):
        pnls = ["-10", "-10", "5", "-10"]
        m = BacktestMetrics(trades=[trade(Decimal(p), ct(2026, 7, 21)) for p in pnls])
        assert m.max_consecutive_losses == 2
        assert m.max_consecutive_wins == 1


class TestAverageTradeDuration:
    def test_none_with_no_trades(self):
        assert BacktestMetrics(trades=[]).average_trade_duration is None

    def test_computes_mean_duration(self):
        m = BacktestMetrics(trades=[trade(Decimal("10"), ct(2026, 7, 21))])
        assert m.average_trade_duration == timedelta(minutes=30)


class TestSharpeSortino:
    def test_none_with_fewer_than_two_trades(self):
        m = BacktestMetrics(trades=[trade(Decimal("10"), ct(2026, 7, 21))])
        assert m.sharpe_ratio is None

    def test_positive_sharpe_for_consistently_profitable_trades(self):
        pnls = ["10", "12", "9", "11", "10"]
        m = BacktestMetrics(trades=[trade(Decimal(p), ct(2026, 7, 21)) for p in pnls])
        assert m.sharpe_ratio > 0

    def test_negative_sharpe_for_consistently_losing_trades(self):
        pnls = ["-10", "-12", "-9", "-11", "-10"]
        m = BacktestMetrics(trades=[trade(Decimal(p), ct(2026, 7, 21)) for p in pnls])
        assert m.sharpe_ratio < 0

    def test_sortino_none_without_any_losses(self):
        pnls = ["10", "12", "9"]
        m = BacktestMetrics(trades=[trade(Decimal(p), ct(2026, 7, 21)) for p in pnls])
        assert m.sortino_ratio is None

    def test_sortino_ignores_upside_volatility(self):
        """A wild winner shouldn't drag Sortino down the way it would Sharpe."""
        steady = [trade(Decimal(p), ct(2026, 7, 21)) for p in ("10", "-5", "10", "-5", "10")]
        with_outlier_win = steady + [trade(Decimal("200"), ct(2026, 7, 22))]

        m_steady = BacktestMetrics(trades=steady)
        m_outlier = BacktestMetrics(trades=with_outlier_win)
        # Sortino should not collapse just because upside variance increased.
        assert m_outlier.sortino_ratio is not None
        assert m_outlier.sortino_ratio > 0


class TestDailyReturns:
    """`BacktestMetrics.daily_returns()` -- professional-research-pipeline
    plan Phase 1b. Net P&L bucketed by CME session date, not per-trade."""

    def test_empty_with_no_trades(self):
        assert BacktestMetrics(trades=[]).daily_returns() == []

    def test_multiple_trades_same_day_sum_into_one_bucket(self):
        m = BacktestMetrics(trades=[
            trade(Decimal("10"), ct(2026, 7, 21, 9)),
            trade(Decimal("-4"), ct(2026, 7, 21, 11)),
            trade(Decimal("6"), ct(2026, 7, 21, 13)),
        ])
        assert m.daily_returns() == [Decimal("12")]

    def test_trades_across_multiple_days_produce_ordered_buckets(self):
        m = BacktestMetrics(trades=[
            trade(Decimal("10"), ct(2026, 7, 21)),
            trade(Decimal("-5"), ct(2026, 7, 22)),
            trade(Decimal("3"), ct(2026, 7, 23)),
        ])
        assert m.daily_returns() == [Decimal("10"), Decimal("-5"), Decimal("3")]

    def test_a_fill_after_session_close_rolls_to_the_next_session_date(self):
        """A trade entered at 18:00 CT (after the 17:00 CT session open)
        belongs to the next CME trade date -- see contracts.session_date."""
        from futures_bot.contracts import session_date

        late_entry = ct(2026, 7, 21, 18)
        early_next_day = ct(2026, 7, 22, 9)
        m = BacktestMetrics(trades=[trade(Decimal("10"), late_entry), trade(Decimal("5"), early_next_day)])
        assert session_date(late_entry) == session_date(early_next_day)
        assert m.daily_returns() == [Decimal("15")]  # both fall in the same session bucket


class TestAnnualizedSharpe:
    """`BacktestMetrics.annualized_sharpe_ratio` -- distinct from the
    existing, deliberately un-annualized `sharpe_ratio`."""

    def test_none_with_fewer_than_two_trading_days(self):
        m = BacktestMetrics(trades=[trade(Decimal("10"), ct(2026, 7, 21))])
        assert m.annualized_sharpe_ratio is None

    def test_none_with_no_trades(self):
        assert BacktestMetrics(trades=[]).annualized_sharpe_ratio is None

    def test_positive_for_consistently_profitable_days(self):
        m = BacktestMetrics(trades=[
            trade(Decimal("10"), ct(2026, 7, 21)),
            trade(Decimal("12"), ct(2026, 7, 22)),
            trade(Decimal("9"), ct(2026, 7, 23)),
            trade(Decimal("11"), ct(2026, 7, 24)),
        ])
        assert m.annualized_sharpe_ratio > 0

    def test_negative_for_consistently_losing_days(self):
        m = BacktestMetrics(trades=[
            trade(Decimal("-10"), ct(2026, 7, 21)),
            trade(Decimal("-12"), ct(2026, 7, 22)),
            trade(Decimal("-9"), ct(2026, 7, 23)),
        ])
        assert m.annualized_sharpe_ratio < 0

    def test_is_the_daily_sharpe_scaled_by_sqrt_trading_days_per_year(self):
        m = BacktestMetrics(trades=[
            trade(Decimal("10"), ct(2026, 7, 21)),
            trade(Decimal("12"), ct(2026, 7, 22)),
            trade(Decimal("9"), ct(2026, 7, 23)),
            trade(Decimal("11"), ct(2026, 7, 24)),
        ])
        returns = m.daily_returns()
        mean = sum(returns) / Decimal(len(returns))
        sd = BacktestMetrics._stdev(returns)
        expected = (mean / sd) * Decimal(BacktestMetrics.TRADING_DAYS_PER_YEAR).sqrt()
        assert m.annualized_sharpe_ratio == expected

    def test_is_a_distinct_metric_from_the_unannualized_sharpe_ratio(self):
        """Same trades, one per day -- daily_returns() and _trade_returns()
        coincide numerically here, so annualized_sharpe_ratio must differ
        from sharpe_ratio by (approximately) the sqrt(252) scaling factor,
        not be silently identical to it."""
        m = BacktestMetrics(trades=[
            trade(Decimal("10"), ct(2026, 7, 21)),
            trade(Decimal("12"), ct(2026, 7, 22)),
            trade(Decimal("9"), ct(2026, 7, 23)),
        ])
        assert m.sharpe_ratio != m.annualized_sharpe_ratio
        ratio = m.annualized_sharpe_ratio / m.sharpe_ratio
        assert ratio == Decimal(BacktestMetrics.TRADING_DAYS_PER_YEAR).sqrt()


class TestPnlDistributionMoments:
    """`pnl_stdev`/`pnl_skew`/`pnl_kurtosis` -- professional-research-
    pipeline plan Phase 1c inputs for `research/dsr.py`. Hand-computed
    against pnls = [1, 2, 3, 4, 5] (mean=3, population m2=2, m3=0, m4=6.8),
    a symmetric series with an exact, checkable answer."""

    def _symmetric_metrics(self) -> BacktestMetrics:
        return BacktestMetrics(trades=[
            trade(Decimal(p), ct(2026, 7, 20 + i)) for i, p in enumerate([1, 2, 3, 4, 5])
        ])

    def test_none_with_fewer_than_two_trades(self):
        m = BacktestMetrics(trades=[trade(Decimal("10"), ct(2026, 7, 21))])
        assert m.pnl_stdev is None
        assert m.pnl_skew is None
        assert m.pnl_kurtosis is None

    def test_pnl_stdev_is_the_sample_stdev(self):
        m = self._symmetric_metrics()
        # variance = sum((v-mean)^2)/(n-1) = 10/4 = 2.5
        assert m.pnl_stdev == Decimal("2.5").sqrt()

    def test_pnl_skew_is_zero_for_a_symmetric_series(self):
        m = self._symmetric_metrics()
        assert m.pnl_skew == Decimal("0")

    def test_pnl_kurtosis_matches_the_hand_computed_value(self):
        m = self._symmetric_metrics()
        # m2=2, m4=6.8 -> kurtosis_excess = 6.8/4 - 3 = -1.3
        assert m.pnl_kurtosis == Decimal("-1.3")

    def test_positive_skew_for_a_right_skewed_series(self):
        # A long flat run of small losses with one huge winner.
        pnls = [-1, -1, -1, -1, -1, -1, -1, -1, -1, 20]
        m = BacktestMetrics(trades=[trade(Decimal(p), ct(2026, 7, 20 + i)) for i, p in enumerate(pnls)])
        assert m.pnl_skew > 0

    def test_negative_skew_for_a_left_skewed_series(self):
        pnls = [1, 1, 1, 1, 1, 1, 1, 1, 1, -20]
        m = BacktestMetrics(trades=[trade(Decimal(p), ct(2026, 7, 20 + i)) for i, p in enumerate(pnls)])
        assert m.pnl_skew < 0

    def test_zero_variance_returns_none(self):
        m = BacktestMetrics(trades=[trade(Decimal("5"), ct(2026, 7, 21)), trade(Decimal("5"), ct(2026, 7, 22))])
        assert m.pnl_skew is None
        assert m.pnl_kurtosis is None


class TestRMultiples:
    def test_r_multiple_is_pnl_over_risk(self):
        m = BacktestMetrics(trades=[trade(Decimal("100"), ct(2026, 7, 21))])
        r = m.r_multiple(m.trades[0], risk_per_trade=Decimal("50"))
        assert r == Decimal("2")

    def test_average_r_multiple(self):
        m = BacktestMetrics(trades=[trade(Decimal(p), ct(2026, 7, 21)) for p in ("50", "-50", "100")])
        avg = m.average_r_multiple(risk_per_trade=Decimal("50"))
        assert avg == (Decimal("1") + Decimal("-1") + Decimal("2")) / 3

    def test_expectancy_r_matches_hand_calculation(self):
        # 2 wins of +1R, 1 loss of -1R -> win_rate=2/3, avg_win_r=1, loss_rate=1/3, avg_loss_r=1
        # expectancy_r = (2/3 * 1) - (1/3 * 1) = 1/3
        m = BacktestMetrics(trades=[trade(Decimal(p), ct(2026, 7, 21)) for p in ("50", "50", "-50")])
        exp_r = m.expectancy_r(risk_per_trade=Decimal("50"))
        # Repeating decimal: compare with tolerance rather than exact equality,
        # since 1/3 computed via two different paths of Decimal arithmetic can
        # differ in the last of 28 significant digits.
        assert abs(exp_r - Decimal("1") / Decimal("3")) < Decimal("0.0000000001")

    def test_none_with_zero_risk(self):
        m = BacktestMetrics(trades=[trade(Decimal("50"), ct(2026, 7, 21))])
        assert m.average_r_multiple(Decimal("0")) is None
        assert m.expectancy_r(Decimal("0")) is None


class TestTimeBreakdowns:
    def test_pnl_by_weekday_orders_monday_first(self):
        trades = [
            trade(Decimal("10"), ct(2026, 7, 22)),  # Wednesday
            trade(Decimal("5"), ct(2026, 7, 20)),   # Monday
        ]
        m = BacktestMetrics(trades=trades)
        assert list(m.pnl_by_weekday.keys()) == ["Monday", "Wednesday"]
        assert m.pnl_by_weekday["Monday"] == Decimal("5")

    def test_pnl_by_hour_groups_and_sums(self):
        trades = [
            trade(Decimal("10"), ct(2026, 7, 21, hh=9)),
            trade(Decimal("5"), ct(2026, 7, 22, hh=9)),
            trade(Decimal("-3"), ct(2026, 7, 21, hh=14)),
        ]
        m = BacktestMetrics(trades=trades)
        assert m.pnl_by_hour[9] == Decimal("15")
        assert m.pnl_by_hour[14] == Decimal("-3")

    def test_pnl_by_month(self):
        trades = [
            trade(Decimal("10"), ct(2026, 6, 15)),
            trade(Decimal("20"), ct(2026, 6, 20)),
            trade(Decimal("-5"), ct(2026, 7, 5)),
        ]
        m = BacktestMetrics(trades=trades)
        assert m.pnl_by_month["2026-06"] == Decimal("30")
        assert m.pnl_by_month["2026-07"] == Decimal("-5")
        assert list(m.pnl_by_month.keys()) == ["2026-06", "2026-07"]  # chronological


class TestEquityCurveCsv:
    def test_writes_one_row_per_trade_plus_starting_row(self, tmp_path):
        m = BacktestMetrics(
            trades=[trade(Decimal("50"), ct(2026, 7, 21)), trade(Decimal("-20"), ct(2026, 7, 22))],
            starting_equity=Decimal("1000"),
        )
        path = tmp_path / "equity.csv"
        m.write_equity_curve_csv(path)

        import csv
        with path.open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 3  # starting + 2 trades
        assert rows[0]["equity"] == "1000"
        assert rows[-1]["equity"] == "1030"
