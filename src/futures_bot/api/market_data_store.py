"""Thin re-export of `market_data.store.get_market_data_store` -- lets
`api/`-layer code spell this import the way the rest of that package's
imports read (`from .market_data_store import get_market_data_store`),
mirroring `api/store.py`'s identical thin-wrapper role over
`research.trade_store`'s own `TradeStore`/`get_store` machinery.

The real implementation lives in `market_data/store.py` (not here)
because lower-level consumers below `api/` in the dependency direction
(`market_data/scheduler.py`, `research_server/paper_trader.py`) need it
too, and must never import from `api/` -- see that function's own
docstring for the full reasoning.
"""

from __future__ import annotations

from ..market_data.store import get_market_data_store

__all__ = ["get_market_data_store"]
