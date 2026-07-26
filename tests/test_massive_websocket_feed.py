"""Tests for `feeds.massive_websocket.MassiveWebSocketBarFeed` -- the
delayed-data WebSocket feed, a second `BarFeed` implementation alongside
the REST-polling `MassiveBarFeed`. A fake `connect` callable (matching
`websockets.connect`'s async-context-manager interface) stands in for the
real service, the same `Fake*` pattern this codebase already uses for
every other external service (`FakeMassiveBarFeed`, `FakeContractsSession`,
`FakeSyncSession`, ...) -- nothing here touches the real endpoint.

Message shapes below are exactly what was captured live against the real
endpoint during development (see `feeds/massive_websocket.py`'s module
docstring): every message is a JSON array of one event.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from futures_bot.feeds.massive_websocket import MassiveWebSocketBarFeed, MassiveWebSocketBarFeedError

STATUS_CONNECTED = json.dumps([{"ev": "status", "status": "connected", "message": "Connected Successfully"}])
AUTH_SUCCESS = json.dumps([{"ev": "status", "status": "auth_success", "message": "authenticated"}])


def _subscribe_ack(ticker: str) -> str:
    return json.dumps([{"ev": "status", "status": "success", "message": f"subscribed to: AM.{ticker}"}])


def _unsubscribe_ack(ticker: str) -> str:
    return json.dumps([{"ev": "status", "status": "success", "message": f"unsubscribed from: AM.{ticker}"}])


def _not_authorized() -> str:
    return json.dumps([{"ev": "status", "status": "error", "message": "not authorized"}])


def _am_event(ticker: str = "MESU6", start_ms: int = 1784869680000, close: str = "7447.5") -> str:
    return json.dumps([{
        "ev": "AM", "sym": ticker, "v": 286, "dv": "2129868.25", "n": 92,
        "o": "7446.5", "c": close, "h": "7447.5", "l": "7446.5",
        "s": start_ms, "e": start_ms + 60000,
    }])


class FakeWebSocketConnection:
    """One simulated connection: a fixed queue of incoming messages, and a
    record of everything sent to it. `fail_after` raises a connection error
    on the Nth `recv()` (1-indexed) to simulate a mid-session drop."""

    def __init__(self, incoming: list[str], fail_after: "int | None" = None):
        self.incoming = list(incoming)
        self.sent: list[str] = []
        self._recv_count = 0
        self.fail_after = fail_after

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        self._recv_count += 1
        if self.fail_after is not None and self._recv_count > self.fail_after:
            raise ConnectionError("simulated connection drop")
        if not self.incoming:
            await asyncio.sleep(3600)  # "no more messages" -- let _pump's own recv timeout govern
        return self.incoming.pop(0)


class _FakeConnectionContext:
    def __init__(self, connection: FakeWebSocketConnection):
        self._connection = connection

    async def __aenter__(self) -> FakeWebSocketConnection:
        return self._connection

    async def __aexit__(self, *exc_info) -> bool:
        return False


class FakeConnect:
    """Callable matching `websockets.connect(url)` -- returns each
    connection in order on successive calls (the last one repeats if
    called more times than provided), so a reconnect after a drop gets the
    next fake connection in the sequence."""

    def __init__(self, connections: list[FakeWebSocketConnection]):
        self.connections = connections
        self.calls = 0

    def __call__(self, url: str) -> _FakeConnectionContext:
        index = min(self.calls, len(self.connections) - 1)
        self.calls += 1
        return _FakeConnectionContext(self.connections[index])


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestStartAndHandshake:
    def test_start_connects_authenticates_and_subscribes(self):
        conn = FakeWebSocketConnection([STATUS_CONNECTED, AUTH_SUCCESS, _subscribe_ack("MESU6")])
        feed = MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", connect=FakeConnect([conn]))
        feed.start()
        try:
            assert _wait_for(lambda: len(conn.sent) >= 2)
            assert json.loads(conn.sent[0]) == {"action": "auth", "params": "test-key"}
            assert json.loads(conn.sent[1]) == {"action": "subscribe", "params": "AM.MESU6"}
        finally:
            feed.stop()

    def test_not_authorized_on_subscribe_raises_fail_fast(self):
        conn = FakeWebSocketConnection([STATUS_CONNECTED, AUTH_SUCCESS, _not_authorized()])
        feed = MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", connect=FakeConnect([conn]))

        with pytest.raises(MassiveWebSocketBarFeedError, match="not authorized"):
            feed.start()

    def test_only_1min_resolution_is_supported(self):
        with pytest.raises(ValueError, match="1-minute"):
            MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", resolution="5min")


class TestPollNewBars:
    def test_delivers_a_correctly_parsed_bar(self):
        """Regression test for the millisecond-vs-nanosecond timestamp unit
        bug this feed must not repeat from the REST endpoint's parser."""
        conn = FakeWebSocketConnection([
            STATUS_CONNECTED, AUTH_SUCCESS, _subscribe_ack("MESU6"),
            _am_event(start_ms=1784869680000, close="7447.5"),
        ])
        feed = MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", connect=FakeConnect([conn]))
        feed.start()
        try:
            bars = []
            assert _wait_for(lambda: (bars.extend(feed.poll_new_bars()), len(bars))[-1] >= 1)
            assert len(bars) == 1
            assert bars[0].close == Decimal("7447.5")
            assert bars[0].timestamp == datetime(2026, 7, 24, 5, 8, tzinfo=timezone.utc)
        finally:
            feed.stop()

    def test_returns_empty_when_nothing_new_has_arrived(self):
        conn = FakeWebSocketConnection([STATUS_CONNECTED, AUTH_SUCCESS, _subscribe_ack("MESU6")])
        feed = MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", connect=FakeConnect([conn]))
        feed.start()
        try:
            assert feed.poll_new_bars() == []
        finally:
            feed.stop()

    def test_drains_multiple_accumulated_bars_in_order(self):
        conn = FakeWebSocketConnection([
            STATUS_CONNECTED, AUTH_SUCCESS, _subscribe_ack("MESU6"),
            _am_event(start_ms=1784869680000, close="7447.5"),
            _am_event(start_ms=1784869740000, close="7446.0"),
        ])
        feed = MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", connect=FakeConnect([conn]))
        feed.start()
        try:
            bars = []
            assert _wait_for(lambda: (bars.extend(feed.poll_new_bars()), len(bars))[-1] >= 2)
            assert [b.close for b in bars] == [Decimal("7447.5"), Decimal("7446.0")]
        finally:
            feed.stop()


class TestSymbolChange:
    def test_setting_symbol_unsubscribes_old_and_subscribes_new(self):
        conn = FakeWebSocketConnection([
            STATUS_CONNECTED, AUTH_SUCCESS, _subscribe_ack("MESU6"),
            _unsubscribe_ack("MESU6"), _subscribe_ack("MESZ6"),
        ])
        feed = MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", connect=FakeConnect([conn]))
        feed.start()
        try:
            feed.symbol = "MESZ6"
            assert _wait_for(lambda: len(conn.sent) >= 4)
            assert json.loads(conn.sent[2]) == {"action": "unsubscribe", "params": "AM.MESU6"}
            assert json.loads(conn.sent[3]) == {"action": "subscribe", "params": "AM.MESZ6"}
            assert feed.symbol == "MESZ6"
        finally:
            feed.stop()

    def test_setting_the_same_symbol_is_a_no_op(self):
        conn = FakeWebSocketConnection([STATUS_CONNECTED, AUTH_SUCCESS, _subscribe_ack("MESU6")])
        feed = MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", connect=FakeConnect([conn]))
        feed.start()
        try:
            assert _wait_for(lambda: len(conn.sent) >= 2)
            sent_before = len(conn.sent)
            feed.symbol = "MESU6"
            time.sleep(0.1)
            assert len(conn.sent) == sent_before
        finally:
            feed.stop()


class TestReconnect:
    def test_reconnects_after_a_dropped_connection_and_keeps_delivering_bars(self):
        # First connection: completes the handshake, then dies on the next
        # recv (the pump's first read) -- fail_after=3 counts the 3
        # handshake recv() calls (status, auth, subscribe-ack).
        conn1 = FakeWebSocketConnection(
            [STATUS_CONNECTED, AUTH_SUCCESS, _subscribe_ack("MESU6")], fail_after=3,
        )
        conn2 = FakeWebSocketConnection([
            STATUS_CONNECTED, AUTH_SUCCESS, _subscribe_ack("MESU6"), _am_event(),
        ])
        feed = MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", connect=FakeConnect([conn1, conn2]))
        feed.start()
        try:
            bars = []
            assert _wait_for(
                lambda: (bars.extend(feed.poll_new_bars()), len(bars))[-1] >= 1, timeout=10.0
            )
            assert len(bars) == 1
        finally:
            feed.stop()


class TestStop:
    def test_stop_joins_the_background_thread(self):
        conn = FakeWebSocketConnection([STATUS_CONNECTED, AUTH_SUCCESS, _subscribe_ack("MESU6")])
        feed = MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", connect=FakeConnect([conn]))
        feed.start()
        thread = feed._thread

        feed.stop()

        assert thread is not None
        assert not thread.is_alive()

    def test_poll_after_stop_does_not_hang_or_raise(self):
        conn = FakeWebSocketConnection([STATUS_CONNECTED, AUTH_SUCCESS, _subscribe_ack("MESU6")])
        feed = MassiveWebSocketBarFeed(symbol="MESU6", api_key="test-key", connect=FakeConnect([conn]))
        feed.start()
        feed.stop()

        assert feed.poll_new_bars() == []
