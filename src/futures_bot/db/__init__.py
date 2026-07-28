"""Pooled TimescaleDB/Postgres access -- team deployment only.

Nothing in this package is imported at module level anywhere outside
``db/`` itself. Every consumer (``api/market_data_store.py``,
``api/store.py``) imports from here lazily, inside the branch that
actually needs Postgres (``FUTURES_BOT_DATABASE_URL`` set) -- so a plain
single-developer SQLite setup, including the test suite, never needs the
``db`` extra installed at all. See ``engine.py``'s module docstring for
why SQLAlchemy Core, not the ORM.
"""

from __future__ import annotations
