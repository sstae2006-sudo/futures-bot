"""Tests for `research_server.nightly_jobs.NightlyJobScheduler` -- job
count/kind per batch, the weekly-comparison trigger, once-per-day
idempotency, and that `run_now()` bypasses the trigger-hour check. Reuses
the real `api.jobs` thread pool (`jobs.reset_executor()`, the same
isolation `tests/test_api_jobs_routes.py` uses) rather than mocking job
submission -- these jobs fail fast (no market data seeded, except in the
one happy-path test) but still exercise the real submission/counting path.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.config import load_settings
from futures_bot.contracts import CME_TZ
from futures_bot.market_data.store import MarketDataStore
from futures_bot.models import Bar
from futures_bot.research_server.nightly_jobs import NightlyJobScheduler

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
broker:
  name: paper
  starting_cash: 2500
logging:
  level: WARNING
  directory: {log_dir}
strategy_name: ema_crossover
strategy_params:
  fast_period: 3
  slow_period: 5
research_server:
  enabled: true
  paper_strategies: [ema_crossover, vwap_reversion]
  data_sync_products: [MES]
  resolution: 5min
  nightly_job_hour_ct: 2
  weekly_report_weekday: 6
state_file: {state_file}
"""


def write_config(tmp_path, yaml_text=CONFIG_YAML):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml_text.format(
            log_dir=(tmp_path / "logs").as_posix(), state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
        ),
        encoding="utf-8",
    )
    return load_settings(config_path)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from futures_bot.api import jobs

    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    jobs.reset_executor()
    yield
    jobs.reset_executor()


class TestRunNow:
    def test_submits_three_jobs_per_strategy_on_a_non_weekly_day(self, tmp_path):
        settings = write_config(tmp_path)
        scheduler = NightlyJobScheduler()
        monday = datetime(2026, 7, 20, 2, 0, tzinfo=CME_TZ)  # Monday -- not weekly_report_weekday (Sunday=6)

        summary = scheduler.run_now(settings, now_ct=monday)

        # 2 strategies x (backtest + optimizer + walk-forward) = 6, no compare.
        assert "6 job(s)" in summary
        assert scheduler.status()["last_run_date"] == monday.date().isoformat()

    def test_adds_a_weekly_comparison_on_the_configured_weekday(self, tmp_path):
        settings = write_config(tmp_path)
        scheduler = NightlyJobScheduler()
        sunday = datetime(2026, 7, 26, 2, 0, tzinfo=CME_TZ)  # Sunday == weekday 6

        summary = scheduler.run_now(settings, now_ct=sunday)

        assert "7 job(s)" in summary  # 6 + 1 compare

    def test_no_strategies_submits_nothing(self, tmp_path):
        empty = CONFIG_YAML.replace(
            "paper_strategies: [ema_crossover, vwap_reversion]", "paper_strategies: []"
        )
        settings = write_config(tmp_path, empty)
        scheduler = NightlyJobScheduler()

        summary = scheduler.run_now(settings, now_ct=datetime(2026, 7, 20, 2, 0, tzinfo=CME_TZ))

        assert "0 job(s)" in summary

    def test_produces_a_completed_run_and_report_with_real_data(self, tmp_path):
        """The one happy-path test: seed real bars so the walk-forward job
        actually completes and a report gets generated."""
        store = MarketDataStore(tmp_path / "market_data.db")
        base = datetime(2026, 1, 5, 8, 30, tzinfo=CME_TZ)
        bars = []
        price = Decimal("7500")
        for i in range(600):
            price += Decimal("3") if i % 4 < 2 else Decimal("-2")
            ts = base + timedelta(minutes=5 * i)
            bars.append(Bar(timestamp=ts, open=price, high=price + 2, low=price - 2, close=price, volume=500))
        store.upsert_bars("MES", "MESH6", "5min", "test", bars)
        store.close()

        only_ema = CONFIG_YAML.replace("paper_strategies: [ema_crossover, vwap_reversion]", "paper_strategies: [ema_crossover]")
        settings = write_config(tmp_path, only_ema)
        scheduler = NightlyJobScheduler()

        scheduler.run_now(settings, now_ct=datetime(2026, 7, 20, 2, 0, tzinfo=CME_TZ))

        from futures_bot.api import services
        deadline = time.monotonic() + 30
        walk_forward_runs = []
        while time.monotonic() < deadline:
            walk_forward_runs = [r for r in services.list_runs(strategy="ema_crossover", kind="walk_forward") if r.status == "completed"]
            if walk_forward_runs:
                break
            time.sleep(0.2)
        assert walk_forward_runs, "walk-forward run never completed"

        deadline = time.monotonic() + 15
        reports = []
        while time.monotonic() < deadline:
            reports = services.list_reports(run_id=walk_forward_runs[0].id)
            if reports:
                break
            time.sleep(0.2)
        assert reports, "no report was generated for the completed walk-forward run"


class TestLifecycleAndIdempotency:
    def test_start_and_stop_without_leaking_a_thread(self, tmp_path):
        settings = write_config(tmp_path)
        scheduler = NightlyJobScheduler()
        scheduler.start(settings, check_interval_seconds=1)
        assert scheduler.status()["running"] is True

        scheduler.stop(timeout=5)
        assert scheduler.status()["running"] is False

    def test_starting_twice_raises(self, tmp_path):
        settings = write_config(tmp_path)
        scheduler = NightlyJobScheduler()
        scheduler.start(settings, check_interval_seconds=1)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                scheduler.start(settings, check_interval_seconds=1)
        finally:
            scheduler.stop(timeout=5)

    def test_loop_does_not_resubmit_within_the_same_day(self, tmp_path, monkeypatch):
        """Directly exercises the loop's date-gate: a manual run_now()
        already recorded today; the loop, ticking during the trigger hour
        on the same day, must not call _run_batch again. The trigger hour
        is set to the real current CT hour so the loop's own hour check
        actually matches during the test, rather than trivially never
        firing at all."""
        current_hour_ct = datetime.now(CME_TZ).hour
        settings = write_config(
            tmp_path, CONFIG_YAML.replace("nightly_job_hour_ct: 2", f"nightly_job_hour_ct: {current_hour_ct}")
        )
        scheduler = NightlyJobScheduler()

        calls = []
        monkeypatch.setattr(scheduler, "_run_batch", lambda *a, **k: calls.append(1) or "0 job(s) submitted")

        now = datetime.now(CME_TZ)
        scheduler.run_now(settings, now_ct=now)
        assert len(calls) == 1

        scheduler.start(settings, check_interval_seconds=1)
        time.sleep(0.3)  # a few loop ticks, all within the same trigger hour
        scheduler.stop(timeout=5)

        assert len(calls) == 1  # still just the manual run -- the loop never re-triggered today
