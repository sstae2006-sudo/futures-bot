"""Tests for `research.features`: the entry/trade join and CSV exporters.

The join test matters most, for the same reason `test_trend_pullback_analytics.py`
makes it matter there: `build_trade_records` lines up trades and entries by
*position alone*, trusting the single-position-at-a-time risk rule rather
than matching timestamps. A mismatch should raise loudly, not misattribute.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.backtest.runner import EntryRecord
from futures_bot.contracts import CME_TZ
from futures_bot.models import Side, Trade
from futures_bot.research.features import build_trade_records, write_ml_dataset_csv, write_trade_log_csv


def make_trade(net: Decimal, side: Side = Side.LONG, when: datetime = None) -> Trade:
    when = when or datetime(2026, 7, 21, 10, 0, tzinfo=CME_TZ)
    return Trade(
        side=side, quantity=1,
        entry_price=Decimal("7500"), exit_price=Decimal("7500") + net / Decimal("5"),
        entry_time=when, exit_time=when + timedelta(minutes=30),
        gross_pnl=net, commission=Decimal("1.24"), exit_reason="take_profit",
    )


def make_entry(reason="test entry", metadata=None, when: datetime = None) -> EntryRecord:
    when = when or datetime(2026, 7, 21, 10, 0, tzinfo=CME_TZ)
    return EntryRecord(timestamp=when, side="long", reason=reason, metadata=metadata or {})


class TestBuildTradeRecords:
    def test_joins_by_position(self):
        trades = [make_trade(Decimal("10")), make_trade(Decimal("-5"))]
        entries = [make_entry("first"), make_entry("second")]

        records = build_trade_records(
            trades, entries, run_id="r1", contract="MES", strategy="ema_crossover", strategy_params={}
        )

        assert len(records) == 2
        assert records[0].entry_reason == "first"
        assert records[0].outcome == "win"
        assert records[1].entry_reason == "second"
        assert records[1].outcome == "loss"

    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError, match="Mismatched lengths"):
            build_trade_records(
                [make_trade(Decimal("10"))], [], run_id="r1", contract="MES",
                strategy="ema_crossover", strategy_params={},
            )

    def test_captures_session_calendar_fields(self):
        when = datetime(2026, 7, 21, 14, 30, tzinfo=CME_TZ)  # a Tuesday
        record = build_trade_records(
            [make_trade(Decimal("10"), when=when)], [make_entry(when=when)],
            run_id="r1", contract="MES", strategy="ema_crossover", strategy_params={},
        )[0]
        assert record.day_of_week == "Tuesday"
        assert record.hour == 14
        assert record.session_date == "2026-07-21"

    def test_carries_strategy_metadata_through(self):
        entry = make_entry(metadata={"rsi": Decimal("62.5"), "trend_direction": "bullish"})
        record = build_trade_records(
            [make_trade(Decimal("10"))], [entry],
            run_id="r1", contract="MES", strategy="trend_pullback", strategy_params={"adx_min": 20},
        )[0]
        assert record.entry_metadata["trend_direction"] == "bullish"
        assert record.strategy_params == {"adx_min": 20}

    def test_scratch_outcome_for_zero_net_pnl(self):
        # gross_pnl must equal commission for net_pnl to land exactly on zero.
        record = build_trade_records(
            [make_trade(Decimal("1.24"))], [make_entry()],
            run_id="r1", contract="MES", strategy="ema_crossover", strategy_params={},
        )[0]
        assert record.net_pnl == Decimal("0")
        assert record.outcome == "scratch"


class TestCsvExport:
    def _records(self):
        entries = [
            make_entry("long setup", metadata={"ema9": Decimal("7502.123456"), "rsi": Decimal("61")}),
            make_entry("short setup", metadata={"ema9": Decimal("7480"), "range_size": 8.0}),
        ]
        trades = [make_trade(Decimal("10")), make_trade(Decimal("-5"), side=Side.SHORT)]
        return build_trade_records(
            trades, entries, run_id="csv-test", contract="MES",
            strategy="trend_pullback", strategy_params={"adx_min": 20},
        )

    def test_write_trade_log_csv_round_trips_core_fields(self, tmp_path):
        path = tmp_path / "trades.csv"
        write_trade_log_csv(self._records(), path)

        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 2
        assert rows[0]["outcome"] == "win"
        assert rows[0]["strategy"] == "trend_pullback"
        assert json.loads(rows[0]["strategy_params"]) == {"adx_min": 20}
        assert "ema9" in json.loads(rows[0]["entry_metadata"])

    def test_write_trade_log_csv_handles_empty(self, tmp_path):
        path = tmp_path / "empty.csv"
        write_trade_log_csv([], path)
        assert path.read_text(encoding="utf-8") == ""

    def test_write_ml_dataset_csv_flattens_metadata_union(self, tmp_path):
        path = tmp_path / "ml.csv"
        write_ml_dataset_csv(self._records(), path)

        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 2
        # Union of both records' metadata keys, even though neither record
        # has both keys individually.
        assert set(rows[0].keys()) >= {"ema9", "rsi", "range_size", "outcome", "net_pnl"}
        assert rows[0]["ema9"] == "7502.123456"
        assert rows[0]["range_size"] == ""  # not present on the first entry's metadata
        assert rows[1]["rsi"] == ""  # not present on the second entry's metadata

    def test_write_ml_dataset_csv_handles_empty(self, tmp_path):
        path = tmp_path / "ml_empty.csv"
        write_ml_dataset_csv([], path)
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows == [
            [
                "trade_id", "run_id", "contract", "strategy", "side",
                "entry_price", "exit_price", "stop_loss", "take_profit",
                "net_pnl", "holding_minutes", "exit_reason",
                "entry_slippage", "exit_slippage",
                "session_date", "day_of_week", "hour", "entry_reason", "outcome",
                "regime_trend", "regime_volatility", "regime_session",
            ]
        ]
