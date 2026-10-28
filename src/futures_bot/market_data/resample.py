"""Local OHLCV bar resampling.

Aggregates an already-stored, finer-resolution bar series up to a coarser
one, entirely from data already in `market_data.db` -- no vendor fetch.
Motivating gap: `MNQ`/`M2K` currently have a full ~2 years of `1min`
history but only ~13 months at coarser resolutions (see KNOWN_ISSUES.md
ISSUE-045); `MES` itself gains a full extra year of usable history the
same way. A transformation of already-stored data, parallel to `sync.py`'s
acquisition role -- this module never talks to a vendor.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Sequence

from ..feeds.massive import resolution_minutes
from ..models import Bar


def resample_bars(bars: Sequence[Bar], target_resolution: str) -> list[Bar]:
    """Aggregates ``bars`` (assumed sorted ascending, uniform source
    resolution -- every caller in this codebase already guarantees this)
    up to ``target_resolution`` via standard OHLCV rules: first open, max
    high, min low, last close, summed volume.

    Buckets are aligned to fixed-size UTC-epoch windows (e.g. every 5
    minutes on the clock past the hour), matching how this project's own
    vendor-native bars are already aligned -- confirmed against real
    `market_data.db` rows (a `10min` MES bar timestamped ``13:20:00Z``
    sits exactly on a 10-minute boundary).

    Only sub-daily target resolutions are supported: ``target_resolution``
    must parse (via `feeds.massive.resolution_minutes`) to under 1440
    minutes. Daily bars need session-calendar-aware bucketing (weekends,
    holidays) rather than fixed-size windows -- a different, more involved
    problem this function deliberately doesn't attempt.

    Raises ``ValueError`` if ``target_resolution`` is daily-or-coarser, or
    isn't strictly coarser than the source bars' own (modal) spacing --
    this function only aggregates up; it cannot invent detail finer than
    what's already in ``bars``.
    """
    target_minutes = resolution_minutes(target_resolution)
    if target_minutes >= 1440:
        raise ValueError(
            f"resample_bars only supports sub-daily target resolutions; "
            f"{target_resolution!r} resolves to {target_minutes} minute(s). "
            f"Daily bars need session-calendar-aware bucketing, not fixed-size windows."
        )
    if not bars:
        return []

    if len(bars) >= 2:
        # Modal spacing, not just the first gap -- the first two bars
        # could straddle a session boundary and misrepresent the series'
        # real (overwhelmingly dominant) native resolution.
        spacings = [
            int((b.timestamp - a.timestamp).total_seconds() // 60)
            for a, b in zip(bars, bars[1:])
        ]
        source_minutes = Counter(spacings).most_common(1)[0][0]
        if target_minutes <= source_minutes:
            raise ValueError(
                f"target_resolution {target_resolution!r} ({target_minutes} min) must be "
                f"coarser than the source bars' own resolution (~{source_minutes} min) -- "
                f"resample_bars only aggregates up, never down."
            )

    buckets: dict[int, list[Bar]] = {}
    order: list[int] = []
    for bar in bars:
        epoch_minutes = int(bar.timestamp.timestamp() // 60)
        bucket_index = epoch_minutes // target_minutes
        if bucket_index not in buckets:
            buckets[bucket_index] = []
            order.append(bucket_index)
        buckets[bucket_index].append(bar)

    resampled: list[Bar] = []
    for bucket_index in order:
        group = buckets[bucket_index]
        bucket_start = datetime.fromtimestamp(bucket_index * target_minutes * 60, tz=timezone.utc)
        resampled.append(Bar(
            timestamp=bucket_start,
            open=group[0].open,
            high=max(b.high for b in group),
            low=min(b.low for b in group),
            close=group[-1].close,
            volume=sum(b.volume for b in group),
        ))
    return resampled
