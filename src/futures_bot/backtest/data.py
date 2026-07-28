"""Historical bar loading.

Vendors disagree about almost everything: column names, timestamp formats, and
whether timestamps are UTC, exchange local, or naive. Rather than assume, this
module maps flexibly and then validates hard, because bad input data produces a
backtest that is confidently wrong rather than obviously broken.

Timezone handling is the part worth caring about. A naive timestamp is
interpreted as Central Time — the exchange's own zone — because that is what
session boundaries are defined against. Getting this wrong shifts every bar
relative to the session and silently changes which trades the hours filter
allows.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Optional, Sequence
from zoneinfo import ZoneInfo

from ..contracts import CME_TZ
from ..market_data.store import get_market_data_store
from ..models import Bar

# Accepted spellings for each field, lowercased.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "time", "datetime", "date", "bar_time", "t"),
    "open": ("open", "o", "open_price"),
    "high": ("high", "h", "high_price"),
    "low": ("low", "l", "low_price"),
    "close": ("close", "c", "close_price", "last"),
    "volume": ("volume", "v", "vol", "tick_volume"),
}

TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
)


class DataError(ValueError):
    """Raised when input data cannot be trusted."""


@dataclass
class DataQualityReport:
    """What the loader noticed. Surfaced rather than silently tolerated."""

    bars_loaded: int
    first_timestamp: Optional[datetime]
    last_timestamp: Optional[datetime]
    duplicate_timestamps: int
    out_of_order_rows: int
    zero_volume_bars: int
    suspicious_gaps: list[tuple[datetime, datetime]]

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.bars_loaded == 0:
            out.append("No bars loaded.")
        if self.duplicate_timestamps:
            out.append(
                f"{self.duplicate_timestamps} duplicate timestamp(s) dropped. "
                f"Duplicates usually mean the export overlapped two requests."
            )
        if self.out_of_order_rows:
            out.append(
                f"{self.out_of_order_rows} row(s) were out of order and have been sorted. "
                f"Check the export if you did not expect that."
            )
        if self.zero_volume_bars:
            pct = self.zero_volume_bars / max(self.bars_loaded, 1) * 100
            out.append(
                f"{self.zero_volume_bars} bar(s) have zero volume ({pct:.1f}%). "
                f"Illiquid or synthetic bars produce fills that would not happen live."
            )
        if self.suspicious_gaps:
            # ASCII "->", not a Unicode arrow -- Windows' default console
            # codepage (cp1252) can't encode "→" and crashes on
            # print(), which is exactly where this string is headed.
            shown = ", ".join(f"{a:%Y-%m-%d %H:%M}->{b:%H:%M}" for a, b in self.suspicious_gaps[:3])
            more = "" if len(self.suspicious_gaps) <= 3 else f" (+{len(self.suspicious_gaps) - 3} more)"
            out.append(
                f"{len(self.suspicious_gaps)} unexpected intraday gap(s): {shown}{more}. "
                f"Missing data during a session can hide the bar that would have stopped you out."
            )
        return out


def _resolve_columns(header: Sequence[str]) -> dict[str, int]:
    """Map required fields to column indices, tolerating vendor naming."""
    lowered = [h.strip().lower().replace(" ", "_") for h in header]
    mapping: dict[str, int] = {}

    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                mapping[field] = lowered.index(alias)
                break

    missing = [f for f in ("timestamp", "open", "high", "low", "close") if f not in mapping]
    if missing:
        raise DataError(
            f"CSV is missing required column(s): {', '.join(missing)}. "
            f"Found columns: {', '.join(header)}"
        )
    return mapping


def _parse_timestamp(raw: str, tz: ZoneInfo) -> datetime:
    text = raw.strip()

    # ISO with an explicit offset — trust it.
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            return parsed
        return parsed.replace(tzinfo=tz)
    except ValueError:
        pass

    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue

    raise DataError(
        f"Could not parse timestamp {raw!r}. Supported formats include "
        f"'2026-07-21 09:30:00' and ISO 8601 with offset."
    )


def _parse_decimal(raw: str, field: str, row_num: int) -> Decimal:
    try:
        return Decimal(raw.strip())
    except (InvalidOperation, AttributeError):
        raise DataError(f"Row {row_num}: {field} value {raw!r} is not a number.") from None


def load_bars(
    path: Path | str,
    tz: ZoneInfo = CME_TZ,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    max_gap_minutes: int = 120,
) -> tuple[list[Bar], DataQualityReport]:
    """Load OHLCV bars from a CSV.

    Args:
        path: CSV file with a header row.
        tz: Zone applied to naive timestamps. Defaults to exchange local time.
        start: Drop bars before this moment.
        end: Drop bars after this moment.
        max_gap_minutes: Intraday gaps wider than this are reported. Overnight
            and weekend gaps are expected and not flagged.

    Returns:
        The bars (sorted, deduplicated) and a quality report.
    """
    p = Path(path)
    if not p.exists():
        raise DataError(f"No such file: {p}")

    rows: list[Bar] = []
    seen: set[datetime] = set()
    duplicates = 0
    out_of_order = 0
    zero_volume = 0
    previous_ts: Optional[datetime] = None

    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            raise DataError(f"{p} is empty.") from None

        cols = _resolve_columns(header)
        vol_idx = cols.get("volume")

        for row_num, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue

            try:
                ts = _parse_timestamp(row[cols["timestamp"]], tz)
                o = _parse_decimal(row[cols["open"]], "open", row_num)
                h = _parse_decimal(row[cols["high"]], "high", row_num)
                lo = _parse_decimal(row[cols["low"]], "low", row_num)
                c = _parse_decimal(row[cols["close"]], "close", row_num)
                v = int(Decimal(row[vol_idx].strip() or "0")) if vol_idx is not None else 0
            except IndexError:
                raise DataError(f"Row {row_num} has fewer columns than the header.") from None

            if ts in seen:
                duplicates += 1
                continue
            seen.add(ts)

            if previous_ts is not None and ts < previous_ts:
                out_of_order += 1
            previous_ts = ts

            if start is not None and ts < start:
                continue
            if end is not None and ts > end:
                continue

            if v == 0:
                zero_volume += 1

            # Bar's own validation catches high < low.
            try:
                rows.append(Bar(timestamp=ts, open=o, high=h, low=lo, close=c, volume=v))
            except ValueError as exc:
                raise DataError(f"Row {row_num}: {exc}") from None

    rows.sort(key=lambda b: b.timestamp)

    gaps: list[tuple[datetime, datetime]] = []
    for prev, curr in zip(rows, rows[1:]):
        delta_minutes = (curr.timestamp - prev.timestamp).total_seconds() / 60
        # Only flag gaps inside a single session; overnight and weekend breaks
        # are normal and would otherwise drown the signal in noise.
        same_day = prev.timestamp.astimezone(tz).date() == curr.timestamp.astimezone(tz).date()
        if same_day and delta_minutes > max_gap_minutes:
            gaps.append((prev.timestamp, curr.timestamp))

    report = DataQualityReport(
        bars_loaded=len(rows),
        first_timestamp=rows[0].timestamp if rows else None,
        last_timestamp=rows[-1].timestamp if rows else None,
        duplicate_timestamps=duplicates,
        out_of_order_rows=out_of_order,
        zero_volume_bars=zero_volume,
        suspicious_gaps=gaps,
    )
    return rows, report


def iter_bars(bars: Sequence[Bar]) -> Iterator[Bar]:
    """Yield bars in order. A seam for streaming very large files later."""
    yield from bars


def load_bars_from_db(
    product_code: str, resolution: str,
    start: Optional[datetime] = None, end: Optional[datetime] = None,
) -> tuple[list[Bar], DataQualityReport]:
    """Phase 8A: the market-data database's equivalent of `load_bars` --
    same return shape, so every existing caller (CLI `--backtest`/
    `--optimize`/`--compare`, `api/services.py`'s `_load_request_bars`) can
    treat local DB history as a drop-in alternative to a CSV file without
    caring which one it got. Duplicate timestamps and out-of-order rows are
    structurally impossible here (`MarketDataStore.bars`'s unique index and
    `ORDER BY timestamp` respectively -- see that module's docstring), so
    this report only ever surfaces bar count and zero-volume bars; gap
    detection already has its own richer, persistent home in
    `market_data.sync.verify`/the `gaps` table rather than being
    recomputed here on every load.
    """
    store = get_market_data_store()
    try:
        bars = store.fetch_bars(product_code, resolution, start=start, end=end)
    finally:
        store.close()

    zero_volume = sum(1 for b in bars if b.volume == 0)
    report = DataQualityReport(
        bars_loaded=len(bars),
        first_timestamp=bars[0].timestamp if bars else None,
        last_timestamp=bars[-1].timestamp if bars else None,
        duplicate_timestamps=0,
        out_of_order_rows=0,
        zero_volume_bars=zero_volume,
        suspicious_gaps=[],
    )
    return bars, report


def is_db_dataset(dataset: str) -> bool:
    return dataset.startswith("db:")


def parse_db_dataset(dataset: str) -> tuple[str, str]:
    """``"db:MES:5min"`` -> ``("MES", "5min")``. Shared by the CLI and the
    API layer so the naming convention lives in exactly one place."""
    body = dataset[len("db:"):]
    parts = body.split(":")
    if len(parts) != 2 or not all(parts):
        raise ValueError(
            f"Invalid market-data dataset name {dataset!r}: expected 'db:PRODUCT:RESOLUTION', e.g. 'db:MES:5min'."
        )
    return parts[0], parts[1]
