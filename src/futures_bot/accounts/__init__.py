"""Lightweight user/organization accounts (Phase: Team Collaboration MVP).

Deliberately not an authentication system: no passwords, no sessions, no
tokens. This package is a data model plus basic CRUD -- who exists, what
organization they belong to, and what role they hold -- so a real auth
layer (password/OAuth/session middleware resolving "the current user" from
a request) can be added later without redesigning the underlying schema or
API shape. See `ROLES` for the fixed role vocabulary and `store.py`'s
module docstring for the full design rationale.
"""

from __future__ import annotations

#: Fixed, small vocabulary -- matches every other "closed enum stored as a
#: TEXT/String column" convention already used in this codebase (e.g.
#: `research/trade_store.py`'s `kind`/`status` columns). Validated at the
#: store layer (AccountStore/PgAccountStore both reject anything else) and
#: at the API layer (Pydantic Literal), not just documented.
ROLES = ("owner", "admin", "member", "viewer")
