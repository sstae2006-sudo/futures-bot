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
    OrganizationCreateRequest, OrganizationOut, UserCreateRequest, UserOut, UserUpdateRequest,
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


@router.post("/api/users", response_model=UserOut)
def create_user(req: UserCreateRequest) -> UserOut:
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


@router.patch("/api/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, req: UserUpdateRequest) -> UserOut:
    return services.update_user(
        user_id, display_name=req.display_name, email=req.email,
        avatar_url=req.avatar_url, role=req.role,
    )


@router.post("/api/users/{user_id}/heartbeat", response_model=UserOut)
def heartbeat(user_id: str) -> UserOut:
    return services.touch_last_active(user_id)
