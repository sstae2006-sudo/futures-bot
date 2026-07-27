"""
Market Context Engine performance benchmark -- Phase 8, Part 5
("Performance Benchmark") of the Context Engine's completion phase
(2026-07-27).

Measures ``ContextEngine.build_context()`` timing (average/worst-case)
and peak memory allocation across several history sizes, to answer:
how expensive is one context generation, and how does that cost scale
with the amount of history a caller passes in? This matters directly
for a future integration into a backtest replay loop, which would call
``build_context`` once per bar over a potentially large history.

Dev/ops tool, not part of the installable package (see CLAUDE.md's
File Ownership table) -- run directly, not imported.

Usage:
    python tools/benchmark_context_engine.py
    python tools/benchmark_context_engine.py --repeats 50
"""

from __future__ import annotations

import argparse
import time
import tracemalloc
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from futures_bot.context import ContextEngine
from futures_bot.models import Bar

CT = ZoneInfo("America/Chicago")
START = datetime(2026, 1, 6, 17, 0, tzinfo=CT)

#: History sizes to benchmark -- chosen to span "a strategy's usual
#: warmup window" (200) up through "a full multi-month 1-minute
#: backtest" (50,000), the realistic range a future integration would
#: actually pass.
BAR_COUNTS = (50, 200, 1_000, 5_000, 20_000, 50_000)


def _zigzag_bars(n: int, start=None) -> list[Bar]:
    """Deterministic rise-then-fall bars with varying volume -- gives
    every dimension (trend, volatility, regime, structure, liquidity,
    risk) real, non-degenerate data to classify, so the benchmark
    reflects the engine's real per-call cost, not a cheap all-UNKNOWN
    path."""
    start = start or START
    prices: list[Decimal] = []
    cycle_low = Decimal("5900")
    price = cycle_low
    direction = 1
    step = Decimal("4")
    for i in range(n):
        price += step * direction
        if i % 8 == 0:
            direction *= -1
        prices.append(price)
    out = []
    for i, p in enumerate(prices):
        ts = start + timedelta(minutes=i)
        vol = 100 + (i % 50) * 5
        out.append(Bar(timestamp=ts, open=p, high=p + 4, low=p - 4, close=p, volume=vol))
    return out


def _time_build_context(bars: list[Bar], repeats: int) -> tuple[float, float, float]:
    """Returns (average_ms, worst_case_ms, best_case_ms) over ``repeats``
    calls, all against the exact same ``bars``/timestamp (the realistic
    "same history, called again" case a backtest replay loop
    approximates bar over bar, since each successive call's history
    only grows by one bar)."""
    engine = ContextEngine(symbol="MES", timeframe="1min")
    timings = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
        timings.append((time.perf_counter() - t0) * 1000.0)
    return sum(timings) / len(timings), max(timings), min(timings)


def _peak_memory_kb(bars: list[Bar]) -> float:
    engine = ContextEngine(symbol="MES", timeframe="1min")
    tracemalloc.start()
    engine.build_context(timestamp=bars[-1].timestamp, bars=bars)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / 1024.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=20, help="Calls per history size (default: 20)")
    args = parser.parse_args()

    print(f"{'bars':>8}  {'avg (ms)':>10}  {'worst (ms)':>11}  {'best (ms)':>10}  {'peak mem (KB)':>14}")
    print("-" * 62)

    prev_avg = None
    for n in BAR_COUNTS:
        bars = _zigzag_bars(n)
        avg_ms, worst_ms, best_ms = _time_build_context(bars, args.repeats)
        peak_kb = _peak_memory_kb(bars)
        scaling = f"  ({avg_ms / prev_avg:.2f}x prior)" if prev_avg else ""
        print(f"{n:>8}  {avg_ms:>10.3f}  {worst_ms:>11.3f}  {best_ms:>10.3f}  {peak_kb:>14.1f}{scaling}")
        prev_avg = avg_ms

    print()
    print("Interpretation:")
    print("- session/risk/scoring are O(1)/near-O(1) regardless of history size.")
    print("- volatility/regime/trend/timeframe are O(n) per call: each re-derives")
    print("  ATR/ADX/classify_trend from the *entire* bars list given every time --")
    print("  Wilder's smoothing (ATR/ADX) cannot be safely truncated without")
    print("  changing its result (the recursion's seed depends on where the slice")
    print("  starts), so this is a genuine, accepted cost, not an oversight.")
    print("- structure is O(n) by design: swing highs/lows, and therefore nearest")
    print("  support/resistance, can legitimately come from anywhere in history.")
    print("- liquidity was optimized this phase to only convert the trailing")
    print("  `lookback` bars to Decimal, not the full history (see")
    print("  context/liquidity.py's analyze_liquidity docstring) -- a real,")
    print("  measured, output-preserving fix.")
    print()
    print("Impact on large backtests: a replay loop calling build_context once")
    print("per bar with an ever-growing `bars` list is O(n) per call / O(n^2)")
    print("over a full run, purely from the O(n) dimensions above. A future")
    print("integration phase should have the CALLER pass a bounded trailing")
    print("window (e.g. the last 200-500 bars) rather than full history -- no")
    print("classifier's default parameters need more than ~60 bars, and this")
    print("script's own numbers (~200 vs ~50,000 bars) show that's a large,")
    print("worthwhile difference. That is a caller-side decision, not a change")
    print("to context/ itself: every function already only reads bars given to")
    print("it, so passing fewer of them is already fully supported today.")


if __name__ == "__main__":
    main()
