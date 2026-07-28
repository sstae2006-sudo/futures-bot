"""TimescaleDB/Postgres-backed accounts store -- the team-deployment
counterpart to `accounts/store.py::AccountStore`, same public method
surface, selected instead of it by `get_account_store()` whenever
`FUTURES_BOT_DATABASE_URL` is set. Built on the shared pooled
`db.engine.get_engine()`, same design as `PgTradeStore`/`PgMarketDataStore`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from . import ROLES
from .store import AccountError, _generate_api_key
from ..db.engine import get_engine
from ..db.research_schema import metadata, organizations, users


def _isoformat_datetimes(d: dict) -> dict:
    """Same "always a string" contract `pg_trade_store.py::_stringify_datetimes`
    restores for `TradeStore` -- `AccountStore`'s SQLite methods never parse
    their TEXT timestamps back into `datetime`, so every existing/future
    caller already expects a plain string."""
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()}


def _row_dict(row) -> Optional[dict]:
    return _isoformat_datetimes(dict(row._mapping)) if row is not None else None


class PgAccountStore:
    """Pooled, Postgres-backed. Holds no connection of its own -- every
    method borrows one from the shared `Engine`'s pool for the duration of
    that call, same design as `PgTradeStore`."""

    def __init__(self, engine: Optional[Engine] = None) -> None:
        self._engine = engine or get_engine()

    def ensure_schema(self) -> None:
        with self._engine.begin() as conn:
            metadata.create_all(conn, checkfirst=True)

    def close(self) -> None:
        """No-op: see `PgMarketDataStore.close`'s identical docstring --
        the shared pooled Engine outlives any one caller."""

    # --- Organizations ---

    def create_organization(self, *, org_id: str, name: str) -> dict:
        try:
            with self._engine.begin() as conn:
                conn.execute(organizations.insert().values(id=org_id, name=name))
        except IntegrityError as exc:
            raise AccountError(f"An organization named {name!r} already exists.") from exc
        return self.fetch_organization(org_id)  # type: ignore[return-value]

    def fetch_organization(self, org_id: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(organizations).where(organizations.c.id == org_id)).first()
        return _row_dict(row)

    def fetch_organizations(self) -> list[dict]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(organizations).order_by(organizations.c.name)).fetchall()
        return [_row_dict(r) for r in rows]

    def update_organization(self, org_id: str, *, name: Optional[str] = None) -> dict:
        if self.fetch_organization(org_id) is None:
            raise AccountError(f"No such organization: {org_id!r}.")
        if name is not None:
            try:
                with self._engine.begin() as conn:
                    conn.execute(organizations.update().where(organizations.c.id == org_id).values(name=name))
            except IntegrityError as exc:
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
        try:
            with self._engine.begin() as conn:
                conn.execute(users.insert().values(
                    id=user_id, display_name=display_name, username=username,
                    email=email, avatar_url=avatar_url, org_id=org_id, role=role,
                    api_key=_generate_api_key(),
                ))
        except IntegrityError as exc:
            raise AccountError(f"A user with username {username!r} already exists.") from exc
        return self.fetch_user(user_id)  # type: ignore[return-value]

    def fetch_user(self, user_id: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(users).where(users.c.id == user_id)).first()
        return _row_dict(row)

    def fetch_user_by_username(self, username: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(users).where(users.c.username == username)).first()
        return _row_dict(row)

    def fetch_users(self, *, org_id: Optional[str] = None) -> list[dict]:
        stmt = select(users).order_by(users.c.display_name)
        if org_id is not None:
            stmt = stmt.where(users.c.org_id == org_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_dict(r) for r in rows]

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

        values = {
            k: v for k, v in (
                ("display_name", display_name), ("email", email),
                ("avatar_url", avatar_url), ("role", role), ("timezone", timezone),
                ("preferred_ai_model", preferred_ai_model), ("default_branch_prefix", default_branch_prefix),
            ) if v is not None
        }
        if notification_preferences is not None:
            values["notification_preferences"] = notification_preferences
        if values:
            with self._engine.begin() as conn:
                conn.execute(users.update().where(users.c.id == user_id).values(**values))
        return self.fetch_user(user_id)  # type: ignore[return-value]

    def regenerate_api_key(self, user_id: str) -> dict:
        if self.fetch_user(user_id) is None:
            raise AccountError(f"No such user: {user_id!r}.")
        with self._engine.begin() as conn:
            conn.execute(users.update().where(users.c.id == user_id).values(api_key=_generate_api_key()))
        return self.fetch_user(user_id)  # type: ignore[return-value]

    def touch_last_active(self, user_id: str) -> dict:
        if self.fetch_user(user_id) is None:
            raise AccountError(f"No such user: {user_id!r}.")
        with self._engine.begin() as conn:
            conn.execute(users.update().where(users.c.id == user_id).values(last_active_at=func.now()))
        return self.fetch_user(user_id)  # type: ignore[return-value]
