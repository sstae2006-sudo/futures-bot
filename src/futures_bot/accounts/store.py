"""SQLite storage for lightweight user/organization accounts.

Lives in `research.db` (same file `research/trade_store.py::TradeStore`
already owns), reusing its exact `default_db_path()` resolution
(`FUTURES_BOT_RESEARCH_DB` env var) so both stores always point at the same
file without duplicating that logic. A genuinely separate connection/class
from `TradeStore` on purpose -- same "one store class per concern" split
`market_data/store.py` and `research/trade_store.py` already are from each
other, even though `market_data.db`/`research.db` are also two separate
files; here it's two concerns sharing one file, which SQLite supports
natively (multiple connections, `ensure_schema()` is idempotent either way).

Organizations own strategies/experiments/research/dashboards/files --
today that's a documented intent, not enforced foreign keys on those
existing tables (a real ownership retrofit onto `runs`/`experiments`/etc.
is a separate, larger schema change of its own, out of scope for this
MVP). What exists now: the `users`/`organizations` tables themselves, and
`org_id` on `users`, ready for other tables to reference later without
this module changing shape.

No authentication here: no password hash, no session token, no login
endpoint. `last_active_at` is updated only by an explicit `touch_last_active`
call (e.g. a "heartbeat" the frontend calls while a user is actively using
the dashboard) -- there's no request-level auth yet to infer "which user"
made an anonymous HTTP request, so this store never guesses.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from ..research.trade_store import default_db_path
from . import ROLES

#: Same convention `market_data/store.py::get_market_data_store()` and
#: `api/store.py::get_store()` already established.
_DATABASE_URL_ENV = "FUTURES_BOT_DATABASE_URL"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    username        TEXT NOT NULL UNIQUE,
    email           TEXT,
    avatar_url      TEXT,
    org_id          TEXT NOT NULL,
    role            TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_active_at  TEXT,
    FOREIGN KEY (org_id) REFERENCES organizations(id)
);
CREATE INDEX IF NOT EXISTS idx_users_org_id ON users(org_id);
"""


class AccountError(ValueError):
    """Raised for a bad role, a duplicate username/org name, or a
    reference to an organization/user that doesn't exist -- callers (the
    API layer) map this to a 400, the same convention `api.services.ApiError`
    already establishes for this codebase's other stores."""


class AccountStore:
    """Owns one SQLite connection. Not thread-safe -- open one per
    request/script, same convention `TradeStore` documents for itself."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- Organizations ---

    def create_organization(self, *, org_id: str, name: str) -> dict:
        try:
            self._conn.execute(
                "INSERT INTO organizations (id, name) VALUES (?, ?)", (org_id, name),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AccountError(f"An organization named {name!r} already exists.") from exc
        return self.fetch_organization(org_id)  # type: ignore[return-value]

    def fetch_organization(self, org_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
        self._conn.row_factory = None
        return dict(row) if row is not None else None

    def fetch_organizations(self) -> list[dict]:
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute("SELECT * FROM organizations ORDER BY name").fetchall()
        self._conn.row_factory = None
        return [dict(row) for row in rows]

    # --- Users ---

    def create_user(
        self, *, user_id: str, display_name: str, username: str, org_id: str, role: str,
        email: Optional[str] = None, avatar_url: Optional[str] = None,
    ) -> dict:
        if role not in ROLES:
            raise AccountError(f"Unknown role {role!r} -- must be one of {ROLES}.")
        if self.fetch_organization(org_id) is None:
            raise AccountError(f"No such organization: {org_id!r}.")
        try:
            self._conn.execute(
                """
                INSERT INTO users (id, display_name, username, email, avatar_url, org_id, role)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, display_name, username, email, avatar_url, org_id, role),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AccountError(f"A user with username {username!r} already exists.") from exc
        return self.fetch_user(user_id)  # type: ignore[return-value]

    def fetch_user(self, user_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        self._conn.row_factory = None
        return dict(row) if row is not None else None

    def fetch_user_by_username(self, username: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        self._conn.row_factory = None
        return dict(row) if row is not None else None

    def fetch_users(self, *, org_id: Optional[str] = None) -> list[dict]:
        self._conn.row_factory = sqlite3.Row
        if org_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM users WHERE org_id = ? ORDER BY display_name", (org_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM users ORDER BY display_name").fetchall()
        self._conn.row_factory = None
        return [dict(row) for row in rows]

    def update_user(
        self, user_id: str, *, display_name: Optional[str] = None, email: Optional[str] = None,
        avatar_url: Optional[str] = None, role: Optional[str] = None,
    ) -> dict:
        if self.fetch_user(user_id) is None:
            raise AccountError(f"No such user: {user_id!r}.")
        if role is not None and role not in ROLES:
            raise AccountError(f"Unknown role {role!r} -- must be one of {ROLES}.")

        fields, values = [], []
        for column, value in (
            ("display_name", display_name), ("email", email),
            ("avatar_url", avatar_url), ("role", role),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if fields:
            values.append(user_id)
            self._conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
            self._conn.commit()
        return self.fetch_user(user_id)  # type: ignore[return-value]

    def touch_last_active(self, user_id: str) -> Optional[dict]:
        """Called by an explicit "heartbeat" from an active client session
        -- never inferred from an anonymous request, since there's no
        request-level auth yet to know which user made it."""
        if self.fetch_user(user_id) is None:
            raise AccountError(f"No such user: {user_id!r}.")
        self._conn.execute(
            "UPDATE users SET last_active_at = datetime('now') WHERE id = ?", (user_id,),
        )
        self._conn.commit()
        return self.fetch_user(user_id)


def get_account_store():
    """A fresh store against the configured database -- `PgAccountStore`
    when `FUTURES_BOT_DATABASE_URL` is set (team-deployment mode), else
    `AccountStore(default_db_path())`. Same factory shape as
    `api/store.py::get_store()`/`market_data/store.py::get_market_data_store()`;
    lazy-imports `PgAccountStore` (and therefore SQLAlchemy) only inside
    the branch that needs it, for the same "SQLite-only setups never need
    the `db` extra" reason `db/engine.py` documents."""
    if os.environ.get(_DATABASE_URL_ENV):
        from .pg_store import PgAccountStore

        store = PgAccountStore()
        store.ensure_schema()
        return store
    return AccountStore()
