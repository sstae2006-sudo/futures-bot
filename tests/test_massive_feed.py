"""Tests for `feeds.massive.MassiveBarFeed` against a mocked HTTP session.

Focus: the "never return a still-forming bar" invariant `BarFeed`'s
docstring states, and that repeated polls never re-return a bar already
handed back once.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from futures_bot.feeds.massive import MassiveBarFeed, resolution_minutes


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, pages: list[dict]):
        """``pages`` is consumed one response per call to `.get`; the last
        page is reused for any calls beyond the list's length."""
        self.pages = pages
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        index = min(len(self.calls) - 1, len(self.pages) - 1)
        return FakeResponse(self.pages[index])


def _bar_payload(window_start: datetime, close=7500.0):
    ns = int(window_start.timestamp() * 1e9)
    return {"window_start": ns, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 500}


class TestResolutionMinutes:
    @pytest.mark.parametrize("text,expected", [
        ("5min", 5), ("1min", 1), ("60min", 60), ("1h", 60), ("2hour", 120), ("1d", 1440), ("1day", 1440),
    ])
    def test_parses_common_forms(self, text, expected):
        assert resolution_minutes(text) == expected

    def test_rejects_unparseable_string(self):
        with pytest.raises(ValueError):
            resolution_minutes("fortnight")


class TestMassiveBarFeed:
    def test_returns_only_fully_closed_bars(self):
        now = datetime(2026, 1, 5, 14, 37, tzinfo=timezone.utc)
        closed_bar_start = now - timedelta(minutes=10)   # 14:27 -> window ends 14:32, fully in the past
        forming_bar_start = now - timedelta(minutes=2)    # 14:35 -> window ends 14:40, still forming

        session = FakeSession([{"results": [
            _bar_payload(closed_bar_start),
            _bar_payload(forming_bar_start),
        ]}])
        feed = MassiveBarFeed(symbol="MESH6", api_key="key", resolution="5min", session=session)
        feed._fetch = lambda start, end: session.get("x").json()["results"]

        bars = feed.poll_new_bars(now)
        assert len(bars) == 1
        assert bars[0].timestamp == closed_bar_start

    def test_does_not_return_the_same_bar_twice(self):
        now = datetime(2026, 1, 5, 14, 37, tzinfo=timezone.utc)
        bar_start = now - timedelta(minutes=10)
        session = FakeSession([{"results": [_bar_payload(bar_start)]}])
        feed = MassiveBarFeed(symbol="MESH6", api_key="key", resolution="5min", session=session)
        feed._fetch = lambda start, end: [_bar_payload(bar_start)]

        first = feed.poll_new_bars(now)
        second = feed.poll_new_bars(now)
        assert len(first) == 1
        assert second == []

    def test_returns_bars_in_chronological_order(self):
        now = datetime(2026, 1, 5, 14, 37, tzinfo=timezone.utc)
        earlier = now - timedelta(minutes=20)
        later = now - timedelta(minutes=10)
        feed = MassiveBarFeed(symbol="MESH6", api_key="key", resolution="5min", session=FakeSession([]))
        # Deliberately out of order in the "API response" to prove the feed sorts.
        feed._fetch = lambda start, end: [_bar_payload(later), _bar_payload(earlier)]

        bars = feed.poll_new_bars(now)
        assert [b.timestamp for b in bars] == [earlier, later]

    def test_empty_when_nothing_new(self):
        now = datetime(2026, 1, 5, 14, 37, tzinfo=timezone.utc)
        feed = MassiveBarFeed(symbol="MESH6", api_key="key", resolution="5min", session=FakeSession([]))
        feed._fetch = lambda start, end: []
        assert feed.poll_new_bars(now) == []

    def test_request_includes_api_key_and_symbol(self):
        session = FakeSession([{"results": []}])
        feed = MassiveBarFeed(symbol="MESH6", api_key="secret-key", resolution="5min", session=session)
        feed._fetch(datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc))
        assert session.calls[0]["params"]["apiKey"] == "secret-key"
        assert "MESH6" in session.calls[0]["url"]
