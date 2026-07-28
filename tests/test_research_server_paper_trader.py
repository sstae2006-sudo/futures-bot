"""Tests for `research_server.paper_trader.AutonomousPaperTrader` --
multiple strategies sharing one bar poll, the structural paper-only
safety guard, per-strategy `runs` persistence, and contract auto-
detection. `feeds.massive.MassiveBarFeed` is monkeypatched to a fake (the
same pattern `tests/test_api_live_session.py` established); the Contracts
API is served by an injected fake `requests.Session` (`start()` accepts
one directly, no monkeypatching needed for that half).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from futures_bot.config import load_settings
from futures_bot.contracts import CME_TZ
from futures_bot.research.trade_store import TradeStore
from futures_bot.research_server.paper_trader import AutonomousPaperTrader, PaperTraderError

CONFIG_YAML = """
contract: MES
mode: paper

risk:
  contracts_per_trade: 1
  stop_loss_points: 5
  take_profit_points: 10
  daily_max_loss: 100000
  max_trades_per_session: 2000
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
  min_ema_distance: 0.01

research_server:
  enabled: true
  paper_strategies: [ema_crossover, vwap_reversion]
  data_sync_products: [MES]
  resolution: 5min
  poll_seconds: 1

state_file: {state_file}
"""

TRADOVATE_CONFIG_YAML = CONFIG_YAML.replace(
    "broker:\n  name: paper\n  slippage_ticks: 1\n  commission_per_side: 0.62\n  starting_cash: 2500",
    "broker:\n  name: tradovate\n  commission_per_side: 0.62\n  tradovate_symbol: MESZ5",
)


def write_config(tmp_path, yaml_text=CONFIG_YAML):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml_text.format(
            log_dir=(tmp_path / "logs").as_posix(), state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
        ),
        encoding="utf-8",
    )
    return load_settings(config_path)


class FakeMassiveBarFeed:
    instances: list["FakeMassiveBarFeed"] = []

    def __init__(self, symbol, api_key, resolution="5min"):
        self.symbol = symbol
        self.api_key = api_key
        self.resolution = resolution
        self.poll_calls = 0
        start = datetime(2026, 7, 21, 8, 30, tzinfo=CME_TZ)
        price = 7500
        self._queued = []
        for i in range(30):
            price += 3 if i % 2 == 0 else -1
            self._queued.append([_bar(start + timedelta(minutes=i), price)])
        FakeMassiveBarFeed.instances.append(self)

    def poll_new_bars(self):
        self.poll_calls += 1
        if self._queued:
            return self._queued.pop(0)
        return []


def _bar(ts, price):
    from decimal import Decimal
    from futures_bot.models import Bar
    price = Decimal(str(price))
    return Bar(timestamp=ts, open=price, high=price + 1, low=price - 1, close=price, volume=500)


class FakeContractsResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeContractsSession:
    """Serves the Contracts API only -- always answers with a single
    front-month MESU6, regardless of query date, which is all these tests
    need."""

    def __init__(self):
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        return FakeContractsResponse({
            "status": "OK",
            "results": [{
                "active": True, "date": (params or {}).get("date", "2026-07-22"), "name": "MESU6 Future",
                "product_code": "MES", "ticker": "MESU6", "type": "single",
                "first_trade_date": "2025-06-20", "last_trade_date": "2026-09-18",
            }],
        })


@pytest.fixture(autouse=True)
def _patch_feed(monkeypatch):
    FakeMassiveBarFeed.instances = []
    monkeypatch.setattr("futures_bot.feeds.massive.MassiveBarFeed", FakeMassiveBarFeed)
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", "will-be-overridden")


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestSafetyGuard:
    def test_refuses_non_paper_broker_before_constructing_anything(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        settings = write_config(tmp_path, TRADOVATE_CONFIG_YAML)
        trader = AutonomousPaperTrader()

        with pytest.raises(PaperTraderError, match="paper broker"):
            trader.start(settings, "test-key", session=FakeContractsSession())

        assert FakeMassiveBarFeed.instances == []

    def test_refuses_unknown_strategy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        bad = CONFIG_YAML.replace(
            "paper_strategies: [ema_crossover, vwap_reversion]", "paper_strategies: [not_a_real_strategy]"
        )
        settings = write_config(tmp_path, bad)
        trader = AutonomousPaperTrader()

        with pytest.raises(PaperTraderError, match="Unknown strategy"):
            trader.start(settings, "test-key", session=FakeContractsSession())

    def test_empty_paper_strategies_is_a_graceful_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        empty = CONFIG_YAML.replace("paper_strategies: [ema_crossover, vwap_reversion]", "paper_strategies: []")
        settings = write_config(tmp_path, empty)
        trader = AutonomousPaperTrader()

        status = trader.start(settings, "test-key", session=FakeContractsSession())

        assert status["running"] is False
        assert status["strategies"] == {}


class TestLifecycle:
    def test_start_runs_every_configured_strategy_off_one_shared_feed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        settings = write_config(tmp_path)
        trader = AutonomousPaperTrader()

        status = trader.start(settings, "test-key", session=FakeContractsSession())

        assert status["running"] is True
        assert status["live_symbol"] == "MESU6"
        assert set(status["strategies"]) == {"ema_crossover", "vwap_reversion"}
        assert len(FakeMassiveBarFeed.instances) == 1  # one shared feed, not one per strategy

        assert _wait_for(lambda: all(
            trader.status()["strategies"][name]["status"] == "running" for name in ("ema_crossover", "vwap_reversion")
        ))

        trader.stop(timeout=10)
        assert trader.status()["running"] is False

    def test_each_strategy_gets_its_own_live_run_row(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        db_path = tmp_path / "research.db"
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(db_path))
        settings = write_config(tmp_path)
        trader = AutonomousPaperTrader()

        status = trader.start(settings, "test-key", session=FakeContractsSession())
        run_ids = {name: s["run_id"] for name, s in status["strategies"].items()}
        assert len(set(run_ids.values())) == 2  # distinct run ids

        trader.stop(timeout=10)

        store = TradeStore(db_path)
        try:
            for name, run_id in run_ids.items():
                run = store.fetch_run(run_id)
                assert run is not None
                assert run["kind"] == "live"
                assert run["strategy"] == name
                assert run["status"] == "completed"
        finally:
            store.close()

    def test_stop_before_start_is_a_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        trader = AutonomousPaperTrader()
        status = trader.stop()
        assert status["running"] is False

    def test_starting_twice_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        settings = write_config(tmp_path)
        trader = AutonomousPaperTrader()
        trader.start(settings, "test-key", session=FakeContractsSession())
        try:
            with pytest.raises(PaperTraderError, match="already running"):
                trader.start(settings, "test-key", session=FakeContractsSession())
        finally:
            trader.stop(timeout=10)

    def test_concurrent_start_calls_never_both_win(self, tmp_path, monkeypatch):
        """Regression test (Stabilization Mode, 2026-07-28): `start()` used to
        check `self._running` and only set it True *after* all the slow setup
        work (contract detection, feed construction) -- two concurrent calls
        could both pass the check before either actually marked itself
        running, both proceeding to build a feed. `_running` is now claimed
        atomically with the check, before any slow work starts, so at most one
        concurrent caller can ever get past it -- verified here by widening
        the race window with an artificial delay in the contract-lookup call
        (the first slow step) and running two real threads against it."""
        import threading

        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        settings = write_config(tmp_path)
        trader = AutonomousPaperTrader()

        class SlowContractsSession(FakeContractsSession):
            def get(self, url, params=None, timeout=None):
                time.sleep(0.2)
                return super().get(url, params=params, timeout=timeout)

        results: list[object] = []

        def call_start():
            try:
                results.append(trader.start(settings, "test-key", session=SlowContractsSession()))
            except PaperTraderError as exc:
                results.append(exc)

        threads = [threading.Thread(target=call_start) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        try:
            successes = [r for r in results if isinstance(r, dict)]
            failures = [r for r in results if isinstance(r, PaperTraderError)]
            assert len(successes) == 1, f"expected exactly one caller to win, got {len(successes)}: {results}"
            assert len(failures) == 1
            assert "already running" in str(failures[0])
            assert len(FakeMassiveBarFeed.instances) == 1
        finally:
            trader.stop(timeout=10)


class TestRollDetection:
    """`_check_for_roll` must prefer `MarketDataStore.get_active_contract`
    -- the record the data scheduler keeps fresh on its own, much shorter
    cycle -- over making its own independent Contracts API call. Regression
    coverage for the two-rollover-detectors bug (see paper_trader.py's
    module docstring)."""

    def test_picks_up_a_roll_already_recorded_by_the_data_scheduler(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        settings = write_config(tmp_path)

        from futures_bot.market_data.store import MarketDataStore
        store = MarketDataStore(tmp_path / "market_data.db")
        store.set_active_contract("MES", "MESZ6")  # as if the data scheduler already detected a roll
        store.close()

        session = FakeContractsSession()  # always answers "MESU6" -- would be wrong if consulted again
        trader = AutonomousPaperTrader()
        status = trader.start(settings, "test-key", session=session)
        assert status["live_symbol"] == "MESU6"  # start()'s own initial check, unaffected by the pre-seeded store

        try:
            assert _wait_for(lambda: trader.status()["live_symbol"] == "MESZ6")
        finally:
            trader.stop(timeout=10)

        # Picked up straight from the store on the very next cycle -- no
        # second Contracts API call beyond start()'s own initial one.
        assert len(session.calls) == 1

    def test_falls_back_to_the_api_when_the_store_has_no_record_yet_and_seeds_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        settings = write_config(tmp_path)
        session = FakeContractsSession()
        trader = AutonomousPaperTrader()

        trader.start(settings, "test-key", session=session)
        try:
            # start()'s own check, plus the loop's first fallback check
            # (the store has nothing yet).
            assert _wait_for(lambda: len(session.calls) >= 2)
            time.sleep(0.3)  # let several more poll cycles (poll_seconds: 1) pass
        finally:
            trader.stop(timeout=10)

        # The fallback is throttled to once a day -- repeated cycles here
        # must not have kept calling the Contracts API.
        assert len(session.calls) == 2

        from futures_bot.market_data.store import MarketDataStore
        store = MarketDataStore(tmp_path / "market_data.db")
        try:
            assert store.get_active_contract("MES") == "MESU6"
        finally:
            store.close()
