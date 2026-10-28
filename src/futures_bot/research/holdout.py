"""Hold-out / data-snooping ledger read side.

`TradeStore.insert_dataset_usage` (professional-research-pipeline plan
Phase 3) records every bar range a backtest/optimizer run has ever
touched. This module answers the question that ledger exists for: given a
candidate range, has any part of it already been used, and if so, what's
left that's genuinely untouched? Phase 4's wider strategy search must
consult this before treating any result as a real, uncontaminated final
test -- a range that's already been trained/optimized on isn't a hold-out
anymore just because a new run calls it one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .trade_store import TradeStore


@dataclass(frozen=True)
class HoldoutStatus:
    """Whether ``[start, end]`` overlaps anything already recorded in
    ``dataset_usage`` for this ``(product_code, resolution)`` pair.

    ``is_protected`` is ``True`` the moment *any* overlap exists --
    partial contamination is still contamination; this does not attempt
    to compute exactly which sub-ranges survive when only part of the
    candidate range overlaps (``untouched_ranges`` is reserved for that
    finer-grained answer, not implemented in Phase 3's first cut -- every
    caller today only needs the yes/no answer).
    """

    is_protected: bool
    overlapping_ranges: list[tuple[datetime, datetime, str]]

    def __bool__(self) -> bool:
        """``if protected_range(...):`` reads as "yes, safe to use" --
        matching this module's own docstring framing (-> True once never
        used) while `overlapping_ranges` stays available for a caller that
        wants to say *why* a range was rejected."""
        return self.is_protected


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start <= b_end and b_start <= a_end


def protected_range(
    store: "TradeStore", product_code: str, resolution: str, start: datetime, end: datetime,
) -> HoldoutStatus:
    """True the moment ``[start, end]`` has never appeared in
    ``dataset_usage`` for this ``(product_code, resolution)`` pair --
    the piece Phase 4 must consult before treating anything as a genuine
    final test, not an already-seen slice being re-labeled as one.

    Malformed rows (a ``start_ts``/``end_ts`` that doesn't parse as an
    ISO timestamp -- shouldn't happen given `insert_dataset_usage` always
    writes `datetime.isoformat()`, but this reads whatever the store
    actually persisted, not what it's supposed to) are skipped rather
    than raising: a corrupted row must never silently make an actually-
    contaminated range look clean by crashing before it's checked.
    """
    rows = store.fetch_dataset_usage(product_code, resolution)
    overlapping: list[tuple[datetime, datetime, str]] = []
    for row in rows:
        try:
            row_start = datetime.fromisoformat(row["start_ts"])
            row_end = datetime.fromisoformat(row["end_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if _overlaps(start, end, row_start, row_end):
            overlapping.append((row_start, row_end, row["purpose"]))

    return HoldoutStatus(is_protected=not overlapping, overlapping_ranges=overlapping)
