from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
import csv

from futures_bot.market_data.store import MarketDataStore
from futures_bot.models import Bar


INPUT_DIR = Path("turtle_converted")

DB_PATH = "market_data.db"


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

    for file in files:

        product = file.stem.upper()

        print(f"\nImporting {product}")

        bars = load_file(file)

        inserted = store.upsert_bars(
            product_code=product,
            contract="CONTINUOUS",
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
    print("================================")


if __name__ == "__main__":
    main()