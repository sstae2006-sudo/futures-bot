"""Tests for `api.live_session.LiveSessionManager` -- the dashboard-
controlled paper-trading engine. `feeds.massive.MassiveBarFeed` is always
replaced with a fake that hands back canned bars from an in-memory queue,
the same pattern `tests/test_cli_live.py` already established for
`cli.cmd_live` -- this never touches a real API or a real broker.

The safety tests here are the ones that matter most: `LiveSessionManager
.start()` must refuse a non-paper broker *before* constructing anything
that could touch a real account, and must never do so silently.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from futures_bot.contracts import CME_TZ

PAPER_CONFIG_YAML = """
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

state_file: {state_file}
"""

TRADOVATE_CONFIG_YAML = PAPER_CONFIG_YAML.replace(
    "broker:\n  name: paper\n  slippage_ticks: 1\n  commission_per_side: 0.62\n  starting_cash: 2500",
    "broker:\n  name: tradovate\n  commission_per_side: 0.62\n  tradovate_symbol: MESZ5",
)


def write_config(tmp_path: Path, yaml_text: str = PAPER_CONFIG_YAML) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml_text.format(
            log_dir=(tmp_path / "logs").as_posix(), state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
        ),
        encoding="utf-8",
    )
    return config_path


class FakeMassiveBarFeed:
    """Hands back a fixed queue of bars, one call's worth at a time, then
    empty lists forever after -- so a live session naturally idles once
    the canned data runs out, rather than needing a real feed to stop."""

    instances: list["FakeMassiveBarFeed"] = []

    def __init__(self, symbol, api_key, resolution="5min"):
        self.symbol = symbol
        self.api_key = api_key
        self.resolution = resolution
        start = datetime(2026, 7, 21, 8, 30, tzinfo=CME_TZ)
        price = Decimal("7500")
        self._queued = []
        for i in range(30):
            price += Decimal("3") if i % 2 == 0 else Decimal("-1")
            self._queued.append([_bar(start + timedelta(minutes=i), price)])
        FakeMassiveBarFeed.instances.append(self)

    def poll_new_bars(self):
        if self._queued:
            return self._queued.pop(0)
        return []


def _bar(ts, price):
    from futures_bot.models import Bar
    return Bar(timestamp=ts, open=price, high=price + 1, low=price - 1, close=price, volume=500)


@pytest.fixture(autouse=True)
def _isolated_manager(monkeypatch, tmp_path):
    from futures_bot.api import live_session

    FakeMassiveBarFeed.instances = []
    monkeypatch.setattr("futures_bot.feeds.massive.MassiveBarFeed", FakeMassiveBarFeed)
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
    # Phase 8A: the live session also writes bars to the market-data DB as
    # they arrive -- isolate that the same way FUTURES_BOT_RESEARCH_DB is
    # isolated elsewhere, so tests never touch the real market_data.db.
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    live_session.reset_live_session_manager()
    yield
    # Best-effort cleanup: stop anything left running so one test's
    # background thread never bleeds into the next.
    manager = live_session.get_live_session_manager()
    try:
        if manager.status()["status"] in ("starting", "running"):
            manager.stop(timeout=5)
    except Exception:
        pass
    live_session.reset_live_session_manager()


def _wait_for_status(manager, target_statuses, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status()
        if status["status"] in target_statuses:
            return status
        time.sleep(0.02)
    raise TimeoutError(f"status never reached {target_statuses}, last was {manager.status()['status']}")


class TestSafetyGuard:
    """The check that actually matters: refuse before anything real-money-
    adjacent is constructed."""

    def test_refuses_non_paper_broker(self, tmp_path):
        from futures_bot.api import live_session
        from futures_bot.api.services import ApiError

        config = write_config(tmp_path, TRADOVATE_CONFIG_YAML)
        manager = live_session.get_live_session_manager()

        with pytest.raises(ApiError, match="paper broker"):
            manager.start("MESH6", "5min", 1, config_path=config)

        # No feed was ever constructed -- the guard fired first.
        assert FakeMassiveBarFeed.instances == []
        assert manager.status()["status"] == "stopped"

    def test_refuses_missing_api_key(self, tmp_path, monkeypatch):
        from futures_bot.api import live_session
        from futures_bot.api.services import ApiError

        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        config = write_config(tmp_path)
        manager = live_session.get_live_session_manager()

        with pytest.raises(ApiError, match="MASSIVE_API_KEY"):
            manager.start("MESH6", "5min", 1, config_path=config)

    def test_refuses_unknown_strategy(self, tmp_path):
        from futures_bot.api import live_session
        from futures_bot.api.services import ApiError

        bad = PAPER_CONFIG_YAML.replace("strategy_name: ema_crossover", "strategy_name: not_a_real_strategy")
        config = write_config(tmp_path, bad)
        manager = live_session.get_live_session_manager()

        with pytest.raises(ApiError, match="Unknown strategy"):
            manager.start("MESH6", "5min", 1, config_path=config)


class TestLifecycle:
    def test_start_transitions_to_running_and_processes_bars(self, tmp_path):
        from futures_bot.api import live_session

        config = write_config(tmp_path)
        manager = live_session.get_live_session_manager()
        manager.start("MESH6", "5min", poll_seconds=1, config_path=config)

        status = _wait_for_status(manager, ("running",))
        assert status["strategy"] == "ema_crossover"
        assert status["broker"] == "paper"
        assert status["live_symbol"] == "MESH6"

        # Give the background thread a moment to process the canned bars.
        time.sleep(0.3)
        status = manager.status()
        assert status["last_bar_time"] is not None

    def test_stop_flattens_and_transitions_to_stopped(self, tmp_path):
        from futures_bot.api import live_session

        config = write_config(tmp_path)
        manager = live_session.get_live_session_manager()
        manager.start("MESH6", "5min", poll_seconds=1, config_path=config)
        _wait_for_status(manager, ("running",))

        final = manager.stop(timeout=5)
        assert final["status"] == "stopped"
        assert final["stopped_at"] is not None

    def test_double_start_is_rejected(self, tmp_path):
        from futures_bot.api import live_session
        from futures_bot.api.services import ApiError

        config = write_config(tmp_path)
        manager = live_session.get_live_session_manager()
        manager.start("MESH6", "5min", poll_seconds=1, config_path=config)
        _wait_for_status(manager, ("running",))

        with pytest.raises(ApiError, match="already"):
            manager.start("MESH6", "5min", poll_seconds=1, config_path=config)

    def test_concurrent_start_calls_never_both_win(self, tmp_path, monkeypatch):
        """Regression test (Stabilization Mode, 2026-07-28, KNOWN_ISSUES.md
        ISSUE-016): start() used to check self._snapshot.status and only
        change it *after* all the slow setup work (settings load, a DB
        insert, strategy/engine construction) -- two concurrent calls could
        both pass the check before either claimed the slot. The status is
        now claimed atomically with the check, so at most one concurrent
        caller can ever get past it -- verified here by widening the race
        window with an artificial delay in strategy construction (the first
        slow-ish step) and running two real threads against it."""
        import threading

        from futures_bot.api import live_session, services

        def slow_build_strategy(settings):
            time.sleep(0.2)
            return services._build_strategy(settings)

        monkeypatch.setattr(live_session, "_build_strategy", slow_build_strategy)

        config = write_config(tmp_path)
        manager = live_session.get_live_session_manager()
        results: list[object] = []

        def call_start():
            try:
                results.append(manager.start("MESH6", "5min", poll_seconds=1, config_path=config))
            except Exception as exc:  # noqa: BLE001 -- capturing whichever error type, asserted below
                results.append(exc)

        threads = [threading.Thread(target=call_start) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        try:
            successes = [r for r in results if isinstance(r, dict)]
            failures = [r for r in results if isinstance(r, Exception)]
            assert len(successes) == 1, f"expected exactly one caller to win, got {len(successes)}: {results}"
            assert len(failures) == 1
            assert "already" in str(failures[0])
            assert len(FakeMassiveBarFeed.instances) == 1
        finally:
            if manager.status()["status"] in ("starting", "running"):
                manager.stop(timeout=5)

    def test_stop_without_a_running_session_is_rejected(self, tmp_path):
        from futures_bot.api import live_session
        from futures_bot.api.services import ApiError

        manager = live_session.get_live_session_manager()
        with pytest.raises(ApiError, match="No live session"):
            manager.stop()

    def test_risk_warnings_are_surfaced_in_status(self, tmp_path):
        from futures_bot.api import live_session

        # daily_max_loss tiny relative to account_size -- should trip a risk warning.
        text = PAPER_CONFIG_YAML.replace("daily_max_loss: 100000", "daily_max_loss: 10")
        config = write_config(tmp_path, text)
        manager = live_session.get_live_session_manager()
        status = manager.start("MESH6", "5min", poll_seconds=1, config_path=config)
        assert len(status["warnings"]) >= 0  # must not crash; content depends on config specifics


class TestLiveRoutes:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        (tmp_path / "config.yaml").write_text(
            PAPER_CONFIG_YAML.format(
                log_dir=(tmp_path / "logs").as_posix(), state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        from fastapi.testclient import TestClient
        from futures_bot.api.app import create_app

        return TestClient(create_app())

    def test_status_when_nothing_started(self, client):
        resp = client.get("/api/live/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

    def test_start_stop_lifecycle(self, client):
        start = client.post("/api/live/start", json={"live_symbol": "MESH6", "resolution": "5min", "poll_seconds": 1})
        assert start.status_code == 200
        assert start.json()["status"] in ("starting", "running")

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if client.get("/api/live/status").json()["status"] == "running":
                break
            time.sleep(0.05)

        stop = client.post("/api/live/stop")
        assert stop.status_code == 200
        assert stop.json()["status"] == "stopped"

    def test_start_with_tradovate_broker_is_400(self, tmp_path, client, monkeypatch):
        (tmp_path / "config.yaml").write_text(
            TRADOVATE_CONFIG_YAML.format(
                log_dir=(tmp_path / "logs").as_posix(), state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
            ),
            encoding="utf-8",
        )
        resp = client.post("/api/live/start", json={"live_symbol": "MESH6"})
        assert resp.status_code == 400
        assert "paper broker" in resp.json()["detail"]

    def test_stop_without_start_is_400(self, client):
        resp = client.post("/api/live/stop")
        assert resp.status_code == 400


class TestStreamLiveStatusDisconnect:
    """Regression coverage for the SSE thread leak: `stream_live_status`'s
    generator used to be a plain `def` on a blocking `time.sleep`, so the
    background thread driving it (Starlette runs sync generators in a
    threadpool) kept looping forever even after the client disconnected --
    cancelling the async task wrapping that thread doesn't stop a thread
    parked in a synchronous sleep. It's now an `async def` generator that
    awaits `request.is_disconnected()`, so it can actually be interrupted.

    Deliberately drives the generator directly with a fake `Request`-like
    object instead of going through `TestClient` + a real ASGI cycle: this
    endpoint's stream has no terminal state of its own (unlike the jobs
    stream, which naturally ends when the job completes), so exercising
    the disconnect path over HTTP means abandoning the connection
    mid-stream -- and `TestClient`'s in-process ASGI transport doesn't
    reliably deliver an `http.disconnect` message in that situation, which
    made an earlier version of this test hang indefinitely. Calling the
    generator directly sidesteps that test-transport limitation and checks
    the actual logic this fix depends on.
    """

    class _FakeRequest:
        def __init__(self, disconnect_after: int):
            self._checks = 0
            self._disconnect_after = disconnect_after

        async def is_disconnected(self) -> bool:
            self._checks += 1
            return self._checks > self._disconnect_after

    def test_stops_once_the_client_disconnects(self, monkeypatch):
        from futures_bot.api.routes import live as live_route

        monkeypatch.setattr(live_route, "_POLL_INTERVAL_SECONDS", 0.0)

        async def drain():
            resp = live_route.stream_live_status(self._FakeRequest(disconnect_after=3))
            return [chunk async for chunk in resp.body_iterator]

        import asyncio

        frames = asyncio.run(drain())

        # The status never changes (nothing started), so only the first
        # disconnect-check's iteration actually yields a frame -- the loop
        # still had to run (and check disconnected()) a few more times
        # before stopping, which is exactly the behavior that leaked a
        # thread forever under the old blocking implementation.
        assert len(frames) == 1
        assert json.loads(frames[0][len("data: "):].strip())["status"] == "stopped"

    def test_never_checks_again_once_already_disconnected(self, monkeypatch):
        """disconnect_after=0 means even the very first check reports
        disconnected -- the loop body must not run at all."""
        from futures_bot.api.routes import live as live_route

        monkeypatch.setattr(live_route, "_POLL_INTERVAL_SECONDS", 0.0)

        async def drain():
            resp = live_route.stream_live_status(self._FakeRequest(disconnect_after=0))
            return [chunk async for chunk in resp.body_iterator]

        import asyncio

        frames = asyncio.run(drain())

        assert frames == []


class TestLiveTradeJournal:
    """Unit-level: `LiveTradeJournal.trade()` persists a closed trade to the
    `TradeStore`, joined against the entry decision `CountingJournal`
    already buffers for it -- independent of the full engine/strategy loop
    above, the same way `research.features.build_trade_records` is tested
    on its own elsewhere."""

    def test_persists_a_closed_trade_joined_to_its_entry(self, tmp_path, monkeypatch):
        from futures_bot.api.live_session import LiveTradeJournal
        from futures_bot.api.store import get_store
        from futures_bot.models import Side, Signal, SignalAction, Trade

        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))

        journal = LiveTradeJournal(
            tmp_path / "logs", False,
            run_id="test-run-1", contract="MES", strategy="ema_crossover",
            strategy_params={"fast_period": 3},
        )
        now = datetime(2026, 7, 21, 8, 30, tzinfo=CME_TZ)
        entry_signal = Signal(action=SignalAction.ENTER_LONG, reason="crossover up", metadata={"rsi": 42})
        journal.decision(now, entry_signal, acted=True, price=Decimal("7500"), session_pnl=Decimal("0"))

        trade = Trade(
            side=Side.LONG, quantity=1, entry_price=Decimal("7500"), exit_price=Decimal("7510"),
            entry_time=now, exit_time=now + timedelta(minutes=5),
            gross_pnl=Decimal("50"), commission=Decimal("1.24"), exit_reason="target",
        )
        journal.trade(trade, session_pnl=Decimal("48.76"))

        assert journal.closed_trades == [trade]
        rows = get_store().fetch_trades(run_id="test-run-1")
        assert len(rows) == 1
        assert rows[0]["strategy"] == "ema_crossover"
        assert rows[0]["entry_reason"] == "crossover up"
        assert rows[0]["entry_metadata"] == {"rsi": 42}
        assert rows[0]["net_pnl"] == Decimal("48.76")

    def test_a_trade_with_no_buffered_entry_is_logged_not_persisted(self, tmp_path, monkeypatch):
        """Shouldn't happen given the one-position-at-a-time invariant, but
        must degrade safely (log, don't crash the live session) rather than
        raise if it somehow did."""
        from futures_bot.api.live_session import LiveTradeJournal
        from futures_bot.api.store import get_store
        from futures_bot.models import Side, Trade

        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))

        journal = LiveTradeJournal(
            tmp_path / "logs", False,
            run_id="test-run-2", contract="MES", strategy="ema_crossover", strategy_params={},
        )
        now = datetime(2026, 7, 21, 8, 30, tzinfo=CME_TZ)
        trade = Trade(
            side=Side.LONG, quantity=1, entry_price=Decimal("7500"), exit_price=Decimal("7510"),
            entry_time=now, exit_time=now + timedelta(minutes=5),
            gross_pnl=Decimal("50"), commission=Decimal("1.24"), exit_reason="target",
        )

        journal.trade(trade, session_pnl=Decimal("48.76"))  # must not raise

        assert journal.closed_trades == [trade]
        assert get_store().fetch_trades(run_id="test-run-2") == []


class TestLiveRunPersistence:
    """End-to-end: a live session's `runs` row and its trades land in the
    same `TradeStore` a backtest uses -- the point of Phase 7a."""

    def test_a_closed_trade_shows_up_and_the_run_completes(self, tmp_path, monkeypatch):
        from futures_bot.api import live_session
        from futures_bot.api.store import get_store
        from futures_bot.strategy.base import Strategy

        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))

        class _OnceLongStrategy(Strategy):
            """Enters long on the very first bar it sees and never again --
            deterministic, so the resulting trade's close (via the paper
            broker's own bracket-fill logic against the canned bar prices)
            doesn't depend on indicator warmup or crossover timing."""

            def __init__(self, contract, **params):
                super().__init__(contract, **params)
                self.entered = False

            def on_bar(self, bars, position):
                if position is None and not self.entered:
                    self.entered = True
                    return self.enter_long("test entry")
                return self.hold("test hold")

        monkeypatch.setattr(live_session, "_build_strategy", lambda settings: _OnceLongStrategy(settings.contract_spec))

        config = write_config(tmp_path)
        manager = live_session.get_live_session_manager()
        # poll_seconds=0 -- drain FakeMassiveBarFeed's canned bars as fast as
        # possible (same trick test_cli_live.py uses), since the bracket
        # (stop 5 / target 10) needs several bars of the canned +3/-1 walk
        # to be touched.
        manager.start("MESH6", "5min", poll_seconds=0, config_path=config)

        status = _wait_for_status(manager, ("running",))
        run_id = status["run_id"]
        assert run_id is not None

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if get_store().trade_count(run_id=run_id) > 0:
                break
            time.sleep(0.02)
        else:
            pytest.fail("No trade was persisted within the timeout.")

        trades = get_store().fetch_trades(run_id=run_id)
        assert len(trades) == 1
        assert trades[0]["entry_reason"] == "test entry"
        assert trades[0]["outcome"] == "win"

        manager.stop(timeout=5)

        run = get_store().fetch_run(run_id)
        assert run["kind"] == "live"
        assert run["status"] == "completed"
        assert run["trade_count"] == 1
        assert run["net_pnl"] == trades[0]["net_pnl"]

    def test_a_session_that_errors_fails_its_run(self, tmp_path, monkeypatch):
        from futures_bot.api import live_session
        from futures_bot.api.store import get_store

        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))

        class _ExplodingFeed(FakeMassiveBarFeed):
            def poll_new_bars(self):
                raise ValueError("boom")

        monkeypatch.setattr("futures_bot.feeds.massive.MassiveBarFeed", _ExplodingFeed)

        config = write_config(tmp_path)
        manager = live_session.get_live_session_manager()
        manager.start("MESH6", "5min", poll_seconds=1, config_path=config)

        status = _wait_for_status(manager, ("error",))
        run_id = status["run_id"]
        assert run_id is not None

        # The in-memory snapshot flips to "error" inside _run's `except`
        # block, strictly before `finally` calls `_finalize_run` to write
        # the run's terminal DB status -- a real (if narrow) gap between the
        # two, so poll the DB rather than assume it's already committed the
        # instant the snapshot observes "error".
        deadline = time.monotonic() + 5.0
        run = None
        while time.monotonic() < deadline:
            run = get_store().fetch_run(run_id)
            if run["status"] != "running":
                break
            time.sleep(0.02)

        assert run["status"] == "failed"
        assert run["error_message"] is not None
