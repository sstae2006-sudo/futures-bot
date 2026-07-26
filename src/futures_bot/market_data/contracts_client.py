"""Client for Massive's Contracts API (`GET /futures/v1/contracts`) --
auto-detects which specific expiry ticker is the front-month contract for a
product on a given date, so `sync.py` never has to be told "MESU6" by hand.

Response shape (confirmed against the live API, not just the docs prose):
one row per (ticker, query-date) snapshot. Two `type`s show up: `"single"`
(an actual outright futures contract -- what this module cares about) and
`"combo"` (a calendar-spread instrument like `"MESH7-MESM7"`, filtered out
here). Each `"single"` row carries a *static* `first_trade_date`/
`last_trade_date` for that ticker (the same regardless of which date you
queried it at) plus a `days_to_maturity` that's relative to the query date.
The front-month contract for a date is the `"single"` whose window contains
that date and whose `last_trade_date` is soonest -- equivalently, the
smallest `days_to_maturity` when queried at that exact date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import requests

BASE_URL = "https://api.massive.com/futures/v1/contracts"


@dataclass(frozen=True)
class ContractInfo:
    ticker: str
    product_code: str
    first_trade_date: date
    last_trade_date: date


class ContractsApiError(RuntimeError):
    pass


class MassiveContractsClient:
    def __init__(self, api_key: str, session: Optional[requests.Session] = None) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()

    def list_single_contracts(self, product_code: str, as_of: date) -> list[ContractInfo]:
        """Every outright ("single") contract Massive considers relevant as
        of ``as_of`` -- typically the current front month plus one or two
        deferred quarters, per the API's own behavior (confirmed live: a
        query at today's date returned exactly 3 singles for MES). Paginates
        via `next_url` defensively, but in practice a single page already
        contains every `"single"` row -- the rest of the page is `"combo"`
        spread instruments this module has no use for."""
        contracts: dict[str, ContractInfo] = {}
        params = {"product_code": product_code, "date": as_of.isoformat(), "limit": 250, "apiKey": self.api_key}
        url = BASE_URL

        while True:
            try:
                response = self.session.get(url, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as exc:
                raise ContractsApiError(f"Massive Contracts API request failed: {exc}") from exc

            for row in data.get("results", []):
                if row.get("type") != "single":
                    continue
                ticker = row["ticker"]
                if ticker in contracts:
                    continue
                contracts[ticker] = ContractInfo(
                    ticker=ticker,
                    product_code=row["product_code"],
                    first_trade_date=date.fromisoformat(row["first_trade_date"]),
                    last_trade_date=date.fromisoformat(row["last_trade_date"]),
                )

            next_url = data.get("next_url")
            if not next_url:
                break
            url, params = next_url, {"apiKey": self.api_key}

        return sorted(contracts.values(), key=lambda c: c.last_trade_date)

    def active_contract(self, product_code: str, as_of: Optional[date] = None) -> ContractInfo:
        """The front-month contract for ``as_of`` (default: today) -- the
        `"single"` whose trading window contains that date and whose
        `last_trade_date` is soonest. Raises if none qualifies (e.g. the
        product code is unknown to Massive, or every listed contract has
        already expired relative to ``as_of``)."""
        as_of = as_of or datetime.now().date()
        candidates = [
            c for c in self.list_single_contracts(product_code, as_of)
            if c.first_trade_date <= as_of <= c.last_trade_date
        ]
        if not candidates:
            raise ContractsApiError(
                f"No active contract found for {product_code!r} as of {as_of.isoformat()}. "
                f"Massive's Contracts API returned no 'single' contract whose trading window covers that date."
            )
        return min(candidates, key=lambda c: c.last_trade_date)

    def front_month_schedule(self, product_code: str, start: date, end: date) -> list[tuple[date, date, str]]:
        """A [(window_start, window_end, ticker), ...] schedule covering
        [start, end] -- which contract was front-month on every date in the
        range, without one Contracts API call per day. Samples the API at
        roughly quarterly intervals across the range (contracts stay listed,
        with their true static first/last trade dates, well before and after
        they're actually front-month -- see the module docstring), unions
        the distinct contracts seen, then computes the day-by-day schedule
        purely in Python from those static windows. This is what makes
        `sync.backfill` able to write the *correct* front-month ticker for
        every historical bar without guessing, and without one API call per
        day of history.
        """
        if start > end:
            raise ValueError(f"start ({start}) must not be after end ({end})")

        contracts: dict[str, ContractInfo] = {}
        sample = start
        step_days = 80  # comfortably shorter than one quarterly cycle (~91 days)
        while sample <= end:
            for c in self.list_single_contracts(product_code, sample):
                contracts.setdefault(c.ticker, c)
            sample = date.fromordinal(sample.toordinal() + step_days)
        # Always sample the end date too, so the schedule's tail isn't
        # missing whatever became front-month right at the boundary.
        for c in self.list_single_contracts(product_code, end):
            contracts.setdefault(c.ticker, c)

        ordered = sorted(contracts.values(), key=lambda c: c.last_trade_date)

        schedule: list[tuple[date, date, str]] = []
        cursor = start
        for c in ordered:
            if cursor > end:
                break
            window_start = max(cursor, c.first_trade_date)
            window_end = min(end, c.last_trade_date)
            if window_start > window_end:
                continue
            schedule.append((window_start, window_end, c.ticker))
            cursor = date.fromordinal(window_end.toordinal() + 1)

        return schedule
