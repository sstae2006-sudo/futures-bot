"""Synthetic bar generation.

For exercising the backtest pipeline before real data is available. A random
walk has no structure for a strategy to find, so results from this data measure
plumbing, not edge — any profit it shows is luck and should be read as such.

Real MES history comes from the broker's data feed, or vendors like Databento,
Kibot, or FirstRate Data.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ..contracts import CME_TZ, ContractSpec, is_market_open


def generate_sample_csv(
    path: Path | str,
    contract: ContractSpec,
    start: datetime,
    days: int = 20,
    start_price: Decimal = Decimal("7500"),
    bar_minutes: int = 5,
    seed: int = 42,
    daily_drift: Decimal = Decimal("0"),
    volatility_points: Decimal = Decimal("1.5"),
) -> int:
    """Write a synthetic OHLCV CSV. Returns the number of bars written.

    Bars are only emitted while the market is actually open, so the session
    filter and force-flat logic get exercised the way they would be live.
    """
    rng = random.Random(seed)
    rows: list[dict] = []

    price = start_price
    moment = start.astimezone(CME_TZ)
    end = moment + timedelta(days=days)
    drift_per_bar = daily_drift / Decimal(max(1, (24 * 60) // bar_minutes))

    while moment < end:
        if not is_market_open(moment):
            moment += timedelta(minutes=bar_minutes)
            continue

        step = Decimal(str(rng.gauss(0, float(volatility_points)))) + drift_per_bar
        open_price = price
        close_price = contract.round_to_tick(open_price + step)

        wick = Decimal(str(abs(rng.gauss(0, float(volatility_points) * 0.6))))
        high = contract.round_to_tick(max(open_price, close_price) + wick)
        low = contract.round_to_tick(min(open_price, close_price) - wick)

        rows.append(
            {
                "timestamp": moment.strftime("%Y-%m-%d %H:%M:%S"),
                "open": f"{open_price:.2f}",
                "high": f"{high:.2f}",
                "low": f"{low:.2f}",
                "close": f"{close_price:.2f}",
                "volume": rng.randint(200, 5000),
            }
        )

        price = close_price
        moment += timedelta(minutes=bar_minutes)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)
