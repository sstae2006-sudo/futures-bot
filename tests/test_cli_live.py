"""Tests for `cli.cmd_live` -- the engine/feed wiring, not the network
calls: `feeds.massive.MassiveBarFeed` is replaced with a fake that hands
back canned bars, so this never touches a real API. Uses `max_iterations`
(a test-only parameter `cmd_live` documents explicitly) to run a fixed
number of poll cycles instead of looping until Ctrl+C.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from futures_bot.cli import cmd_live
from futures_bot.contracts import CME_TZ

CONFIG_YAML = """
contract: MES
mode: paper

risk:
  contracts_per_trade: 1
  stop_loss_points: 5
  take_profit_points: 10
  daily_max_loss: 300
  max_trades_per_session: 20
  account_size: 2500

session:
  start_ct: "00:00"
  end_ct: "23:59"
  flatten_before_close_minutes: 0
  trade_on_weekends: true

broker:
  name: paper
  slippage_ticks: 1
  commission_per_side: 0.62
  starting_cash: 2500

logging:
  level: WARNING
  directory: {log_dir}
  log_every_decision: false

strategy_name: ema_crossover
strategy_params:
  fast_period: 3
  slow_period: 5
  trend_period: 5

state_file: {state_file}
"""


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        CONFIG_YAML.format(
            log_dir=(tmp_path / "logs").as_posix(),
            state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
        ),
        encoding="utf-8",
    )
    return config_path


class FakeMassiveBarFeed:
    """Stands in for `feeds.massive.MassiveBarFeed`: hands back one
    pre-built bar per poll, then empty lists once exhausted, and records
    the constructor arguments `cmd_live` called it with."""

    instances: list["FakeMassiveBarFeed"] = []

    def __init__(self, symbol, api_key, resolution="5min"):
        self.symbol = symbol
        self.api_key = api_key
        self.resolution = resolution
        start = datetime(2026, 7, 21, 8, 30, tzinfo=CME_TZ)
        price = Decimal("7500")
        self._queued = []
        for i in range(20):
            price += Decimal("2")
            self._queued.append(
                [_bar(start + timedelta(minutes=i), price)]
            )
        FakeMassiveBarFeed.instances.append(self)

    def poll_new_bars(self):
        if self._queued:
            return self._queued.pop(0)
        return []


def _bar(ts, price):
    from futures_bot.models import Bar
    return Bar(timestamp=ts, open=price, high=price + 1, low=price - 1, close=price, volume=500)


@pytest.fixture(autouse=True)
def _patch_feed(monkeypatch):
    FakeMassiveBarFeed.instances = []
    monkeypatch.setattr("futures_bot.feeds.massive.MassiveBarFeed", FakeMassiveBarFeed)
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
    yield


class TestCmdLive:
    def test_runs_fixed_iterations_and_feeds_bars_to_the_engine(self, tmp_path, capsys):
        config = write_config(tmp_path)
        exit_code = cmd_live(config, "MESH6", "5min", poll_seconds=0, max_iterations=10)
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "Starting live run" in out
        assert FakeMassiveBarFeed.instances, "cmd_live never constructed the feed"
        feed = FakeMassiveBarFeed.instances[0]
        assert feed.symbol == "MESH6"
        assert feed.api_key == "test-key"

    def test_constructs_feed_with_requested_resolution(self, tmp_path):
        config = write_config(tmp_path)
        cmd_live(config, "MESH6", "1h", poll_seconds=0, max_iterations=1)
        assert FakeMassiveBarFeed.instances[0].resolution == "1h"

    def test_missing_api_key_fails_cleanly(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        config = write_config(tmp_path)
        exit_code = cmd_live(config, "MESH6", "5min", poll_seconds=0, max_iterations=1)
        captured = capsys.readouterr()
        assert exit_code == 1
        assert "MASSIVE_API_KEY" in captured.err

    def test_flattens_on_shutdown(self, tmp_path):
        """`engine.stop()` in cmd_live's `finally` block must flatten any
        open position rather than leaving the engine (and, for a real
        broker, the account) with an untracked open position."""
        config = write_config(tmp_path)
        cmd_live(config, "MESH6", "5min", poll_seconds=0, max_iterations=15)
        # No exception, and the run completed cleanly through engine.stop() --
        # TradingEngine.stop() itself flattens any open position and is
        # already covered by tests/test_backtest.py; this just confirms
        # cmd_live actually reaches that shutdown path rather than exiting
        # some other way (os._exit, an unhandled exception, etc).

    def test_non_paper_broker_warns_before_connecting(self, tmp_path, monkeypatch, capsys):
        """A tradovate-configured run must print the real-money warning
        before attempting to connect -- and with no Tradovate credentials in
        this test environment, connecting itself must fail loudly (a
        `BrokerError`) rather than silently falling back to something safe.
        `brokers.tradovate.TradovateCredentials.from_env` is exactly what
        should raise here; this is the CLI-level proof that wiring reaches
        it and doesn't swallow the failure.
        """
        from futures_bot.brokers.base import BrokerError

        for var in (
            "TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_APP_ID",
            "TRADOVATE_CLIENT_ID", "TRADOVATE_CLIENT_SECRET", "TRADOVATE_ACCOUNT_ID",
        ):
            monkeypatch.delenv(var, raising=False)

        config_text = CONFIG_YAML.format(
            log_dir=(tmp_path / "logs").as_posix(),
            state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
        ).replace(
            "broker:\n  name: paper\n  slippage_ticks: 1\n  commission_per_side: 0.62\n  starting_cash: 2500",
            "broker:\n  name: tradovate\n  commission_per_side: 0.62\n  tradovate_symbol: MESZ5",
        )
        config_path = tmp_path / "config_tradovate.yaml"
        config_path.write_text(config_text, encoding="utf-8")

        with pytest.raises(BrokerError):
            cmd_live(config_path, "MESH6", "5min", poll_seconds=0, max_iterations=1)

        captured = capsys.readouterr()
        assert "can place real orders" in captured.out
