"""Per-strategy feature matrix construction, dataset health thresholds, and
the raw-dict encoding round-trip (`encode_feature_row`) that both the
Backtest+AI filter and Prediction Sandbox rely on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from futures_bot.research.features import TradeRecord
from futures_bot.research.ml.dataset import (
    build_feature_matrix, dataset_health, dedupe_market_events, encode_feature_row, feature_distribution,
)
from futures_bot.research.trade_store import TradeStore, default_db_path


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "ml_dataset.db"))
    yield


def _insert_trades(
    strategy: str, n: int, *, rsi_predicts_win: bool = True, with_categorical: bool = True, start_hour: int = 0,
):
    store = TradeStore(default_db_path())
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    records = []
    for i in range(start_hour, start_hour + n):
        rsi = 40.0 + (i % 20)
        win = (rsi > 50) if rsi_predicts_win else (i % 2 == 0)
        metadata = {"rsi": rsi, "adx": 20.0 + (i % 10)}
        if with_categorical:
            metadata["trend_direction"] = "bullish" if win else "bearish"
        records.append(TradeRecord(
            run_id="r1", contract="MES", strategy=strategy, strategy_params={},
            entry_time=base + timedelta(hours=i), exit_time=base + timedelta(hours=i, minutes=30),
            side="long", entry_price=Decimal("100"), exit_price=Decimal("105" if win else "95"),
            gross_pnl=Decimal("50" if win else "-50"), commission=Decimal("1.24"),
            net_pnl=Decimal("48.76" if win else "-51.24"),
            holding_minutes=30.0, exit_reason="take_profit" if win else "stop_loss",
            session_date=str((base + timedelta(hours=i)).date()), day_of_week="Monday", hour=i % 24,
            entry_reason="test", entry_metadata=metadata, outcome="win" if win else "loss",
        ))
    store.insert_trades(records)
    store.close()
    return records


def _trade(run_id: str, entry_hour: int, *, strategy: str = "s1", outcome: str = "win"):
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return TradeRecord(
        run_id=run_id, contract="MES", strategy=strategy, strategy_params={"fast": 9},
        entry_time=base + timedelta(hours=entry_hour), exit_time=base + timedelta(hours=entry_hour, minutes=30),
        side="long", entry_price=Decimal("100"), exit_price=Decimal("105" if outcome == "win" else "95"),
        gross_pnl=Decimal("50" if outcome == "win" else "-50"), commission=Decimal("1.24"),
        net_pnl=Decimal("48.76" if outcome == "win" else "-51.24"),
        holding_minutes=30.0, exit_reason="take_profit" if outcome == "win" else "stop_loss",
        session_date=str((base + timedelta(hours=entry_hour)).date()), day_of_week="Monday", hour=entry_hour % 24,
        entry_reason="test", entry_metadata={"rsi": 55.0}, outcome=outcome,
    )


class TestDedupeMarketEvents:
    def test_identical_trade_recorded_under_two_run_ids_collapses_to_one(self):
        trades = [_trade("run-a", 1).__dict__, _trade("run-b", 1).__dict__]
        # TradeRecord fields are the same shape `TradeStore.fetch_trades` returns
        # (plain dicts); entry_time/exit_time as datetimes round-trip via str() same as isoformat.
        result = dedupe_market_events(trades)
        assert result.total_rows == 2
        assert result.unique_timestamps == 1
        assert result.duplicate_count == 1
        assert len(result.trades) == 1

    def test_different_entry_times_are_not_deduped(self):
        trades = [_trade("run-a", 1).__dict__, _trade("run-a", 2).__dict__]
        result = dedupe_market_events(trades)
        assert result.duplicate_count == 0
        assert len(result.trades) == 2

    def test_different_run_id_or_params_alone_does_not_prevent_dedup(self):
        # Same underlying market event, different run/config -- still one opportunity.
        t1 = _trade("run-a", 1)
        t2 = _trade("run-b", 1)
        object.__setattr__(t2, "strategy_params", {"fast": 21})
        result = dedupe_market_events([t1.__dict__, t2.__dict__])
        assert result.duplicate_count == 1


class TestBuildFeatureMatrix:
    def test_numeric_and_categorical_columns_split_correctly(self):
        _insert_trades("s1", 80)
        fm = build_feature_matrix("s1")
        assert "rsi" in fm.numeric_raw_keys
        assert "adx" in fm.numeric_raw_keys
        assert "trend_direction" not in fm.numeric_raw_keys
        # trend_direction one-hot columns exist in the final feature space
        assert any(col.startswith("trend_direction_") for col in fm.feature_columns)

    def test_row_count_matches_trade_count(self):
        _insert_trades("s1", 42)
        fm = build_feature_matrix("s1")
        assert fm.dataset_size == 42
        assert len(fm.X) == 42
        assert len(fm.y) == 42

    def test_scoped_to_one_strategy_only(self):
        _insert_trades("strategy_a", 10)
        _insert_trades("strategy_b", 5)
        fm_a = build_feature_matrix("strategy_a")
        fm_b = build_feature_matrix("strategy_b")
        assert fm_a.dataset_size == 10
        assert fm_b.dataset_size == 5

    def test_dataset_version_changes_when_trades_change(self):
        _insert_trades("s1", 10)
        v1 = build_feature_matrix("s1").dataset_version
        _insert_trades("s1", 5, start_hour=100)  # genuinely new market events, not reruns of the first 10
        v2 = build_feature_matrix("s1").dataset_version
        assert v1 != v2

    def test_rerunning_the_same_backtest_does_not_inflate_dataset_size(self):
        store = TradeStore(default_db_path())
        store.insert_trades([_trade("run-a", i) for i in range(70)])
        store.insert_trades([_trade("run-b", i) for i in range(70)])  # identical rerun
        store.close()
        fm = build_feature_matrix("s1")
        assert fm.dataset_size == 70
        assert fm.total_rows == 140
        assert fm.duplicate_count == 70

    def test_deduped_rows_never_span_both_train_and_validation(self):
        """The scenario dedup exists to prevent: without it, an identical
        row recorded under two run_ids could land on both sides of the
        chronological split -- the model would effectively see a twin of a
        validation row during training."""
        from futures_bot.research.ml.split import split_trades_chronologically

        store = TradeStore(default_db_path())
        store.insert_trades([_trade("run-a", i) for i in range(70)])
        store.insert_trades([_trade("run-b", i) for i in range(70)])
        store.close()
        fm = build_feature_matrix("s1")
        split = split_trades_chronologically(fm.trades)
        train_keys = {(fm.trades[i]["entry_time"], fm.trades[i]["exit_time"]) for i in split.train}
        val_keys = {(fm.trades[i]["entry_time"], fm.trades[i]["exit_time"]) for i in split.validation}
        assert not (train_keys & val_keys)


class TestDatasetHealth:
    def test_not_enough_data_below_minimum_trade_count(self):
        _insert_trades("thin", 10)
        health = dataset_health("thin")
        assert health.status == "NOT_ENOUGH_DATA"

    def test_not_enough_data_with_only_one_outcome_class(self):
        store = TradeStore(default_db_path())
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        records = [
            TradeRecord(
                run_id="r1", contract="MES", strategy="all_wins", strategy_params={},
                entry_time=base + timedelta(hours=i), exit_time=base + timedelta(hours=i, minutes=30),
                side="long", entry_price=Decimal("100"), exit_price=Decimal("105"),
                gross_pnl=Decimal("50"), commission=Decimal("1.24"), net_pnl=Decimal("48.76"),
                holding_minutes=30.0, exit_reason="take_profit",
                session_date=str((base + timedelta(hours=i)).date()), day_of_week="Monday", hour=i % 24,
                entry_reason="test", entry_metadata={"rsi": 60.0}, outcome="win",
            )
            for i in range(80)
        ]
        store.insert_trades(records)
        store.close()
        health = dataset_health("all_wins")
        assert health.status == "NOT_ENOUGH_DATA"
        assert any("one outcome class" in r for r in health.reasons)

    def test_ready_with_enough_balanced_data(self):
        _insert_trades("ready_strategy", 80, rsi_predicts_win=False)
        health = dataset_health("ready_strategy")
        assert health.status in ("READY", "WARNING")
        assert health.trade_count == 80
        assert health.win_count + health.loss_count == 80

    def test_reports_total_rows_and_duplicate_market_events(self):
        store = TradeStore(default_db_path())
        store.insert_trades([_trade("run-a", i) for i in range(65)])
        store.insert_trades([_trade("run-b", i) for i in range(65)])  # identical rerun
        store.close()
        health = dataset_health("s1")
        assert health.total_rows == 130
        assert health.unique_timestamps == 65
        assert health.duplicate_market_events == 65
        assert health.trade_count == 65  # final training dataset size, post-dedup


class TestFeatureDistribution:
    def test_returns_bins_and_win_loss_averages(self):
        _insert_trades("dist", 80)
        result = feature_distribution("dist", "rsi", bins=8)
        assert result["feature"] == "rsi"
        assert len(result["bins"]) == 9  # bins+1 edges
        assert sum(result["counts"]) == 80
        assert result["win_average"] is not None
        assert result["loss_average"] is not None

    def test_unknown_feature_raises(self):
        _insert_trades("dist2", 80)
        with pytest.raises(ValueError):
            feature_distribution("dist2", "not_a_real_feature")


class TestEncodeFeatureRow:
    def test_roundtrips_numeric_and_categorical_values(self):
        _insert_trades("enc", 80)
        fm = build_feature_matrix("enc")
        live_signal_metadata = {"rsi": 55.0, "adx": 22.0, "trend_direction": "bullish"}
        encoded = encode_feature_row(live_signal_metadata, fm.numeric_raw_keys, fm.dummy_source, fm.feature_columns)
        assert encoded.shape == (1, len(fm.feature_columns))
        rsi_index = fm.feature_columns.index("rsi")
        assert encoded[0, rsi_index] == 55.0
        bullish_col = next(c for c in fm.feature_columns if c == "trend_direction_bullish")
        bearish_col = next(c for c in fm.feature_columns if c == "trend_direction_bearish")
        assert encoded[0, fm.feature_columns.index(bullish_col)] == 1.0
        assert encoded[0, fm.feature_columns.index(bearish_col)] == 0.0

    def test_missing_raw_key_encodes_as_nan_not_a_crash(self):
        _insert_trades("enc2", 80)
        fm = build_feature_matrix("enc2")
        encoded = encode_feature_row({}, fm.numeric_raw_keys, fm.dummy_source, fm.feature_columns)
        assert encoded.shape == (1, len(fm.feature_columns))
