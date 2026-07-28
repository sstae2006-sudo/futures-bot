"""Locks in the contract `api/store.py::get_store()` depends on:
`TradeStore` and `PgTradeStore` must expose the same public method
surface, so a caller written against one works unmodified against the
other. Mirrors `test_market_data_store_parity.py` exactly. Doesn't need a
real Postgres connection -- this only checks signatures, not behavior
(behavior parity is what `tests/test_pg_trade_store_live.py` covers).
"""

from __future__ import annotations

import inspect

from futures_bot.research.pg_trade_store import PgTradeStore
from futures_bot.research.trade_store import TradeStore


def _public_methods(cls) -> set[str]:
    return {
        name for name, member in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


class TestMethodParity:
    def test_every_sqlite_method_exists_on_the_postgres_store(self):
        missing = _public_methods(TradeStore) - _public_methods(PgTradeStore)
        assert not missing, f"PgTradeStore is missing: {sorted(missing)}"

    def test_every_postgres_method_exists_on_the_sqlite_store(self):
        extra = _public_methods(PgTradeStore) - _public_methods(TradeStore)
        assert not extra, f"PgTradeStore has extra methods not on TradeStore: {sorted(extra)}"

    def test_both_stores_expose_location_and_size_bytes(self):
        assert hasattr(TradeStore, "location")
        assert hasattr(PgTradeStore, "location")
        assert hasattr(TradeStore, "size_bytes")
        assert hasattr(PgTradeStore, "size_bytes")
