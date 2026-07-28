"""Authorization -- kept as its own module, deliberately separate from
`store.py` (identity/profile data) and from authentication (which doesn't
exist yet at all, see this package's own `__init__.py` docstring). This
is the seam a real auth layer plugs into later: once a request can be
resolved to "the current user" (a session, a validated `api_key`, real
login), the *only* thing that needs to change is what populates `role`
before calling `can()` -- not this module, not the capability table below.

Nothing in this codebase enforces these checks server-side yet (there is
no request-level identity to check them against -- see `store.py`'s
docstring for why). Today `can()` is consulted by the frontend only, to
decide what to show/hide (e.g. hiding "rename organization" from a
`viewer`) -- a UX nicety, not a security boundary. Treat every check here
as advisory until real authentication exists; do not treat "the button
was hidden" as equivalent to "the action was authorized."
"""

from __future__ import annotations

from . import ROLES

#: A capability a role either has or doesn't -- deliberately flat and
#: small (not a hierarchy/inheritance system) so it stays trivial to
#: audit. Add entries here rather than special-casing a role name at a
#: call site.
CAPABILITIES = (
    "manage_organization",  # rename the org, view/change org-wide settings
    "manage_members",       # change another member's role, remove a member
    "manage_work",          # create/claim/release/complete/advance work items
    "view",                 # read-only access to everything visible to the org
)

#: Every role at least has "view" -- a `viewer` truly can only view.
_ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "owner": frozenset(CAPABILITIES),
    "admin": frozenset(CAPABILITIES),
    "member": frozenset({"manage_work", "view"}),
    "viewer": frozenset({"view"}),
}


def can(role: str, capability: str) -> bool:
    """`False` for an unrecognized role or capability rather than raising
    -- a caller deciding what to render shouldn't need a try/except for a
    typo'd capability name; it just means "no.\""""
    if role not in ROLES or capability not in CAPABILITIES:
        return False
    return capability in _ROLE_CAPABILITIES[role]


def capabilities_for(role: str) -> frozenset[str]:
    """Every capability a role has -- what the frontend's session context
    fetches once per login rather than calling `can()` repeatedly for
    every button on the page."""
    return _ROLE_CAPABILITIES.get(role, frozenset())
