"""File-path overlap detection between a proposed work item and every
other currently-active (not completed) work item -- the "intelligent
overlap detection" the Active Work Registry MVP provides. Deliberately
simple and explainable: exact-path set intersection, not a dependency
graph or architecture-aware impact analysis (a real, much larger
follow-on effort -- see this package's own `__init__.py` docstring).

Warn-only, by design: this module never blocks or prevents anything, it
only classifies and explains. Every caller decides what to do with the
warning.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverlapWarning:
    work_item_id: str
    title: str
    owner_user_id: str | None
    overlapping_files: tuple[str, ...]
    risk: str  # one of collaboration.RISK_LEVELS
    reason: str


def _risk_level(overlap_count: int, proposed_file_count: int) -> str:
    """Small, explicit, and documented rather than a learned/black-box
    score -- exactly the "explain why" requirement this feature has.
    Thresholds are deliberately simple (raw count OR ratio, whichever is
    more alarming) since this MVP has no way to weigh *which* files
    matter more than others (that needs the architecture/dependency graph
    a later phase is meant to add)."""
    if overlap_count == 0:
        return "no_risk"
    ratio = overlap_count / max(proposed_file_count, 1)
    if overlap_count >= 5 or ratio >= 0.75:
        return "critical"
    if overlap_count >= 3 or ratio >= 0.5:
        return "high"
    if ratio >= 0.25:
        return "medium"
    return "low"


def _reason(overlapping_files: tuple[str, ...], risk: str) -> str:
    file_list = ", ".join(overlapping_files[:5])
    more = f" and {len(overlapping_files) - 5} more" if len(overlapping_files) > 5 else ""
    return f"{len(overlapping_files)} file(s) also touched by this task ({risk} risk): {file_list}{more}"


def detect_overlap(proposed_files: list[str], active_items: list[dict]) -> list[OverlapWarning]:
    """`active_items` is every other currently-active (status != completed)
    work item, each a dict with at least `id`, `title`, `owner_user_id`,
    and `estimated_files` (a list of path strings). Returns one
    `OverlapWarning` per active item that shares at least one file with
    `proposed_files`, sorted by risk (most severe first) -- items with
    zero overlap are omitted entirely, not returned with a "no_risk" entry
    (nothing to warn about)."""
    proposed = set(proposed_files)
    if not proposed:
        return []

    warnings: list[OverlapWarning] = []
    for item in active_items:
        other_files = set(item.get("estimated_files") or [])
        overlapping = tuple(sorted(proposed & other_files))
        if not overlapping:
            continue
        risk = _risk_level(len(overlapping), len(proposed))
        warnings.append(OverlapWarning(
            work_item_id=item["id"], title=item["title"], owner_user_id=item.get("owner_user_id"),
            overlapping_files=overlapping, risk=risk, reason=_reason(overlapping, risk),
        ))

    _risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "no_risk": 4}
    warnings.sort(key=lambda w: (_risk_order[w.risk], -len(w.overlapping_files)))
    return warnings
