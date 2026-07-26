"""Analytics joining, CSV export, and full-strategy end-to-end tests.

The join test matters most: `build_trade_records` lines up the broker's
`Trade` objects with the strategy's own `entry_contexts`/`bars_held`/
`excursions` by *position alone*, trusting the Phase 1 one-position-at-a-time
constraint rather than matching on timestamps. If that constraint were ever
violated the join would silently misattribute context to the wrong trade, so
the mismatched-length guard is exercised directly.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.backtest.runner import run_backtest
from futures_bot.config import BrokerSettings, RiskSettings, SessionSettings, Settings
from futures_bot.contracts import CME_TZ, MES, is_market_open
from futures_bot.models import Bar, Side, Trade
from futures_bot.strategy.trend_pullback.analytics import (
    EntryContext,
    build_trade_records,
    write_ml_dataset_csv,
    write_trade_log_csv,
)
from futures_bot.strategy.trend_pullback.strategy import TrendPullbackStrategy


def make_trade(net: Decimal, side: Side = Side.LONG, commission: Decimal = Decimal("1.24")) -> Trade:
    when = datetime(2026, 7, 21, 10, 0, tzinfo=CME_TZ)
    return Trade(
        side=side, quantity=1,
        entry_price=Decimal("7500"), exit_price=Decimal("7500") + net / Decimal("5"),
        entry_time=when, exit_time=when + timedelta(minutes=30),
        gross_pnl=net, commission=commission, exit_reason="take_profit",
    )


def make_context(reason="test entry") -> EntryContext:
    return EntryContext(
        entry_reason=reason,
        ema9=Decimal("7502"), ema21=Decimal("7498"), ema50=Decimal("7490"), ema200=Decimal("7470"),
        ema50_slope=Decimal("0.5"), ema200_slope=Decimal("0.2"),
        rsi=Decimal("60"), atr=Decimal("3"), adx=Decimal("25"),
        vwap=Decimal("7495"), vwap_distance=Decimal("5"), volume_ratio=Decimal("1.4"),
        trend_direction="bullish", trade_direction="long",
    )


class TestBuildTradeRecords:
    def test_joins_by_position_in_order(self):
        trades = [make_trade(Decimal("50")), make_trade(Decimal("-20"))]
        contexts = [make_context("first"), make_context("second")]
        records = build_trade_records(trades, contexts, [4, 7], [(Decimal("10"), Decimal("2")),
                                                                    (Decimal("3"), Decimal("15"))])
        assert len(records) == 2
        assert records[0].entry_reason == "first"
        assert records[0].duration_bars == 4
        assert records[0].mfe == Decimal("10")
        assert records[1].entry_reason == "second"
        assert records[1].outcome == "loss"
        assert records[0].outcome == "win"

    def test_rejects_mismatched_lengths(self):
        trades = [make_trade(Decimal("50"))]
        contexts = [make_context(), make_context()]  # one too many
        with pytest.raises(ValueError, match="Mismatched lengths"):
            build_trade_records(trades, contexts, [1], [(Decimal("0"), Decimal("0"))])

    def test_scratch_outcome_for_zero_pnl(self):
        trades = [make_trade(Decimal("0"), commission=Decimal("0"))]
        records = build_trade_records(trades, [make_context()], [1], [(Decimal("0"), Decimal("0"))])
        assert records[0].outcome == "scratch"


class TestCsvExport:
    def test_write_trade_log_csv_round_trips(self, tmp_path):
        trades = [make_trade(Decimal("50")), make_trade(Decimal("-20"), Side.SHORT)]
        contexts = [make_context("a"), make_context("b")]
        records = build_trade_records(trades, contexts, [4, 7], [(Decimal("10"), Decimal("2")),
                                                                    (Decimal("3"), Decimal("15"))])
        path = tmp_path / "trades.csv"
        write_trade_log_csv(records, path)

        with path.open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 2
        assert rows[0]["entry_reason"] == "a"
        assert rows[1]["side"] == "short"

    def test_write_trade_log_csv_handles_empty(self, tmp_path):
        path = tmp_path / "empty.csv"
        write_trade_log_csv([], path)
        assert path.exists()

    def test_write_ml_dataset_csv_has_expected_columns(self, tmp_path):
        trades = [make_trade(Decimal("50"))]
        records = build_trade_records(trades, [make_context()], [4], [(Decimal("10"), Decimal("2"))])
        path = tmp_path / "dataset.csv"
        write_ml_dataset_csv(records, path)

        with path.open() as fh:
            rows = list(csv.DictReader(fh))
        assert "outcome" in rows[0] and "net_pnl" in rows[0] and "rsi" in rows[0]
        assert rows[0]["outcome"] == "win"

    def test_write_ml_dataset_csv_handles_empty(self, tmp_path):
        path = tmp_path / "empty_dataset.csv"
        write_ml_dataset_csv([], path)
        with path.open() as fh:
            header = fh.readline()
        assert "outcome" in header

    def test_decimal_values_are_rounded_for_readability(self, tmp_path):
        """A raw Decimal division can carry 20+ digits; the CSV should not."""
        ctx = make_context()
        object.__setattr__(ctx, "rsi", Decimal("1") / Decimal("3"))  # 0.333333...
        trades = [make_trade(Decimal("10"))]
        records = build_trade_records(trades, [ctx], [1], [(Decimal("0"), Decimal("0"))])
        path = tmp_path / "rounded.csv"
        write_trade_log_csv(records, path)
        with path.open() as fh:
            row = next(csv.DictReader(fh))
        assert len(row["rsi"].split(".")[-1]) <= 4


class TestStrategyEndToEnd:
    """Drives the full strategy through the real engine on synthetic bars,
    the same way the CLI does -- catches wiring mistakes that isolated unit
    tests of each module can't (e.g. a signature mismatch between the
    strategy and what the engine actually calls)."""

    def _settings(self, tmp_path) -> Settings:
        return Settings(
            contract="MES", mode="paper",
            risk=RiskSettings(
                stop_loss_points=Decimal("10"), take_profit_points=Decimal("20"),
                daily_max_loss=Decimal("500"), max_trades_per_session=20,
                account_size=Decimal("5000"),
            ),
            session=SessionSettings(start_ct="08:00", end_ct="15:30"),
            broker=BrokerSettings(starting_cash=Decimal("5000")),
            state_file=tmp_path / "state.json",
            logging={"directory": tmp_path, "level": "WARNING"},
        )

    def _bars(self, days: int = 10) -> list[Bar]:
        import random
        rng = random.Random(11)
        start = datetime(2026, 6, 1, 0, 0, tzinfo=CME_TZ)
        bars = []
        price = 7500.0
        moment = start
        end = start + timedelta(days=days)
        while moment < end:
            if is_market_open(moment):
                price += rng.gauss(0, 1.2)
                o = price
                c = price + rng.gauss(0, 0.4)
                h = max(o, c) + abs(rng.gauss(0, 0.4))
                low = min(o, c) - abs(rng.gauss(0, 0.4))
                bars.append(Bar(
                    timestamp=moment, open=Decimal(str(round(o, 2))),
                    high=Decimal(str(round(h, 2))), low=Decimal(str(round(low, 2))),
                    close=Decimal(str(round(c, 2))), volume=rng.randint(200, 3000),
                ))
                price = c
            moment += timedelta(minutes=5)
        return bars

    def test_runs_without_crashing_and_tracks_analytics(self, tmp_path):
        settings = self._settings(tmp_path)
        strategy = TrendPullbackStrategy(settings.contract_spec)
        bars = self._bars(days=15)

        metrics = run_backtest(settings, strategy, bars, journal_dir=tmp_path)

        # Whatever number of trades happened, the strategy's own bookkeeping
        # must be internally consistent and line up with what the broker saw.
        assert len(strategy.entry_contexts) == len(strategy.bars_held) == len(strategy.excursions)
        assert len(strategy.entry_contexts) == metrics.trade_count
        assert strategy._current is None, "strategy must not think a position is open after the run ends"

    def test_mfe_mae_are_never_negative(self, tmp_path):
        settings = self._settings(tmp_path)
        strategy = TrendPullbackStrategy(settings.contract_spec)
        run_backtest(settings, strategy, self._bars(days=15), journal_dir=tmp_path)
        for mfe, mae in strategy.excursions:
            assert mfe >= 0
            assert mae >= 0

    def test_records_and_broker_trades_line_up(self, tmp_path):
        from futures_bot.brokers.paper import PaperBroker

        settings = self._settings(tmp_path)
        strategy = TrendPullbackStrategy(settings.contract_spec)
        bars = self._bars(days=15)
        metrics = run_backtest(settings, strategy, bars, journal_dir=tmp_path)

        records = build_trade_records(
            metrics.trades, strategy.entry_contexts, strategy.bars_held, strategy.excursions
        )
        assert len(records) == metrics.trade_count
        for record, trade in zip(records, metrics.trades):
            assert record.net_pnl == trade.net_pnl
            assert record.side == trade.side.value
