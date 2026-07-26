"""
Fetch MES 5-min bars from the Massive API and write a clean CSV
formatted for use with futures_bot's backtest runner.

Usage:
    python fetch_mes_data.py --symbol MESH6 --start 2026-01-01 --end 2026-01-31 --out mes_jan2026.csv

Output columns:
    timestamp (CME-local, e.g. 2026-01-02 07:00:00-06:00)
    open, high, low, close, volume
"""

import argparse
import csv
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

CME_TZ = ZoneInfo("America/Chicago")
BASE_URL = "https://api.massive.com/futures/v1/aggs/{symbol}"


def fetch_all_bars(symbol: str, start: str, end: str, api_key: str, resolution: str = "5min"):
    """Page through the API until all bars in [start, end] are collected."""
    bars = []
    params = {
        "resolution": resolution,
        "window_start.gte": start,
        "window_start.lte": end,
        "limit": 50000,
        "sort": "window_start.asc",
        "apiKey": api_key,
    }
    url = BASE_URL.format(symbol=symbol)

    while True:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            break

        bars.extend(results)

        # Handle pagination: prefer an explicit next_url if the API returns one,
        # otherwise advance window_start.gte past the last bar received.
        next_url = data.get("next_url")
        if next_url:
            url = next_url
            params = {"apiKey": api_key}  # next_url usually carries its own query params
        elif len(results) < params.get("limit", 50000):
            break
        else:
            last_ns = results[-1]["window_start"]
            last_dt = datetime.fromtimestamp(last_ns / 1e9, tz=timezone.utc)
            new_start = last_dt.isoformat().replace("+00:00", "Z")
            if params.get("window_start.gte") == new_start:
                break
            params["window_start.gte"] = new_start

    return bars


def to_csv(bars, out_path: str):
    rows = []
    for b in bars:
        ts_utc = datetime.fromtimestamp(b["window_start"] / 1e9, tz=timezone.utc)
        ts_cme = ts_utc.astimezone(CME_TZ)
        rows.append({
            "timestamp": ts_cme.isoformat(),
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": b["close"],
            "volume": b["volume"],
        })

    rows.sort(key=lambda r: r["timestamp"])

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} bars to {out_path}")
    if rows:
        print(f"Range: {rows[0]['timestamp']} -> {rows[-1]['timestamp']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="e.g. MESH6")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--resolution", default="5min")
    parser.add_argument("--out", required=True, help="output CSV path")
    parser.add_argument("--api-key", required=True, help="Massive API key")
    args = parser.parse_args()

    bars = fetch_all_bars(args.symbol, args.start, args.end, args.api_key, args.resolution)
    to_csv(bars, args.out)


if __name__ == "__main__":
    main()
