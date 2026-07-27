# Market Context Engine — Performance Benchmark

Performed as Phase 8, Part 5 ("Performance Benchmark") of the Market
Context Engine's completion phase (2026-07-27). Run via
`tools/benchmark_context_engine.py` — rerun it directly for fresh
numbers on any machine; figures below are one representative run on
the development machine (Windows, Python 3.12.10), 20 repeats per
history size, using a deterministic zigzag bar generator so every
dimension (trend/volatility/regime/structure/liquidity/risk) does real,
non-degenerate work rather than hitting a cheap all-`UNKNOWN` path.

## Results

| Bars   | Avg (ms) | Worst (ms) | Best (ms) | Peak memory (KB) |
|-------:|---------:|-----------:|----------:|-----------------:|
| 50     | 0.578    | 1.106      | 0.491     | 34.9              |
| 200    | 2.153    | 2.659      | 1.918     | 154.5             |
| 1,000  | 10.209   | 10.757     | 9.762     | 785.7             |
| 5,000  | 28.302   | 31.182     | 26.418    | 3,918.8           |
| 20,000 | 123.680  | 127.787    | 119.996   | 15,724.5          |
| 50,000 | 621.142  | 655.679    | 582.808   | 39,407.0          |

## Scaling behavior

Roughly linear-to-mildly-superlinear in the number of bars passed to
`build_context`. This is expected, not a bug:

- **`session`/`risk`/`scoring` are O(1)** (or effectively so) regardless
  of history size — `session` reads only a timestamp;
  `risk`/`scoring` read only already-computed enum values/fields, no
  bars at all.
- **`volatility`/`regime`/`trend`/`timeframe` are O(n) per call** —
  each re-derives ATR (`atr_series`), ADX, and
  `research.regime.classify_trend` from the *entire* `bars` list given,
  every single call. This cannot be safely optimized by truncating the
  input: Wilder's smoothing (used by both ATR and ADX) is a recursive
  EMA-style computation whose result depends on where the truncated
  slice's seed average starts — narrowing the input changes the
  numeric result, however slightly. Given this project's "exact, never
  approximate" discipline (verified throughout by exact-equality tests,
  not tolerance-based ones), this cost is accepted and documented rather
  than silently traded away for speed.
- **`structure` is O(n) by design**, not by oversight: confirmed swing
  highs/lows — and therefore the nearest support/resistance — can
  legitimately come from anywhere in history (verified manually during
  Phase 6: an old resistance level from well before "now" was correctly
  selected as the nearest one above current price). Bounding this
  dimension's lookback would be a real behavior change, not a
  transparent optimization.
- **`liquidity` was optimized this phase** (Part 5): the previous
  implementation converted every bar's volume to `Decimal` before
  slicing, even though only the trailing `lookback` bars are ever used.
  Fixed to slice `bars[-lookback:]` *before* converting — verified
  output-identical by the existing `tests/test_context_liquidity.py`
  suite staying green with zero test changes required (see
  `context/liquidity.py`'s `analyze_liquidity` docstring for the exact
  reasoning this is safe here but not for ATR/ADX).

## Impact on large backtests

A replay loop calling `build_context` once per bar with an
ever-growing `bars` list is **O(n) per call / O(n²) over a full run**,
purely from the O(n) dimensions above — at 50,000 bars, a single call
already costs ~621ms average, so replaying a run that long one bar at a
time (each call seeing more history than the last) would be
substantially slower than the flat numbers above suggest in isolation.

**Recommendation for a future integration phase** (not implemented
here — Phase 8 explicitly does not integrate into `TradingEngine`):
have the *caller* pass a bounded trailing window (e.g. the last 200–500
bars) rather than the full history accumulated so far. No classifier's
default parameters need more than ~60 bars (ADX's default period × 2 =
28 is the largest single requirement). This is a caller-side decision
requiring **no changes to `context/` itself** — every function already
only reads whatever `bars` it's given, so passing fewer of them is
already fully supported today; nothing here needs to change to make
that possible.

## Memory

Peak memory scales roughly linearly with bar count (~0.7–0.8 KB per
bar at scale), dominated by the O(n) dimensions' own intermediate lists
(`atr_series`'s full smoothed series, `structure`'s per-bar
high/low arrays) rather than anything held onto after `build_context`
returns — `MarketContext` itself is small (a handful of enums, floats,
and short nested dataclasses), so a caller holding onto many returned
contexts (e.g. for `context/analytics.py`, Part 6) does not carry this
same per-call intermediate cost forward.
