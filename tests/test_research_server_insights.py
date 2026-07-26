"""Tests for `research_server.insights` -- degradation, regime-drift, and
parameter-recommendation findings. Builds `runs`/`trades`/
`optimization_trials` rows directly via `TradeStore`, the same "construct
fixtures at the storage layer, not through a full backtest" style
`tests/test_research_trade_store.py` already uses.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.contracts import CME_TZ
from futures_bot.research.features import TradeRecord
from futures_bot.research.trade_store import TradeStore
from futures_bot.research_server.insights import (
    degradation_findings, recommendation_findings, regime_drift_findings,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = tmp_path / "research.db"
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(db_path))
    return TradeStore(db_path)


def make_record(run_id: str, net_pnl: str, regime=(None, None, None), when=None) -> TradeRecord:
    when = when or datetime(2026, 7, 20, 10, 0, tzinfo=CME_TZ)
    net = Decimal(net_pnl)
    trend, vol, session = regime
    return TradeRecord(
        run_id=run_id, contract="MES", strategy="ema_crossover", strategy_params={},
        entry_time=when, exit_time=when + timedelta(minutes=10), side="long",
        entry_price=Decimal("7500"), exit_price=Decimal("7500") + net, gross_pnl=net,
        commission=Decimal("0"), net_pnl=net, holding_minutes=10.0, exit_reason="target",
        session_date=when.date().isoformat(), day_of_week="Monday", hour=10,
        entry_reason="test", entry_metadata={}, outcome="win" if net > 0 else "loss",
        regime_trend=trend, regime_volatility=vol, regime_session=session,
    )


def insert_completed_run(
    store: TradeStore, run_id: str, kind: str, strategy: str = "ema_crossover", expectancy: str = "1.0",
) -> None:
    store.insert_run(run_id=run_id, kind=kind, status="running", strategy=strategy, contract="MES", strategy_params={})
    store.complete_run(
        run_id, starting_equity=Decimal("2500"), trade_count=10, net_pnl=Decimal(expectancy) * 10,
        profit_factor=Decimal("1.5"), win_rate=Decimal("60"), expectancy=Decimal(expectancy),
        sharpe_ratio=None, sortino_ratio=None, max_drawdown=Decimal("50"), max_drawdown_pct=Decimal("2"),
        caveats=[],
    )


class TestDegradationFindings:
    def test_flags_negative_live_expectancy_against_positive_historical(self, store):
        insert_completed_run(store, "hist-1", "backtest", expectancy="5.0")
        insert_completed_run(store, "live-1", "live", expectancy="-3.0")

        findings = degradation_findings("ema_crossover")

        assert len(findings) == 1
        assert findings[0]["category"] == "degradation"
        assert findings[0]["severity"] == "warning"
        assert "ema_crossover" in findings[0]["message"]
        assert findings[0]["details"]["live_run_id"] == "live-1"
        assert findings[0]["details"]["historical_run_id"] == "hist-1"

    def test_no_finding_when_live_expectancy_is_still_positive(self, store):
        insert_completed_run(store, "hist-1", "backtest", expectancy="5.0")
        insert_completed_run(store, "live-1", "live", expectancy="2.0")

        assert degradation_findings("ema_crossover") == []

    def test_no_finding_without_a_live_run(self, store):
        insert_completed_run(store, "hist-1", "backtest", expectancy="5.0")
        assert degradation_findings("ema_crossover") == []

    def test_no_finding_without_historical_data(self, store):
        insert_completed_run(store, "live-1", "live", expectancy="-3.0")
        assert degradation_findings("ema_crossover") == []


class TestRegimeDriftFindings:
    def test_flags_a_live_regime_never_seen_historically(self, store):
        store.insert_run(run_id="hist-1", kind="backtest", status="completed", strategy="ema_crossover", contract="MES", strategy_params={})
        store.insert_trades([
            make_record("hist-1", "10", regime=("bullish", "medium", "morning")),
            make_record("hist-1", "10", regime=("bullish", "medium", "morning")),
        ])
        store.insert_run(run_id="live-1", kind="live", status="completed", strategy="ema_crossover", contract="MES", strategy_params={})
        store.insert_trades([
            make_record("live-1", "-5", regime=("bearish", "high", "overnight")),
            make_record("live-1", "-5", regime=("bearish", "high", "overnight")),
            make_record("live-1", "-5", regime=("bearish", "high", "overnight")),
            make_record("live-1", "-5", regime=("bearish", "high", "overnight")),
            make_record("live-1", "-5", regime=("bearish", "high", "overnight")),
        ])

        findings = regime_drift_findings("ema_crossover")

        assert len(findings) == 1
        assert findings[0]["category"] == "regime_drift"
        assert findings[0]["details"]["trend"] == "bearish"
        assert findings[0]["details"]["volatility"] == "high"
        assert findings[0]["details"]["session"] == "overnight"

    def test_no_finding_when_live_regimes_match_historical(self, store):
        store.insert_run(run_id="hist-1", kind="backtest", status="completed", strategy="ema_crossover", contract="MES", strategy_params={})
        store.insert_trades([make_record("hist-1", "10", regime=("bullish", "medium", "morning"))])
        store.insert_run(run_id="live-1", kind="live", status="completed", strategy="ema_crossover", contract="MES", strategy_params={})
        store.insert_trades([make_record("live-1", "5", regime=("bullish", "medium", "morning")) for _ in range(5)])

        assert regime_drift_findings("ema_crossover") == []

    def test_no_finding_below_minimum_live_trade_count(self, store):
        store.insert_run(run_id="hist-1", kind="backtest", status="completed", strategy="ema_crossover", contract="MES", strategy_params={})
        store.insert_trades([make_record("hist-1", "10", regime=("bullish", "medium", "morning"))])
        store.insert_run(run_id="live-1", kind="live", status="completed", strategy="ema_crossover", contract="MES", strategy_params={})
        store.insert_trades([make_record("live-1", "-5", regime=("bearish", "high", "overnight"))])  # only 1

        assert regime_drift_findings("ema_crossover") == []

    def test_no_finding_without_any_live_run(self, store):
        store.insert_run(run_id="hist-1", kind="backtest", status="completed", strategy="ema_crossover", contract="MES", strategy_params={})
        store.insert_trades([make_record("hist-1", "10", regime=("bullish", "medium", "morning"))])
        assert regime_drift_findings("ema_crossover") == []


class TestRecommendationFindings:
    def test_flags_a_different_best_params(self, store):
        store.insert_run(run_id="opt-1", kind="optimizer", status="running", strategy="ema_crossover", contract="MES", strategy_params={})
        store.complete_run(
            "opt-1", starting_equity=Decimal("2500"), trade_count=10, net_pnl=Decimal("100"),
            profit_factor=Decimal("1.5"), win_rate=Decimal("60"), expectancy=Decimal("10"),
            sharpe_ratio=None, sortino_ratio=None, max_drawdown=Decimal("50"), max_drawdown_pct=Decimal("2"), caveats=[],
        )
        store.insert_optimization_trial(
            batch_id="opt-1", strategy="ema_crossover", params={"fast_period": 5, "slow_period": 13},
            train_trades=10, train_net_pnl=Decimal("100"), train_profit_factor=Decimal("1.5"),
            train_max_drawdown=Decimal("50"), rank=1,
        )

        findings = recommendation_findings("ema_crossover", current_params={"fast_period": 8, "slow_period": 34})

        assert len(findings) == 1
        assert findings[0]["category"] == "recommendation"
        assert findings[0]["severity"] == "info"
        assert findings[0]["details"]["run_id"] == "opt-1"
        assert findings[0]["details"]["recommended_params"] == {"fast_period": 5, "slow_period": 13}
        assert findings[0]["details"]["current_params"] == {"fast_period": 8, "slow_period": 34}

    def test_no_finding_when_best_params_match_current(self, store):
        store.insert_run(run_id="opt-1", kind="optimizer", status="running", strategy="ema_crossover", contract="MES", strategy_params={})
        store.complete_run(
            "opt-1", starting_equity=Decimal("2500"), trade_count=10, net_pnl=Decimal("100"),
            profit_factor=Decimal("1.5"), win_rate=Decimal("60"), expectancy=Decimal("10"),
            sharpe_ratio=None, sortino_ratio=None, max_drawdown=Decimal("50"), max_drawdown_pct=Decimal("2"), caveats=[],
        )
        store.insert_optimization_trial(
            batch_id="opt-1", strategy="ema_crossover", params={"fast_period": 8, "slow_period": 34},
            train_trades=10, train_net_pnl=Decimal("100"), train_profit_factor=Decimal("1.5"),
            train_max_drawdown=Decimal("50"), rank=1,
        )

        findings = recommendation_findings("ema_crossover", current_params={"fast_period": 8, "slow_period": 34})

        assert findings == []

    def test_no_finding_without_any_optimizer_run(self, store):
        assert recommendation_findings("ema_crossover", current_params={"fast_period": 8}) == []
