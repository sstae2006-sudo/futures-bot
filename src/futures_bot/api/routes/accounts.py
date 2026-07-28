"""Lightweight user/organization accounts (Team Collaboration MVP). See
`accounts/store.py`'s module docstring: a data model plus basic CRUD,
deliberately not an authentication system -- no login, no session, no
password. `POST /api/users/{user_id}/heartbeat` is the one write a client
is expected to call on its own initiative (while a user is actively using
the dashboard), since there's no request-level auth yet to infer "which
user" made an ordinary request.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from .. import accounts_service as services
from ..schemas import (
    OrganizationCreateRequest, OrganizationOut, OrganizationUpdateRequest, UserCreateRequest, UserMeOut,
    UserOut, UserUpdateRequest,
)

router = APIRouter(tags=["accounts"])


@router.post("/api/organizations", response_model=OrganizationOut)
def create_organization(req: OrganizationCreateRequest) -> OrganizationOut:
    return services.create_organization(req.name)


@router.get("/api/organizations", response_model=list[OrganizationOut])
def list_organizations() -> list[OrganizationOut]:
    return services.list_organizations()


@router.get("/api/organizations/{org_id}", response_model=OrganizationOut)
def get_organization(org_id: str) -> OrganizationOut:
    return services.get_organization(org_id)


@router.patch("/api/organizations/{org_id}", response_model=OrganizationOut)
def update_organization(org_id: str, req: OrganizationUpdateRequest) -> OrganizationOut:
    """The UI gates this behind `accounts.permissions.can(role,
    "manage_organization")` (advisory only, not enforced here -- see that
    module's docstring)."""
    return services.update_organization(org_id, name=req.name)


@router.post("/api/users", response_model=UserMeOut)
def create_user(req: UserCreateRequest) -> UserMeOut:
    """Returns `UserMeOut` (includes the auto-generated `api_key`) --
    registration is the one moment a freshly-created user needs to see
    their own key so it can be saved."""
    return services.create_user(
        display_name=req.display_name, username=req.username, org_id=req.org_id,
        role=req.role, email=req.email, avatar_url=req.avatar_url,
    )


@router.get("/api/users", response_model=list[UserOut])
def list_users(org_id: Optional[str] = None) -> list[UserOut]:
    return services.list_users(org_id=org_id)


@router.get("/api/users/{user_id}", response_model=UserOut)
def get_user(user_id: str) -> UserOut:
    return services.get_user(user_id)


@router.get("/api/users/{user_id}/me", response_model=UserMeOut)
def get_user_me(user_id: str) -> UserMeOut:
    """Includes `api_key` -- the profile page's own fetch, never used for
    a roster listing (`GET /api/users` stays `UserOut`, no key)."""
    return services.get_user_me(user_id)


@router.patch("/api/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, req: UserUpdateRequest) -> UserOut:
    return services.update_user(
        user_id, display_name=req.display_name, email=req.email,
        avatar_url=req.avatar_url, role=req.role, timezone=req.timezone,
        preferred_ai_model=req.preferred_ai_model, default_branch_prefix=req.default_branch_prefix,
        notification_preferences=req.notification_preferences,
    )


@router.post("/api/users/{user_id}/regenerate-api-key", response_model=UserMeOut)
def regenerate_api_key(user_id: str) -> UserMeOut:
    return services.regenerate_api_key(user_id)


@router.post("/api/users/{user_id}/heartbeat", response_model=UserOut)
def heartbeat(user_id: str) -> UserOut:
    return services.touch_last_active(user_id)
