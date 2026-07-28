"""Locks in the contract `get_market_data_store()` depends on:
`MarketDataStore` and `PgMarketDataStore` must expose the same public
method surface, so a caller written against one works unmodified against
the other. Doesn't need a real Postgres connection -- this only checks
signatures, not behavior (behavior parity is what
TEAM_DEPLOYMENT.md's real-server verification steps cover instead).
"""

from __future__ import annotations

import inspect

from futures_bot.market_data.pg_store import PgMarketDataStore
from futures_bot.market_data.store import MarketDataStore, get_market_data_store


def _public_methods(cls) -> set[str]:
    return {
        name for name, member in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


class TestMethodParity:
    def test_every_sqlite_method_exists_on_the_postgres_store(self):
        missing = _public_methods(MarketDataStore) - _public_methods(PgMarketDataStore)
        assert not missing, f"PgMarketDataStore is missing: {sorted(missing)}"

    def test_every_postgres_method_exists_on_the_sqlite_store(self):
        """The reverse direction too -- a method only one class has is
        just as much a broken seam as one that's missing."""
        extra = _public_methods(PgMarketDataStore) - _public_methods(MarketDataStore)
        assert not extra, f"PgMarketDataStore has extra methods not on MarketDataStore: {sorted(extra)}"

    def test_both_stores_expose_location_and_size_bytes(self):
        assert hasattr(MarketDataStore, "location")
        assert hasattr(PgMarketDataStore, "location")
        assert hasattr(MarketDataStore, "size_bytes")
        assert hasattr(PgMarketDataStore, "size_bytes")


class TestFactoryFallback:
    def test_unset_database_url_returns_sqlite_store_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FUTURES_BOT_DATABASE_URL", raising=False)
        monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
        store = get_market_data_store()
        try:
            assert isinstance(store, MarketDataStore)
        finally:
            store.close()

    def test_set_database_url_returns_postgres_store(self, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/x")
        from futures_bot.db import engine as db_engine

        db_engine.dispose_engine()
        try:
            store = get_market_data_store()
            assert isinstance(store, PgMarketDataStore)
        finally:
            db_engine.dispose_engine()
