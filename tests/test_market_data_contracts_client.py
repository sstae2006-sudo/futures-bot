"""Tests for `market_data.contracts_client.MassiveContractsClient` against a
mocked HTTP session -- the same `FakeSession`/`FakeResponse` pattern
`tests/test_massive_feed.py` already established for the aggs endpoint,
applied here to the Contracts API. Fixture payloads mirror the real,
live-verified response shape (see the module docstring): one row per
(ticker, query-date), `type` distinguishing outright "single" contracts
from "combo" calendar spreads.
"""

from __future__ import annotations

from datetime import date

import pytest

from futures_bot.market_data.contracts_client import ContractsApiError, MassiveContractsClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, pages_by_date: dict[str, dict] | None = None, sequential_pages: list[dict] | None = None):
        """Either respond based on the requested `date` query param
        (`pages_by_date`), or hand back one page per call in order
        (`sequential_pages`, for pagination tests)."""
        self.pages_by_date = pages_by_date or {}
        self.sequential_pages = sequential_pages
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        if self.sequential_pages is not None:
            index = min(len(self.calls) - 1, len(self.sequential_pages) - 1)
            return FakeResponse(self.sequential_pages[index])
        query_date = (params or {}).get("date")
        return FakeResponse(self.pages_by_date.get(query_date, {"results": [], "status": "OK"}))


def _single(ticker, first_trade_date, last_trade_date, query_date):
    return {
        "active": True, "date": query_date, "name": f"{ticker} Future", "product_code": "MES",
        "ticker": ticker, "type": "single",
        "first_trade_date": first_trade_date, "last_trade_date": last_trade_date,
    }


def _combo(ticker, query_date):
    return {"active": True, "date": query_date, "name": ticker, "product_code": "MES", "ticker": ticker, "type": "combo"}


class TestListSingleContracts:
    def test_filters_out_combo_spreads(self):
        session = FakeSession(pages_by_date={
            "2026-07-22": {
                "status": "OK",
                "results": [
                    _single("MESU6", "2025-06-20", "2026-09-18", "2026-07-22"),
                    _combo("MESU6-MESZ6", "2026-07-22"),
                ],
            }
        })
        client = MassiveContractsClient("key", session=session)

        contracts = client.list_single_contracts("MES", date(2026, 7, 22))

        assert [c.ticker for c in contracts] == ["MESU6"]

    def test_follows_next_url_pagination(self):
        session = FakeSession(sequential_pages=[
            {"status": "OK", "results": [_single("MESU6", "2025-06-20", "2026-09-18", "2026-07-22")],
             "next_url": "https://api.massive.com/futures/v1/contracts?cursor=abc"},
            {"status": "OK", "results": [_single("MESZ6", "2025-09-19", "2026-12-18", "2026-07-22")]},
        ])
        client = MassiveContractsClient("key", session=session)

        contracts = client.list_single_contracts("MES", date(2026, 7, 22))

        assert {c.ticker for c in contracts} == {"MESU6", "MESZ6"}
        assert len(session.calls) == 2

    def test_deduplicates_a_ticker_seen_on_multiple_pages(self):
        session = FakeSession(sequential_pages=[
            {"status": "OK", "results": [_single("MESU6", "2025-06-20", "2026-09-18", "2026-07-22")],
             "next_url": "https://api.massive.com/futures/v1/contracts?cursor=abc"},
            {"status": "OK", "results": [_single("MESU6", "2025-06-20", "2026-09-18", "2026-07-22")]},
        ])
        client = MassiveContractsClient("key", session=session)

        contracts = client.list_single_contracts("MES", date(2026, 7, 22))

        assert len(contracts) == 1

    def test_request_failure_raises_contracts_api_error(self):
        import requests

        class ExplodingSession:
            def get(self, *a, **k):
                raise requests.RequestException("network down")

        client = MassiveContractsClient("key", session=ExplodingSession())
        with pytest.raises(ContractsApiError, match="network down"):
            client.list_single_contracts("MES", date(2026, 7, 22))


class TestActiveContract:
    def test_picks_the_contract_with_the_soonest_last_trade_date(self):
        """Mirrors the real live response: MESH7 (Mar 2027), MESM7 (Jun
        2027), and MESU6 (Sep 2026) all listed simultaneously on
        2026-07-22 -- MESU6 is front-month because its own last_trade_date
        is soonest."""
        session = FakeSession(pages_by_date={
            "2026-07-22": {
                "status": "OK",
                "results": [
                    _single("MESH7", "2025-12-19", "2027-03-19", "2026-07-22"),
                    _single("MESM7", "2026-03-20", "2027-06-17", "2026-07-22"),
                    _single("MESU6", "2025-06-20", "2026-09-18", "2026-07-22"),
                ],
            }
        })
        client = MassiveContractsClient("key", session=session)

        active = client.active_contract("MES", as_of=date(2026, 7, 22))

        assert active.ticker == "MESU6"

    def test_ignores_a_contract_whose_window_does_not_cover_the_date(self):
        session = FakeSession(pages_by_date={
            "2026-07-22": {
                "status": "OK",
                "results": [
                    # A single contract that hasn't started trading yet.
                    _single("MESZ6", "2026-08-01", "2026-12-18", "2026-07-22"),
                    _single("MESU6", "2025-06-20", "2026-09-18", "2026-07-22"),
                ],
            }
        })
        client = MassiveContractsClient("key", session=session)

        active = client.active_contract("MES", as_of=date(2026, 7, 22))

        assert active.ticker == "MESU6"

    def test_raises_when_nothing_covers_the_date(self):
        session = FakeSession(pages_by_date={"2026-07-22": {"status": "OK", "results": []}})
        client = MassiveContractsClient("key", session=session)

        with pytest.raises(ContractsApiError, match="No active contract"):
            client.active_contract("MES", as_of=date(2026, 7, 22))


class TestFrontMonthSchedule:
    def test_builds_a_contiguous_schedule_across_a_rollover(self):
        """The same M6->U6 rollover this session hand-stitched earlier --
        the schedule should hand back two contiguous windows with no gap
        and no overlap."""
        session = FakeSession(pages_by_date={
            "2026-06-01": {
                "status": "OK",
                "results": [
                    _single("MESM6", "2025-12-19", "2026-06-19", "2026-06-01"),
                    _single("MESU6", "2026-03-20", "2026-09-18", "2026-06-01"),
                ],
            },
        })
        client = MassiveContractsClient("key", session=session)

        schedule = client.front_month_schedule("MES", date(2026, 6, 1), date(2026, 7, 1))

        assert schedule == [
            (date(2026, 6, 1), date(2026, 6, 19), "MESM6"),
            (date(2026, 6, 20), date(2026, 7, 1), "MESU6"),
        ]

    def test_raises_on_start_after_end(self):
        client = MassiveContractsClient("key", session=FakeSession())
        with pytest.raises(ValueError, match="must not be after"):
            client.front_month_schedule("MES", date(2026, 7, 1), date(2026, 6, 1))
