from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
import csv
import re

from futures_bot.market_data.store import MarketDataStore
from futures_bot.models import Bar


INPUT_DIR = Path("turtle_converted")

DB_PATH = "market_data.db"

# root (1-3 letters) + 2-digit year + month-letter, e.g. "CL00F" -> CL/00/F.
TICKER_PATTERN = re.compile(r"^([A-Z]{1,3})(\d{2})([FGHJKMNQUVXZ])$")


def parse_ticker(stem: str) -> str:
    """
    Validates a turtle filename stem against the expected contract-
    symbol pattern (root + 2-digit year + month-letter, e.g. "CL00F"),
    raising ValueError if it doesn't match -- so a malformed/unexpected
    filename is rejected at import time instead of silently imported.

    IMPORTANT: product_code is set to this full ticker (not a generic
    root like "CL") deliberately, matching what this script already
    did before this fix. bars' uniqueness/coalescing index is
    (product_code, resolution, timestamp) -- see market_data/store.py
    -- which assumes exactly one contract represents a product on any
    given day (the live front-month-rolling design). This archive is
    the opposite: hundreds of individual contract-month files with
    heavily overlapping trading date ranges, each of which must be
    preserved independently. Setting product_code to a shared generic
    root collides every overlapping day across contracts and silently
    drops all but the first-written one -- confirmed by reimporting
    with that "fix" during the 2026-07-26 repair, which reduced
    342,494 rows to 34,331 before being caught and rolled back (see
    docs/DATABASE_CORRUPTION_REPORT.md). Using the full ticker as
    product_code is what actually keeps every contract's full history
    collision-free under this schema.

    The real, previously-diagnosed bug was that `contract` was
    hardcoded to the meaningless placeholder "CONTINUOUS" for every
    row instead of also being set to this same ticker -- that's what
    this fix corrects.
    """
    if not TICKER_PATTERN.match(stem):
        raise ValueError(
            f"{stem!r} doesn't match the expected contract-symbol "
            "pattern ROOT + YY + month-letter (e.g. 'CL00F')"
        )
    return stem


def load_file(path: Path):

    bars = []

    with open(path, newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:

            bars.append(
                Bar(
                    timestamp=datetime.fromisoformat(
                        row["timestamp"]
                    ).astimezone(timezone.utc),

                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),

                    volume=int(row["volume"]),
                )
            )

    return bars


def main():

    store = MarketDataStore(DB_PATH)

    files = list(INPUT_DIR.glob("*.csv"))

    if not files:
        print("No turtle CSV files found")
        return


    total = 0
    skipped = 0

    for file in files:

        stem = file.stem.upper()

        try:
            ticker = parse_ticker(stem)
        except ValueError as e:
            print(f"\nSkipping {file.name}: {e}")
            skipped += 1
            continue

        print(f"\nImporting {ticker}")

        bars = load_file(file)

        inserted = store.upsert_bars(
            product_code=ticker,
            contract=ticker,
            resolution="1day",
            source="turtletrader",
            bars=bars,
        )

        total += inserted

        print(
            f"Loaded {len(bars):,} bars "
            f"({inserted:,} new)"
        )


    print("\n================================")
    print(f"TOTAL NEW BARS: {total:,}")
    if skipped:
        print(f"SKIPPED (invalid ticker format): {skipped:,}")
    print("================================")


if __name__ == "__main__":
    main()