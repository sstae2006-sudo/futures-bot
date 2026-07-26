"""Tests for `research_server.orchestrator.ResearchServer` -- composing
`MarketDataScheduler`, `AutonomousPaperTrader`, and `NightlyJobScheduler`
behind one start/stop/status, and that it shares the *same*
`MarketDataScheduler` singleton the Market Data dashboard page's manual
controls use (not a second, independently-running one). Uses the same
`FakeMassiveBarFeed`/`FakeContractsSession` fakes
`test_research_server_paper_trader.py` established, monkeypatched onto
the module `AutonomousPaperTrader.start()` actually calls into.
"""

from __future__ import annotations

import pytest

from futures_bot.config import load_settings
from futures_bot.market_data.scheduler import reset_scheduler as reset_market_data_scheduler
from futures_bot.research_server.nightly_jobs import reset_nightly_job_scheduler
from futures_bot.research_server.orchestrator import (
    ResearchServer, ResearchServerError, get_research_server, reset_research_server,
)
from futures_bot.research_server.paper_trader import reset_paper_trader
from tests.test_research_server_paper_trader import CONFIG_YAML, FakeMassiveBarFeed, write_config

# Patch AutonomousPaperTrader.start to accept a fake Contracts session by
# default -- reuse the exact fake from the paper-trader test suite so a
# real network call is never made here either.
from tests.test_research_server_paper_trader import FakeContractsSession


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    FakeMassiveBarFeed.instances = []
    monkeypatch.setattr("futures_bot.feeds.massive.MassiveBarFeed", FakeMassiveBarFeed)
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))

    # AutonomousPaperTrader.start() builds its own `requests.Session()` when
    # the caller (ResearchServer) doesn't pass one -- ResearchServer.start()
    # doesn't expose a session parameter (production always wants a real
    # one), so patch the Contracts API client's session construction itself.
    import requests
    monkeypatch.setattr(requests, "Session", FakeContractsSession)

    # ResearchServer now reaches its subsystems exclusively through their own
    # module-level singletons (see orchestrator.py's docstring on why) --
    # each test needs a fresh paper trader / nightly-job scheduler too, not
    # just a fresh ResearchServer/data-scheduler, or state leaks across tests.
    reset_research_server()
    reset_market_data_scheduler()
    reset_paper_trader()
    reset_nightly_job_scheduler()
    yield
    reset_research_server()
    reset_market_data_scheduler()
    reset_paper_trader()
    reset_nightly_job_scheduler()


class TestLifecycle:
    def test_start_composes_all_three_subsystems(self, tmp_path):
        settings = write_config(tmp_path)
        server = ResearchServer()

        status = server.start(settings, "test-key")

        assert status["running"] is True
        assert status["data_scheduler"]["running"] is True
        assert status["data_scheduler"]["targets"] == ["MES:5min"]
        assert status["paper_trader"]["running"] is True
        assert set(status["paper_trader"]["strategies"]) == {"ema_crossover", "vwap_reversion"}
        assert status["nightly_jobs"]["running"] is True
        assert status["uptime_seconds"] is not None

        server.stop()
        status = server.status()
        assert status["running"] is False
        assert status["paper_trader"]["running"] is False
        assert status["nightly_jobs"]["running"] is False

    def test_starting_twice_raises(self, tmp_path):
        settings = write_config(tmp_path)
        server = ResearchServer()
        server.start(settings, "test-key")
        try:
            with pytest.raises(ResearchServerError, match="already running"):
                server.start(settings, "test-key")
        finally:
            server.stop()

    def test_stop_before_start_is_a_no_op(self, tmp_path):
        server = ResearchServer()
        status = server.stop()
        assert status["running"] is False

    def test_a_failure_starting_the_last_subsystem_rolls_back_the_earlier_ones(self, tmp_path):
        """If nightly_jobs.start() fails after the paper trader already
        started, the paper trader must not be left running and unreachable
        -- the exact orphaned-thread bug the rollback exists to close.
        Triggers a *real* failure (not a mock): pre-start the shared nightly
        job scheduler singleton so ResearchServer.start()'s own attempt to
        start it hits the same "already running" RuntimeError a genuine
        failure would."""
        from futures_bot.research_server.nightly_jobs import get_nightly_job_scheduler
        from futures_bot.research_server.paper_trader import get_paper_trader

        settings = write_config(tmp_path)
        get_nightly_job_scheduler().start(settings, check_interval_seconds=60)
        try:
            server = ResearchServer()

            with pytest.raises(RuntimeError, match="already running"):
                server.start(settings, "test-key")

            assert server.status()["running"] is False
            assert get_paper_trader().status()["running"] is False, (
                "the paper trader started before the failing step and must have been rolled back"
            )
        finally:
            get_nightly_job_scheduler().stop()

    def test_no_paper_strategies_still_starts_data_sync_and_nightly_jobs(self, tmp_path):
        no_paper = CONFIG_YAML.replace("paper_strategies: [ema_crossover, vwap_reversion]", "paper_strategies: []")
        settings = write_config(tmp_path, no_paper)
        server = ResearchServer()

        status = server.start(settings, "test-key")

        assert status["data_scheduler"]["running"] is True
        assert status["paper_trader"]["running"] is False
        assert status["nightly_jobs"]["running"] is True

        server.stop()


class TestSharedDataScheduler:
    def test_does_not_stop_a_data_scheduler_it_did_not_start(self, tmp_path):
        """If the Market Data dashboard already had the scheduler running
        independently, ResearchServer.stop() must not yank it away."""
        from futures_bot.market_data.scheduler import SyncTarget, get_scheduler

        already_running = get_scheduler(tmp_path / "market_data.db", lambda: "test-key")
        already_running.start([SyncTarget("MES", "5min")], interval_seconds=60)
        try:
            settings = write_config(tmp_path)
            server = ResearchServer()
            server.start(settings, "test-key")

            server.stop()

            assert already_running.status()["running"] is True
        finally:
            already_running.stop()


class TestGlobalAccessor:
    def test_get_research_server_returns_the_same_instance(self):
        first = get_research_server()
        second = get_research_server()
        assert first is second
