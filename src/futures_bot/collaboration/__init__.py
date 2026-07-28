"""Team Collaboration MVP -- an Active Work Registry so multiple humans
and AI agents working on this codebase at once can see who's doing what,
claim/release/complete/reassign work, and get warned (never blocked)
about file-level overlap with other active work before they start.

Deliberately scoped small: file-path overlap only (no dependency graph,
no architecture mapping, no semantic merge analysis -- those are much
larger, separate efforts to build on top of this foundation later, not
missing pieces of this MVP). See `overlap.py` for the detection logic and
`store.py` for the schema/CRUD.
"""

from __future__ import annotations

#: Fixed vocabulary, validated at the store layer -- same convention
#: `accounts.ROLES` already established.
STATUSES = ("open", "claimed", "completed")
PRIORITIES = ("low", "medium", "high", "critical")
RISK_LEVELS = ("no_risk", "low", "medium", "high", "critical")
