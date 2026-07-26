"""One-off pull of Massive's CME minute-aggregates flat files
(us_futures_cme/minute_aggs_v1, S3-compatible at files.massive.com) into
the existing market_data.db, as 1-minute resolution bars.

Scope: MES/MNQ/M2K single-contract tickers only (spreads like
"MESM6-MESU6" and MYM, which has no rows in this dataset, are excluded).
Date range: whatever the account's plan actually exposes (probed via
head_object) through today.

Front month only, per product per day: `MarketDataStore.upsert_bars`'s
uniqueness key is (product_code, resolution, timestamp) -- it does not
include the contract ticker (that's fine for the store's normal caller,
`sync.sync_incremental`, which only ever fetches the single currently-
active contract). But a flat file's one day has *every* contract that
traded that day -- front month and back month(s) simultaneously -- so
without picking one, whichever ticker's rows happened to appear first in
the file would silently win each timestamp and the rest would be dropped
by INSERT OR IGNORE, with no guarantee that "first in the file" means
"front month." Each day, per product, only the contract with the highest
total volume (the actual front month) is kept.

Credentials come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in the
environment -- never hardcode them here or in any tracked file.

Usage:
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... python pull_massive_flatfiles.py
"""

from __future__ import annotations

import csv
import gzip
import io
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import boto3

from futures_bot.market_data.store import MarketDataStore, default_db_path
from futures_bot.models import Bar

ENDPOINT_URL = "https://files.massive.com"
BUCKET = "flatfiles"
PREFIX = "us_futures_cme/minute_aggs_v1/"
RESOLUTION = "1min"
SOURCE = "massive_flatfiles"

TICKER_PATTERN = re.compile(r"^(MES|MNQ|M2K)[FGHJKMNQUVXZ]\d$")


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _key_for(d: date) -> str:
    return f"{PREFIX}{d.year:04d}/{d.month:02d}/{d.isoformat()}.csv.gz"


def _probe(s3, d: date) -> str:
    """'ok' (accessible), 'denied' (plan doesn't reach this date -- a real
    403), or 'missing' (weekend/holiday -- no file was ever going to exist
    here, a 404, which says nothing about plan access)."""
    try:
        s3.head_object(Bucket=BUCKET, Key=_key_for(d))
        return "ok"
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        return "denied" if code == "403" else "missing"


def find_earliest_accessible(s3, today: date) -> date:
    """Binary-search backward from today for the earliest date this plan
    can actually reach -- the plan-recency table on Massive's docs states a
    history depth (e.g. "2 years") but not the exact boundary date.

    Must treat "denied" (403, a real plan-access limit) and "missing" (404,
    an ordinary weekend/holiday with no file at all) as different signals.
    A naive "any exception means inaccessible" check breaks binary search's
    monotonicity assumption -- a run of weekend 404s sitting past the true
    403 boundary looks identical to still being inside it, and the search
    converges weeks later than the real cutoff. Treat "missing" as "keep
    searching earlier" (same as "ok", since it says nothing about access)
    and only "denied" as the actual boundary signal.
    """
    def reachable(d: date) -> bool:
        return _probe(s3, d) != "denied"

    lo, hi = today - timedelta(days=4000), today
    if _probe(s3, hi) == "missing":
        # today's file may not exist yet (market not closed) -- step back
        # a few days to find a real, checkable day near the edge.
        for back in range(1, 10):
            hi = today - timedelta(days=back)
            if _probe(s3, hi) != "missing":
                break
    while (hi - lo).days > 1:
        mid = lo + (hi - lo) // 2
        if reachable(mid):
            hi = mid
        else:
            lo = mid
    return hi


def main() -> int:
    s3 = boto3.client("s3", endpoint_url=ENDPOINT_URL)
    today = datetime.now(timezone.utc).date()

    # Optional explicit range (YYYY-MM-DD YYYY-MM-DD) -- for re-running
    # against just a gap instead of re-probing/re-downloading everything
    # `upsert_bars`'s INSERT OR IGNORE already makes idempotent.
    if len(sys.argv) == 3:
        earliest = date.fromisoformat(sys.argv[1])
        end = date.fromisoformat(sys.argv[2])
    else:
        earliest = find_earliest_accessible(s3, today)
        end = today
    print(f"Range: {earliest} .. {end}", file=sys.stderr)

    store = MarketDataStore(default_db_path())
    print(f"Writing to: {default_db_path()}", file=sys.stderr)

    total_files = 0
    total_rows_matched = 0
    total_rows_inserted = 0
    per_contract_rows: dict[tuple[str, str], int] = defaultdict(int)
    missing_days = []

    try:
        for d in _daterange(earliest, end):
            key = _key_for(d)
            try:
                obj = s3.get_object(Bucket=BUCKET, Key=key)
            except Exception:
                missing_days.append(d)  # weekend/holiday -- no file that day
                continue

            total_files += 1
            text = gzip.decompress(obj["Body"].read()).decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))

            by_contract: dict[str, list[Bar]] = defaultdict(list)
            volume_by_contract: dict[str, int] = defaultdict(int)
            for row in reader:
                ticker = row["ticker"]
                if not TICKER_PATTERN.match(ticker):
                    continue
                ts = datetime.fromtimestamp(int(row["window_start"]) / 1e9, tz=timezone.utc)
                volume = int(float(row["volume"]))
                bar = Bar(
                    timestamp=ts,
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=volume,
                )
                by_contract[ticker].append(bar)
                volume_by_contract[ticker] += volume
                total_rows_matched += 1

            # Pick one front-month contract per product for this day -- see
            # module docstring on why only one can be stored at all.
            front_month_by_product: dict[str, str] = {}
            for ticker, total_volume in volume_by_contract.items():
                product_code = ticker[:3]
                current = front_month_by_product.get(product_code)
                if current is None or total_volume > volume_by_contract[current]:
                    front_month_by_product[product_code] = ticker

            for product_code, ticker in front_month_by_product.items():
                bars = sorted(by_contract[ticker], key=lambda b: b.timestamp)
                inserted = store.upsert_bars(product_code, ticker, RESOLUTION, SOURCE, bars)
                total_rows_inserted += inserted
                per_contract_rows[(product_code, ticker)] += inserted

            if total_files % 25 == 0:
                print(f"  ...{total_files} files, {total_rows_inserted} rows inserted so far", file=sys.stderr)
    finally:
        store.close()

    print(file=sys.stderr)
    print(f"Done. {total_files} files processed, {len(missing_days)} non-trading days skipped.", file=sys.stderr)
    print(f"Rows matched: {total_rows_matched}, rows newly inserted: {total_rows_inserted}", file=sys.stderr)
    print(file=sys.stderr)
    print("Per contract:", file=sys.stderr)
    for (product_code, ticker), count in sorted(per_contract_rows.items()):
        print(f"  {product_code:5s} {ticker:8s} {count:>8d} rows", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
