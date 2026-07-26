"""Phase 8A: a local historical market-data pipeline.

Everything in this package exists to answer one question cheaply and
correctly: "what bars do we already have, and what's still missing?" --
so that backtests, the optimizer, research tools, and paper trading can
all read from one growing local database instead of each juggling their
own CSV exports.

See `store.py` for the schema, `contracts_client.py` for auto-detecting
the active futures contract, `sync.py` for the incremental/resumable
download engine, and `scheduler.py` for the background thread that keeps
the database current while the market is open.
"""

from __future__ import annotations
