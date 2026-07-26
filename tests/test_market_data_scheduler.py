"""Tests for `market_data.scheduler.MarketDataScheduler` -- the background
thread that keeps the local DB current. Verifies it only syncs when the
market is open, survives one target's failure without stopping the others,
skips a cycle cleanly when no API key is available, and starts/stops
without leaking a thread.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from futures_bot.market_data.scheduler import MarketDataScheduler, SyncTarget, get_scheduler, reset_scheduler
from futures_bot.market_data.store import MarketDataStore


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    reset_scheduler()
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    yield
    reset_scheduler()


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestLifecycle:
    def test_start_and_stop_without_leaking_a_thread(self, tmp_path):
        scheduler = MarketDataScheduler(tmp_path / "market_data.db", lambda: "")
        scheduler.start([SyncTarget("MES", "5min")], interval_seconds=1)
        assert scheduler.status()["running"] is True

        scheduler.stop(timeout=5)
        assert scheduler.status()["running"] is False

    def test_starting_twice_raises(self, tmp_path):
        scheduler = MarketDataScheduler(tmp_path / "market_data.db", lambda: "")
        scheduler.start([SyncTarget("MES", "5min")], interval_seconds=1)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                scheduler.start([SyncTarget("MES", "5min")], interval_seconds=1)
        finally:
            scheduler.stop(timeout=5)


class TestCycleBehavior:
    def test_skips_a_cycle_when_market_is_closed(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "futures_bot.market_data.scheduler.sync_incremental",
            lambda *a, **k: calls.append(1),
        )
        monkeypatch.setattr("futures_bot.market_data.scheduler.is_market_open", lambda now: False)

        scheduler = MarketDataScheduler(tmp_path / "market_data.db", lambda: "test-key")
        scheduler.start([SyncTarget("MES", "5min")], interval_seconds=1)
        time.sleep(0.3)
        scheduler.stop(timeout=5)

        assert calls == []
        assert scheduler.status()["cycles_completed"] == 0

    def test_runs_a_cycle_when_market_is_open(self, tmp_path, monkeypatch):
        from futures_bot.market_data.sync import SyncResult

        monkeypatch.setattr("futures_bot.market_data.scheduler.is_market_open", lambda now: True)
        monkeypatch.setattr(
            "futures_bot.market_data.scheduler.sync_incremental",
            lambda store, api_key, product_code, resolution, now=None: SyncResult(
                run_id="r1", product_code=product_code, resolution=resolution, bars_fetched=3,
            ),
        )

        scheduler = MarketDataScheduler(tmp_path / "market_data.db", lambda: "test-key")
        scheduler.start([SyncTarget("MES", "5min")], interval_seconds=1)
        assert _wait_until(lambda: scheduler.status()["cycles_completed"] >= 1)
        scheduler.stop(timeout=5)

        status = scheduler.status()
        assert "3 new bars" in status["last_result"]
        assert status["last_error"] is None

    def test_one_targets_failure_does_not_stop_the_others(self, tmp_path, monkeypatch):
        monkeypatch.setattr("futures_bot.market_data.scheduler.is_market_open", lambda now: True)

        def fake_sync(store, api_key, product_code, resolution, now=None):
            if product_code == "MES":
                raise RuntimeError("boom")
            from futures_bot.market_data.sync import SyncResult
            return SyncResult(run_id="r1", product_code=product_code, resolution=resolution, bars_fetched=1)

        monkeypatch.setattr("futures_bot.market_data.scheduler.sync_incremental", fake_sync)

        scheduler = MarketDataScheduler(tmp_path / "market_data.db", lambda: "test-key")
        scheduler.start([SyncTarget("MES", "5min"), SyncTarget("MNQ", "5min")], interval_seconds=1)
        assert _wait_until(lambda: scheduler.status()["cycles_completed"] >= 1)
        scheduler.stop(timeout=5)

        status = scheduler.status()
        assert "MES:5min=FAILED" in status["last_result"]
        assert "MNQ:5min=1 new bars" in status["last_result"]

    def test_missing_api_key_skips_the_cycle_with_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("futures_bot.market_data.scheduler.is_market_open", lambda now: True)

        scheduler = MarketDataScheduler(tmp_path / "market_data.db", lambda: "")
        scheduler.start([SyncTarget("MES", "5min")], interval_seconds=1)
        assert _wait_until(lambda: scheduler.status()["last_error"] is not None)
        scheduler.stop(timeout=5)

        assert "MASSIVE_API_KEY" in scheduler.status()["last_error"]
        assert scheduler.status()["cycles_completed"] == 0


class TestGlobalAccessor:
    def test_get_scheduler_requires_args_the_first_time(self):
        with pytest.raises(RuntimeError, match="needs db_path"):
            get_scheduler()

    def test_get_scheduler_returns_the_same_instance(self, tmp_path):
        first = get_scheduler(tmp_path / "market_data.db", lambda: "")
        second = get_scheduler()
        assert first is second
