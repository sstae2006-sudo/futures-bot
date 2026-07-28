"""API-facing logic for the lightweight user/organization accounts (Team
Collaboration MVP) -- kept as its own module rather than folded into the
already-large `services.py`, per this feature's own "keep it modular"
brief. Same role `services.py` plays for every other resource: translates
between `accounts.store.AccountStore`/`AccountError` (backend-agnostic,
raises its own errors) and the API's Pydantic schemas/`ApiError` (which the
app's exception handler maps to a 400 -- see `api/app.py`).
"""

from __future__ import annotations

import uuid
from typing import Optional

from ..accounts.store import AccountError, get_account_store
from .schemas import OrganizationOut, UserOut
from .services import ApiError


def create_organization(name: str) -> OrganizationOut:
    store = get_account_store()
    try:
        org_id = uuid.uuid4().hex[:12]
        org = store.create_organization(org_id=org_id, name=name)
    except AccountError as exc:
        raise ApiError(str(exc)) from exc
    return OrganizationOut(**org)


def list_organizations() -> list[OrganizationOut]:
    store = get_account_store()
    return [OrganizationOut(**o) for o in store.fetch_organizations()]


def get_organization(org_id: str) -> OrganizationOut:
    store = get_account_store()
    org = store.fetch_organization(org_id)
    if org is None:
        raise ApiError(f"No such organization: {org_id!r}")
    return OrganizationOut(**org)


def create_user(
    *, display_name: str, username: str, org_id: str, role: str,
    email: Optional[str] = None, avatar_url: Optional[str] = None,
) -> UserOut:
    store = get_account_store()
    try:
        user_id = uuid.uuid4().hex[:12]
        user = store.create_user(
            user_id=user_id, display_name=display_name, username=username,
            org_id=org_id, role=role, email=email, avatar_url=avatar_url,
        )
    except AccountError as exc:
        raise ApiError(str(exc)) from exc
    return UserOut(**user)


def list_users(org_id: Optional[str] = None) -> list[UserOut]:
    store = get_account_store()
    return [UserOut(**u) for u in store.fetch_users(org_id=org_id)]


def get_user(user_id: str) -> UserOut:
    store = get_account_store()
    user = store.fetch_user(user_id)
    if user is None:
        raise ApiError(f"No such user: {user_id!r}")
    return UserOut(**user)


def update_user(
    user_id: str, *, display_name: Optional[str] = None, email: Optional[str] = None,
    avatar_url: Optional[str] = None, role: Optional[str] = None,
) -> UserOut:
    store = get_account_store()
    try:
        user = store.update_user(
            user_id, display_name=display_name, email=email, avatar_url=avatar_url, role=role,
        )
    except AccountError as exc:
        raise ApiError(str(exc)) from exc
    return UserOut(**user)


def touch_last_active(user_id: str) -> UserOut:
    store = get_account_store()
    try:
        user = store.touch_last_active(user_id)
    except AccountError as exc:
        raise ApiError(str(exc)) from exc
    return UserOut(**user)
