"""Conflict Resolution Assistant (SIL Phase 6 Milestone 2) -- expands
Overlap Engine V2's warnings with a human-actionable "what should I do
about this" layer: which subsystems the conflict touches (via
`architecture_map.py`'s minimal, honestly-scoped lookup -- see that
module's docstring for why it isn't a real dependency graph) and a
templated suggested resolution/integration order.

Still an explainable heuristic layered on top of `overlap_v2.py`'s
existing scoring, not a new conflict-*detection* algorithm -- see that
module's own docstring for the actual detection logic this builds on.
"suggested resolution" here means "who should talk to whom, and in what
order should these two branches land," never an automated merge action;
this package never merges anything on its own (see ROADMAP.md's "Future"
section for why autonomous merging stays out of scope).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from . import STATUSES
from .architecture_map import affected_subsystems


class OverlapWarningLike(Protocol):
    """Structurally, not nominally, typed -- `build_conflict_resolutions`
    reads only these fields, so it accepts `overlap_v2.OverlapWarningV2`
    (the dataclass) *or* `api.schemas.OverlapWarningV2Out` (the pydantic
    response model) without either module importing the other.
    `api/collaboration_service.py::generate_integration_review` passes
    the latter -- reusing a `MergeReadinessOut` it already computed,
    rather than re-running `compute_overlap_v2` a second time on the
    same inputs just to get the dataclass version."""
    work_item_id: str
    title: str
    owner_user_id: Optional[str]
    risk: str
    confidence: int
    reason: str


@dataclass(frozen=True)
class ConflictResolution:
    warning: OverlapWarningLike
    architecture_components_affected: list[str] = field(default_factory=list)
    suggested_resolution: str = ""


def _pipeline_rank(status: str) -> int:
    return STATUSES.index(status) if status in STATUSES else 0


def _integration_order(this_item: dict, other_item: dict) -> tuple[dict, dict]:
    """Whichever item is further along the lifecycle pipeline is suggested
    to integrate first (it's closer to done, so the other branch should
    rebase onto it rather than the reverse); a tie breaks on `created_at`
    ascending (older work integrates first). A simple, explainable
    tiebreak, not a scheduling algorithm."""
    this_rank, other_rank = _pipeline_rank(this_item.get("status", "")), _pipeline_rank(other_item.get("status", ""))
    if this_rank != other_rank:
        return (this_item, other_item) if this_rank > other_rank else (other_item, this_item)
    this_created, other_created = this_item.get("created_at") or "", other_item.get("created_at") or ""
    return (this_item, other_item) if this_created <= other_created else (other_item, this_item)


def _suggested_resolution(warning: OverlapWarningLike, this_item: dict, other_item: dict) -> str:
    owner = warning.owner_user_id or "its (unclaimed) owner"
    if warning.risk in ("critical", "high"):
        first, second = _integration_order(this_item, other_item)
        return (
            f"High conflict risk -- recommend integrating '{first.get('title', first)}' before "
            f"'{second.get('title', second)}', and coordinating directly with {owner} to sequence "
            "the merges and avoid a conflicting rebase."
        )
    if warning.risk == "medium":
        return f"Moderate conflict risk -- recommend a quick sync with {owner} before either branch merges."
    return f"Low conflict risk -- no special sequencing needed, but worth a heads-up to {owner}."


def build_conflict_resolutions(
    this_item: dict, warnings: list[OverlapWarningLike], other_items_by_id: dict[str, dict],
) -> list[ConflictResolution]:
    """`other_items_by_id` maps `work_item_id` -> the full work item dict
    for each warning's target -- needed for `_integration_order`'s
    status/`created_at` comparison and for the other item's own
    `estimated_files`, neither of which `OverlapWarningV2` carries on its
    own. A warning whose target isn't in the map (already gone by the
    time this runs) still gets a resolution, just without the other
    side's subsystem contribution -- degrades gracefully rather than
    raising, matching every other overlap-adjacent computation here."""
    this_components = affected_subsystems(this_item.get("estimated_files") or [])
    results: list[ConflictResolution] = []
    for warning in warnings:
        other = other_items_by_id.get(warning.work_item_id)
        if other is not None:
            components = sorted(set(this_components) | set(affected_subsystems(other.get("estimated_files") or [])))
            suggestion = _suggested_resolution(warning, this_item, other)
        else:
            components = this_components
            suggestion = _suggested_resolution(warning, this_item, {"title": warning.title, "status": "", "created_at": ""})
        results.append(ConflictResolution(
            warning=warning, architecture_components_affected=components, suggested_resolution=suggestion,
        ))
    return results
