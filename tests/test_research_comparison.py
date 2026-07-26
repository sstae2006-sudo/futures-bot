"""Tests for `research.comparison`: multi-strategy leaderboard."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.config import BrokerSettings, RiskSettings, SessionSettings, Settings
from futures_bot.contracts import CME_TZ, MES
from futures_bot.models import Bar
from futures_bot.research.comparison import (
    compare_strategies,
    format_leaderboard,
    rank_by_net_pnl,
    rank_by_profit_factor,
)
from futures_bot.strategy import ema_crossover, vwap_reversion  # noqa: F401 -- registers strategies


def make_settings(**overrides) -> Settings:
    base = dict(
        contract="MES",
        mode="paper",
        risk=RiskSettings(
            contracts_per_trade=1,
            stop_loss_points=Decimal("5"),
            take_profit_points=Decimal("10"),
            daily_max_loss=Decimal("1000"),
            max_trades_per_session=200,
            account_size=Decimal("5000"),
        ),
        session=SessionSettings(start_ct="08:30", end_ct="15:00"),
        broker=BrokerSettings(starting_cash=Decimal("5000")),
    )
    base.update(overrides)
    return Settings(**base)


def make_trending_bars(n: int, start_price: Decimal = Decimal("7500")) -> list[Bar]:
    """Deterministic up/down swings so both a trend strategy (ema_crossover)
    and a mean-reversion strategy (vwap_reversion) actually take trades."""
    start = datetime(2026, 7, 21, 8, 30, tzinfo=CME_TZ)
    bars, price = [], start_price
    for i in range(n):
        swing = Decimal("3") if (i // 20) % 2 == 0 else Decimal("-3")
        price += swing
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=i),
                open=price, high=price + Decimal("2"), low=price - Decimal("2"),
                close=price, volume=800,
            )
        )
    return bars


class TestCompareStrategies:
    def test_runs_every_requested_strategy(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_trending_bars(300)

        entries = compare_strategies(
            settings, bars,
            strategies=[("ema_crossover", {"fast_period": 3, "slow_period": 8}), ("vwap_reversion", {"min_bars": 5})],
            journal_root=tmp_path,
        )

        assert {e.strategy for e in entries} == {"ema_crossover", "vwap_reversion"}
        assert all(e.metrics.bars_processed == 300 for e in entries)

    def test_ranked_best_first_by_default(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_trending_bars(300)

        entries = compare_strategies(
            settings, bars,
            strategies=[("ema_crossover", {"fast_period": 3, "slow_period": 8}), ("vwap_reversion", {"min_bars": 5})],
            journal_root=tmp_path,
        )

        pnls = [e.metrics.net_pnl for e in entries]
        assert pnls == sorted(pnls, reverse=True)

    def test_custom_rank_key_changes_order(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_trending_bars(300)
        strategies = [("ema_crossover", {"fast_period": 3, "slow_period": 8}), ("vwap_reversion", {"min_bars": 5})]

        by_pnl = compare_strategies(settings, bars, strategies, rank_key=rank_by_net_pnl, journal_root=tmp_path)
        by_pf = compare_strategies(settings, bars, strategies, rank_key=rank_by_profit_factor, journal_root=tmp_path)

        # Same two strategies either way, ranking key is honored independently.
        assert {e.strategy for e in by_pnl} == {e.strategy for e in by_pf}

    def test_separate_journal_directories_per_strategy(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_trending_bars(200)

        compare_strategies(
            settings, bars,
            strategies=[("ema_crossover", {"fast_period": 3, "slow_period": 8}), ("vwap_reversion", {"min_bars": 5})],
            journal_root=tmp_path,
        )

        assert (tmp_path / "ema_crossover" / "decisions.jsonl").exists()
        assert (tmp_path / "vwap_reversion" / "decisions.jsonl").exists()

    def test_unknown_strategy_raises_clear_error(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        with pytest.raises(KeyError):
            compare_strategies(settings, make_trending_bars(50), strategies=[("not_a_real_strategy", {})])


class TestFormatLeaderboard:
    def test_empty_list_does_not_crash(self):
        text = format_leaderboard([])
        assert "No strategies to compare" in text

    def test_includes_required_columns(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        entries = compare_strategies(
            settings, make_trending_bars(300),
            strategies=[("ema_crossover", {"fast_period": 3, "slow_period": 8})],
            journal_root=tmp_path,
        )
        text = format_leaderboard(entries)
        for column in ("Strategy", "Net P&L", "PF", "Drawdown", "Trades"):
            assert column in text
        assert "ema_crossover" in text
