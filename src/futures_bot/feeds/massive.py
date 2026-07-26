"""Live bar feed against the Massive API."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import requests

from ..models import Bar
from .base import BarFeed

BASE_URL = "https://api.massive.com/futures/v1/aggs/{symbol}"

_RESOLUTION_RE = re.compile(r"^(\d+)\s*(min|h|hour|d|day)s?$", re.IGNORECASE)
_UNIT_MINUTES = {"min": 1, "h": 60, "hour": 60, "d": 1440, "day": 1440}


def resolution_minutes(resolution: str) -> int:
    match = _RESOLUTION_RE.match(resolution.strip())
    if not match:
        raise ValueError(
            f"Could not parse resolution {resolution!r}. Expected '5min', '1h', or '1d'."
        )

    count, unit = match.groups()
    return int(count) * _UNIT_MINUTES[unit.lower()]


class MassiveBarFeed(BarFeed):
    def __init__(
        self,
        symbol: str,
        api_key: str,
        resolution: str = "5min",
        session: Optional[requests.Session] = None,
        lookback_minutes: int = 1440,
    ) -> None:
        self.symbol = symbol
        self.api_key = api_key
        self.resolution = resolution
        self.session = session or requests.Session()
        self.lookback_minutes = lookback_minutes
        self._resolution_minutes = resolution_minutes(resolution)

        self._last_returned: Optional[datetime] = None

    def poll_new_bars(self, now: Optional[datetime] = None) -> list[Bar]:
        now = now or datetime.now(timezone.utc)

        window_start = self._last_returned or (
            now - timedelta(minutes=self.lookback_minutes)
        )

        raw = self._fetch(window_start, now)

        print(
            "LIVE REQUEST:",
            window_start,
            "to",
            now,
            "raw bars:",
            len(raw),
        )

        bars = [_parse_bar(item) for item in raw]
        bars.sort(key=lambda b: b.timestamp)

        print(
            "PARSED:",
            len(bars),
            "LATEST:",
            bars[-1].timestamp if bars else None,
        )

        new_bars = [
            b
            for b in bars
            if (
                self._last_returned is None
                or b.timestamp > self._last_returned
            )
            and self._is_closed(b, now)
        ]

        print(
            "CLOSED NEW BARS:",
            len(new_bars),
        )

        if new_bars:
            self._last_returned = new_bars[-1].timestamp

        return new_bars

    def _is_closed(self, bar: Bar, now: datetime) -> bool:
        window_end = bar.timestamp + timedelta(
            minutes=self._resolution_minutes
        )
        return window_end <= now

    def _fetch(self, start: datetime, end: datetime) -> list[dict]:
        bars: list[dict] = []

        params = {
            "resolution": self.resolution,
            "window_start.gte": start.isoformat().replace("+00:00", "Z"),
            "window_start.lte": end.isoformat().replace("+00:00", "Z"),
            "limit": 5000,
            "sort": "window_start.asc",
            "apiKey": self.api_key,
        }

        url = BASE_URL.format(symbol=self.symbol)

        while True:
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()

            except requests.RequestException as exc:
                raise RuntimeError(
                    f"Massive API request failed: {exc}"
                ) from exc

            results = data.get("results", [])

            if not results:
                break

            bars.extend(results)

            next_url = data.get("next_url")

            if next_url:
                url = next_url
                params = {"apiKey": self.api_key}

            elif len(results) < params.get("limit", 5000):
                break

            else:
                last_ns = results[-1]["window_start"]
                last_dt = datetime.fromtimestamp(
                    last_ns / 1e9,
                    tz=timezone.utc,
                )

                new_start = last_dt.isoformat().replace("+00:00", "Z")

                if params.get("window_start.gte") == new_start:
                    break

                params["window_start.gte"] = new_start
                url = BASE_URL.format(symbol=self.symbol)

        return bars


def _parse_bar(raw: dict) -> Bar:
    raw_ts = raw["window_start"]

    ts = datetime.fromtimestamp(
        raw_ts / 1e9,
        tz=timezone.utc,
    )

    print(
        "RAW TIMESTAMP:",
        raw_ts,
        "CONVERTED:",
        ts,
    )

    return Bar(
        timestamp=ts,
        open=Decimal(str(raw["open"])),
        high=Decimal(str(raw["high"])),
        low=Decimal(str(raw["low"])),
        close=Decimal(str(raw["close"])),
        volume=int(raw.get("volume", 0)),
    )