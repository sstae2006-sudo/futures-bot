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

`api_key` (added for the registration/profile feature, 2026-07-28) is
generated automatically at `create_user` time and is a placeholder for
future auth, not an enforced credential today -- nothing in this codebase
checks it against anything. It exists now so (a) the registration UI has
something concrete to show/copy at signup and a profile page has
something to display/rotate, and (b) a later real-auth pass can start
validating it as a bearer token without a schema change. See
`accounts/permissions.py`'s module docstring for the matching note about
authorization.
"""

from __future__ import annotations

import json
import os
import secrets
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


def _generate_api_key() -> str:
    """URL-safe, 32 bytes of entropy -- same generator Python's own
    `secrets` module recommends for tokens. Prefixed so it's visually
    identifiable in logs/UI as a futures-bot key, not a stray secret."""
    return f"fbot_{secrets.token_urlsafe(32)}"


def _row_with_notification_preferences(row: dict) -> dict:
    row = dict(row)
    raw = row.get("notification_preferences")
    if raw:
        try:
            row["notification_preferences"] = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            row["notification_preferences"] = {}
    else:
        row["notification_preferences"] = {}
    return row


class AccountError(ValueError):
    """Raised for a bad role, a duplicate username/org name, or a
    reference to an organization/user that doesn't exist -- callers (the
    API layer) map this to a 400, the same convention `api.services.ApiError`
    already establishes for this codebase's other stores."""


class AccountStore:
    """Owns one SQLite connection. Not thread-safe -- open one per
    request/script, same convention `TradeStore` documents for itself."""

    #: Profile fields added after `users` already shipped -- `CREATE TABLE
    #: IF NOT EXISTS` can't retrofit columns onto an existing table, so
    #: these are added via `ALTER TABLE` in `ensure_schema` if missing,
    #: same "ALTER TABLE if missing" pattern `research/trade_store.py`
    #: established. Every existing row backfills to NULL/empty -- correct
    #: for every user created before these fields existed.
    _PROFILE_COLUMNS = (
        ("api_key", "TEXT"),
        ("timezone", "TEXT"),
        ("preferred_ai_model", "TEXT"),
        ("default_branch_prefix", "TEXT"),
        ("notification_preferences", "TEXT"),
    )

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
        existing_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(users)")}
        for name, sqltype in self._PROFILE_COLUMNS:
            if name not in existing_columns:
                self._conn.execute(f"ALTER TABLE users ADD COLUMN {name} {sqltype}")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_api_key ON users(api_key)")
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

    def update_organization(self, org_id: str, *, name: Optional[str] = None) -> dict:
        if self.fetch_organization(org_id) is None:
            raise AccountError(f"No such organization: {org_id!r}.")
        if name is not None:
            try:
                self._conn.execute("UPDATE organizations SET name = ? WHERE id = ?", (name, org_id))
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise AccountError(f"An organization named {name!r} already exists.") from exc
        return self.fetch_organization(org_id)  # type: ignore[return-value]

    # --- Users ---

    def create_user(
        self, *, user_id: str, display_name: str, username: str, org_id: str, role: str,
        email: Optional[str] = None, avatar_url: Optional[str] = None,
    ) -> dict:
        if role not in ROLES:
            raise AccountError(f"Unknown role {role!r} -- must be one of {ROLES}.")
        if self.fetch_organization(org_id) is None:
            raise AccountError(f"No such organization: {org_id!r}.")
        api_key = _generate_api_key()
        try:
            self._conn.execute(
                """
                INSERT INTO users (id, display_name, username, email, avatar_url, org_id, role, api_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, display_name, username, email, avatar_url, org_id, role, api_key),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AccountError(f"A user with username {username!r} already exists.") from exc
        return self.fetch_user(user_id)  # type: ignore[return-value]

    def fetch_user(self, user_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        self._conn.row_factory = None
        return _row_with_notification_preferences(dict(row)) if row is not None else None

    def fetch_user_by_username(self, username: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        self._conn.row_factory = None
        return _row_with_notification_preferences(dict(row)) if row is not None else None

    def fetch_users(self, *, org_id: Optional[str] = None) -> list[dict]:
        self._conn.row_factory = sqlite3.Row
        if org_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM users WHERE org_id = ? ORDER BY display_name", (org_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM users ORDER BY display_name").fetchall()
        self._conn.row_factory = None
        return [_row_with_notification_preferences(dict(row)) for row in rows]

    def update_user(
        self, user_id: str, *, display_name: Optional[str] = None, email: Optional[str] = None,
        avatar_url: Optional[str] = None, role: Optional[str] = None, timezone: Optional[str] = None,
        preferred_ai_model: Optional[str] = None, default_branch_prefix: Optional[str] = None,
        notification_preferences: Optional[dict] = None,
    ) -> dict:
        if self.fetch_user(user_id) is None:
            raise AccountError(f"No such user: {user_id!r}.")
        if role is not None and role not in ROLES:
            raise AccountError(f"Unknown role {role!r} -- must be one of {ROLES}.")

        fields, values = [], []
        for column, value in (
            ("display_name", display_name), ("email", email),
            ("avatar_url", avatar_url), ("role", role), ("timezone", timezone),
            ("preferred_ai_model", preferred_ai_model), ("default_branch_prefix", default_branch_prefix),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if notification_preferences is not None:
            fields.append("notification_preferences = ?")
            values.append(json.dumps(notification_preferences))
        if fields:
            values.append(user_id)
            self._conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
            self._conn.commit()
        return self.fetch_user(user_id)  # type: ignore[return-value]

    def regenerate_api_key(self, user_id: str) -> dict:
        if self.fetch_user(user_id) is None:
            raise AccountError(f"No such user: {user_id!r}.")
        new_key = _generate_api_key()
        self._conn.execute("UPDATE users SET api_key = ? WHERE id = ?", (new_key, user_id))
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
