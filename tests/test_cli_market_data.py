"""Tests for the CLI's Phase 8A commands (`--sync-data`, `--backfill`,
`--verify-data`, `--repair-gaps`) -- argument validation, the
MASSIVE_API_KEY guard, and one real end-to-end pass per command against a
monkeypatched `requests.Session.get` (never a real network call).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from futures_bot.cli import main


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _agg_bar(window_start: datetime, close: float = 7500.0):
    ns = int(window_start.timestamp() * 1e9)
    return {"window_start": ns, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 500}


def _single(ticker, first_trade_date, last_trade_date, query_date):
    return {
        "active": True, "date": query_date, "name": ticker, "product_code": "MES",
        "ticker": ticker, "type": "single", "first_trade_date": first_trade_date, "last_trade_date": last_trade_date,
    }


@pytest.fixture
def patched_requests(monkeypatch):
    """Monkeypatches `requests.Session.get` at the class level -- both
    `sync._fetch_aggs` and `MassiveContractsClient` construct their own
    `requests.Session()` internally when the CLI doesn't inject one, so
    patching the class method is what actually intercepts every call."""
    state = {"contracts_by_date": {}, "aggs_by_ticker": {}}

    def fake_get(self, url, params=None, timeout=None):
        if "/contracts" in url:
            query_date = (params or {}).get("date")
            return FakeResponse(state["contracts_by_date"].get(query_date, {"results": [], "status": "OK"}))
        ticker = url.rsplit("/", 1)[-1]
        return FakeResponse({"results": state["aggs_by_ticker"].get(ticker, []), "status": "OK"})

    monkeypatch.setattr(requests.Session, "get", fake_get)
    return state


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))


class TestApiKeyGuard:
    def test_sync_data_without_api_key_fails_cleanly(self, monkeypatch, capsys):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        exit_code = main(["--sync-data", "--product", "MES"])
        assert exit_code == 1
        assert "MASSIVE_API_KEY" in capsys.readouterr().err

    def test_backfill_without_api_key_fails_cleanly(self, monkeypatch, capsys):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        exit_code = main(["--backfill", "--product", "MES", "--data-start", "2026-07-01", "--data-end", "2026-07-02"])
        assert exit_code == 1
        assert "MASSIVE_API_KEY" in capsys.readouterr().err

    def test_verify_data_does_not_need_an_api_key(self, monkeypatch, capsys):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        exit_code = main(["--verify-data", "--product", "MES"])
        assert exit_code == 0


class TestBackfillArgValidation:
    def test_backfill_without_dates_fails_cleanly(self, monkeypatch, capsys):
        monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
        exit_code = main(["--backfill", "--product", "MES"])
        assert exit_code == 1
        assert "--data-start" in capsys.readouterr().err


class TestEndToEnd:
    def test_sync_data_backfill_verify_repair_round_trip(self, monkeypatch, capsys, patched_requests):
        monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
        patched_requests["contracts_by_date"]["2026-07-20"] = {
            "status": "OK", "results": [_single("MESU6", "2025-06-20", "2026-09-18", "2026-07-20")],
        }
        patched_requests["aggs_by_ticker"]["MESU6"] = [
            _agg_bar(datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)),
            _agg_bar(datetime(2026, 7, 20, 14, 20, tzinfo=timezone.utc)),  # a 20-min-wide gap on purpose
        ]

        exit_code = main([
            "--backfill", "--product", "MES", "--resolution", "5min",
            "--data-start", "2026-07-20", "--data-end", "2026-07-20",
        ])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "Backfilled MES 5min" in out
        assert "MESU6" in out

        exit_code = main(["--verify-data", "--product", "MES"])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "2 bar(s) stored" in out
        assert "1 new gap(s) detected" in out

        # Fill in the gap so repair actually recovers something.
        patched_requests["aggs_by_ticker"]["MESU6"].append(
            _agg_bar(datetime(2026, 7, 20, 14, 10, tzinfo=timezone.utc))
        )
        exit_code = main(["--repair-gaps", "--product", "MES"])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "resolved 1" in out

    def test_sync_data_reports_a_roll(self, monkeypatch, capsys, patched_requests):
        monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
        today = datetime.now(timezone.utc).date().isoformat()
        patched_requests["contracts_by_date"][today] = {
            "status": "OK", "results": [_single("MESU6", "2025-06-20", "2026-09-18", today)],
        }

        exit_code = main(["--sync-data", "--product", "MES", "--resolution", "5min"])

        out = capsys.readouterr().out
        assert exit_code == 0
        assert "Contract rolled: (none) -> MESU6" in out
