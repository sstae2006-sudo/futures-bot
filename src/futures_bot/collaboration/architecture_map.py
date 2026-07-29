"""A minimal, hand-curated path-prefix -> subsystem lookup.

SIL Phase 6 Milestone 2's spec asks several pieces (Architecture Impact
Reports, the Conflict Resolution Assistant, subsystem-level coordinator
reasoning) to "leverage the existing Architecture Model." No such system
exists: SIL Phase 5 ("Trading Intelligence Layer" -- a real dependency/
import graph, semantic architecture discovery) was proposed but never
built, and `collaboration/overlap_v2.py`'s own docstring already lists
"real architecture/dependency-graph analysis" as explicitly out of scope
for this package. Pretending otherwise here would silently overstate what
this platform actually knows about itself.

What follows is the smallest honest thing that unblocks Milestone 2: a
static table mapping a changed file's path prefix to a human-readable
subsystem label, matching the vocabulary already used for a worker's own
free-text `subsystem` field (`collaboration/__init__.py::WORKER_TYPES`'s
docstring lists the same kind of names). It is deliberately NOT a parser,
an import graph, or anything that reads file contents -- just a lookup
over paths already available on any work item's `estimated_files` (the
same self-reported list `overlap_v2.py` already treats as a best-effort
proxy, see `api/collaboration_service.py::_readiness_note`). Extend the
table as new top-level areas of the codebase are added; if this ever
needs to reason about imports/call graphs instead of path prefixes, that
is the real Architecture Model (TIL) milestone, not a growth of this
module.
"""

from __future__ import annotations

#: Ordered most-specific-prefix-first so a file under e.g.
#: `frontend/src/components/mission-control/` resolves to that label
#: rather than the broader `frontend/src/` one below it.
_SUBSYSTEM_PREFIXES: tuple[tuple[str, str], ...] = (
    ("src/futures_bot/engine.py", "Trading Engine"),
    ("src/futures_bot/strategy/", "Strategy Engine"),
    ("src/futures_bot/context/", "Market Context Engine"),
    ("src/futures_bot/risk/", "Risk Management"),
    ("src/futures_bot/brokers/", "Order Execution / Brokers"),
    ("src/futures_bot/backtest/", "Backtesting"),
    ("src/futures_bot/research_server/", "Autonomous Research Server"),
    ("src/futures_bot/research/", "Research & ML"),
    ("src/futures_bot/market_data/", "Market Data"),
    ("src/futures_bot/collaboration/", "Collaboration / SIL"),
    ("src/futures_bot/accounts/", "Accounts"),
    ("src/futures_bot/db/", "Database Layer"),
    ("src/futures_bot/api/", "API Layer"),
    ("frontend/src/components/mission-control/", "Frontend -- Mission Control"),
    ("frontend/src/pages/", "Frontend -- Pages"),
    ("frontend/src/", "Frontend"),
    ("alembic/", "Database Migrations"),
    ("tests/", "Test Suite"),
    ("docs/", "Documentation"),
)


def affected_subsystems(paths: list[str]) -> list[str]:
    """Every subsystem label touched by `paths`, in first-seen order, no
    duplicates. A path matching nothing in the table (a repo-root config
    file, an unrecognized new top-level directory) simply contributes
    nothing -- degrades gracefully, same convention `overlap_v2.analyze_files`
    already establishes for unreadable/unrecognized files."""
    found: list[str] = []
    for path in paths:
        for prefix, label in _SUBSYSTEM_PREFIXES:
            if path.startswith(prefix):
                if label not in found:
                    found.append(label)
                break
    return found
