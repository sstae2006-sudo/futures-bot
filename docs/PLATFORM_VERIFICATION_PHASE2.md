# Platform Verification Phase 2 — Context Engine Deduplication & Stale-State Fix

Performed 2026-07-27, immediately following Platform Verification Phase 1
(`docs/PLATFORM_VERIFICATION_PHASE1.md`). Resolves the two findings that
audit surfaced but deliberately did not fix (Phase 1 was measurement-only).
No new functionality; no behavioral, classification, scoring, or API
changes — the explicit goal was "a cleaner and faster implementation with
zero behavioral changes."

## Architecture summary

**Finding #1 (duplicate ADX/volatility computation) — fixed by threading a
single computed value through the call graph, not by changing any
algorithm.** `context/regime.py`'s `classify_regime` and
`context/trend.py`'s `analyze_trend` gained optional
`precomputed_volatility`/`precomputed_adx` parameters, each defaulting to
a private sentinel (`_UNSET`, one per module) rather than `None` — this
distinguishes "caller didn't supply anything, compute it yourself"
(the sentinel, and the only case every *existing* caller of either
function ever hits) from "caller already computed it, and it genuinely
came back `None`" (an explicit `None`, which is used as-is, never
retried). Every call site anywhere in the codebase that doesn't pass
these new parameters — every direct test, every future standalone
caller — is byte-for-byte on the exact same code path as before this
phase.

`context/context_engine.py`'s `ContextEngine.build_context` is the one
caller that *does* supply them: it now calls `_classify_volatility` and a
new `_compute_adx` helper exactly once per bar, then passes both results
into `_classify_regime` (→ `classify_regime(..., precomputed_volatility=,
precomputed_adx=)`) and `_classify_trend` (→
`analyze_trend(..., precomputed_adx=)`). This is safe specifically
because `ContextEngine` never overrides either function's `adx_period`
default (`DEFAULT_ADX_PERIOD = 14`, still owned by `regime.py`, imported
by both `trend.py` and now `context_engine.py`) — a shared computation
using that one default is always exactly what `classify_regime` and
`analyze_trend` would have computed independently, since `adx()` and
`analyze_volatility()` are pure functions of `(bars, period)` with no
other input. Verified directly, not just argued:
`tests/test_platform_verification_phase2.py::TestPrecomputedValuesAreInterchangeableWithFreshComputation`
compares a precomputed-value call against an independent recompute and
asserts full dataclass equality (including the `None`-is-used-as-is edge
case, where volatility is `UNKNOWN` and `classify_regime` returns early
before ever touching ADX).

No computation is ever done *more* than before: `trend.analyze_trend` has
no dependency on volatility state and always tried to compute ADX itself
whenever `len(bars) >= 2`, so `_compute_adx` running unconditionally in
`build_context` matches that pre-existing cost even in the edge case
where `regime.classify_regime` would have skipped ADX entirely (an early
return on `VolatilityState.UNKNOWN`, before that phase's precomputed value
is even read).

**Finding #2 (stale `Strategy.context` on a reused instance) — fixed
defensively, not by documentation.** `TradingEngine.__init__` now sets
`self.strategy.context = None` immediately at construction (closing the
gap between "engine built" and "first bar processed"), and `on_bar`'s
step 0 now sets `self.strategy.context` unconditionally on *every* bar —
to the real `MarketContext` when `ContextMode.ENABLED` and
`strategy.uses_context` are both true, to `None` otherwise — instead of
only ever setting it in the `ENABLED` branch and leaving every other
mode's prior value untouched. No caller has to remember to clear
anything; a `Strategy` instance reused across two engines with different
modes now always reflects only the *current* engine's own state.
Verified by `tests/test_platform_verification_phase1.py::TestStaleStrategyContextAcrossReusedInstancesIsResolved`
(inverted from Phase 1's version, which asserted the bug existed — now
asserts it's gone) and
`tests/test_platform_verification_phase2.py::TestStrategyContextResetOnConstruction`
(the narrower construction-time-only case).

## Files changed

| File | Change |
|---|---|
| `src/futures_bot/context/regime.py` | Added `_UNSET` sentinel; `classify_regime` gained `precomputed_volatility`/`precomputed_adx` optional parameters. |
| `src/futures_bot/context/trend.py` | Added its own `_UNSET` sentinel; `analyze_trend` gained `precomputed_adx`. |
| `src/futures_bot/context/context_engine.py` | `build_context` computes volatility and ADX once (`_classify_volatility`, new `_compute_adx`) and passes both to `_classify_regime`/`_classify_trend`, which pass them straight through to `regime.py`/`trend.py`. |
| `src/futures_bot/engine.py` | `TradingEngine.__init__` resets `self.strategy.context = None` at construction; `on_bar` sets it unconditionally every bar (real value or `None`), not only in the `ENABLED` branch. |
| `tests/test_platform_verification_phase1.py` | `TestKnownLimitationStaleStrategyContextAcrossReusedInstances` renamed to `TestStaleStrategyContextAcrossReusedInstancesIsResolved`; its assertion inverted to prove the fix. |
| `tests/test_platform_verification_phase2.py` | New — 6 tests: precomputed-vs-fresh equivalence (including the `None`-is-used-as-is case), `ContextEngine` actually using the shared computation (not silently falling back), full `MarketContext` equivalence against calling every dimension independently, and the construction-time reset. |
| `docs/PLATFORM_VERIFICATION_PHASE2.md` | New — this document. |

No changes to any classifier's algorithm, threshold, or default value;
no changes to `MarketContext`, `RegimeContext`, `TrendContext`, or
`VolatilityContext`'s fields; no changes to any strategy; no API changes.

## Performance: before vs. after

**Methodology note:** a live `futures_bot.api` server (a legitimate,
separately-running process, not a leftover diagnostic) was found
consuming significant background CPU partway through this benchmark,
contaminating wall-clock timing exactly as Phase 1's own methodology
note described. It was confirmed via `Get-CimInstance Win32_Process`
and stopped by the user (not by this session) before the numbers below
were captured, in a verified-clean environment (`Get-Process python`
returned nothing).

**End-to-end backtest wall-clock time**, same `make_settings`/`make_bars`/
`_SimpleMomentum` workload as Phase 1's own benchmark
(`tests/test_platform_verification_phase1.py`'s helpers, reused verbatim):

| Bars | OFF | OBSERVE (Phase 1) | OBSERVE (Phase 2) | OBSERVE reduction |
|-----:|----:|-------------------:|-------------------:|------:|
| 400  | 104.2ms | 552.0ms | 444.4ms | 19.5% |
| 800  | 216.4ms | 2,186.5ms | 1,426.7ms | 34.8% |
| 1,600 | 368.9ms | 10,757.2ms | 6,189.6ms | 42.4% |

`OFF`'s own absolute time is **not** directly comparable session-to-session
(it never touches `context/` at all — identical code before and after this
phase — the difference is environmental: per-decision journal I/O timing,
disk/OS cache state, unrelated background load between the two measurement
sessions). The fairer, environment-normalized figure is each run's own
**marginal context-generation cost** (`OBSERVE − OFF`, computed within the
same run so both sides share the same environmental noise):

| Bars | Marginal cost (Phase 1) | Marginal cost (Phase 2) | Reduction |
|-----:|------------------------:|------------------------:|------:|
| 400  | 528.3ms | 340.2ms | 35.6% |
| 800  | 2,165.0ms | 1,210.3ms | 44.1% |
| 1,600 | 10,705.0ms | 5,820.7ms | 45.6% |

Converging toward **~45%** as bar count grows — consistent with Phase 1's
own cProfile finding that ADX/volatility together accounted for ~90% of
context-generation CPU time, each computed exactly twice; halving each
predicts almost exactly this reduction. `ENABLED` mode measured within
noise of `OBSERVE` (1,383.8ms vs. 1,426.7ms at 800 bars) — expected, since
`ENABLED` only adds a single attribute assignment (`self.strategy.context
= market_context`) over `OBSERVE`'s own cost.

**CPU reduction, directly measured** (`cProfile`, 800-bar `OBSERVE`
backtest, 800 `build_context` calls):

| Function | Before (Phase 1) | After (Phase 2) | 
|---|---:|---:|
| `strategy.indicators.adx` | 1,585 calls | **800 calls** |
| `context.volatility.analyze_volatility` | 1,600 calls | **800 calls** |

Exactly one call per `build_context` invocation for both — the
duplication is completely eliminated, not merely reduced.

**Peak memory** (`tracemalloc`, `OBSERVE` peak minus `OFF` peak):

| Bars | Phase 1 | Phase 2 | Reduction |
|-----:|--------:|--------:|------:|
| 400  | 322.9 KB | 295.7 KB | 8.4% |
| 800  | 698.6 KB | 629.2 KB | 9.9% |
| 1,600 | 1,386.2 KB | 1,289.8 KB | 7.0% |

A modest, secondary improvement (fewer intermediate `Decimal`/ADX
allocations) — memory was never the primary cost here, CPU was.

## Regression verification

- **Full test suite: 1,215 passed, 0 failed** (`.venv/Scripts/python.exe
  -m pytest -q`), identical pass count to Phase 1's own baseline.
- **124 targeted tests** across `test_context_regime.py`,
  `test_context_trend.py`, `test_context_engine_validation.py`,
  `test_context.py`, `test_platform_verification_phase1.py`,
  `test_engine_context_integration.py`, and
  `test_backtest_context_comparison.py` — all pass, including the
  inverted stale-context test.
- **6 new targeted tests** (`test_platform_verification_phase2.py`)
  directly proving byte-identical output: precomputed-vs-fresh
  equivalence for both `classify_regime` and `analyze_trend` (including
  the early-return-on-`UNKNOWN`-volatility edge case), `build_context`'s
  `regime_context.adx`/`trend_context.adx` agreeing with a single direct
  `adx()` call, and full dataclass-equality between `build_context`'s
  output and calling every dimension independently the old way.
- **No difference found** in trades, metrics, P&L, or reports —
  `TestBackwardCompatibilityRegression`'s eight exact-equality checks
  (entry/exit timestamps, entry/exit prices, exit reasons, net P&L, win
  rate, profit factor) all still pass unchanged, and the new equivalence
  tests prove *why*: the underlying classification math is provably
  identical, not just observed to produce the same trades on this one
  dataset.

## Risks eliminated

1. **Duplicate ADX/volatility computation** (Phase 1, Part 5) — closed.
   `adx()`/`analyze_volatility()` are now each computed exactly once per
   `build_context` call; verified by direct `cProfile` call counts (800/800,
   down from 1,585/1,600), not just inferred from wall-clock improvement.
2. **Stale `Strategy.context` across a reused instance** (Phase 1, Part 6)
   — closed. The reset is automatic (constructor + every bar), requiring
   no caller discipline; verified by both the inverted Phase 1 test and a
   dedicated construction-time test.

## Remaining risks

- **O(n²) full-replay cost is unchanged in kind, only reduced in
  constant factor.** `TradingEngine.bars` still grows by one bar per
  call, and volatility/regime/trend/timeframe/structure still re-derive
  their result from the entire history passed in each time (this is a
  correctness requirement for Wilder-style smoothing, not an oversight —
  see `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`). The ~45%
  reduction achieved here does not change the underlying growth curve —
  a long backtest or a fast-polling live/paper session in `OBSERVE`/
  `ENABLED` is still meaningfully more expensive than `OFF`, just less
  so than before. Phase 1's Recommendation #3 (pass a bounded trailing
  window rather than full history, a caller-side decision already fully
  supported by every classifier's own signature) remains open and
  unaddressed by this phase, deliberately — it was not one of the two
  verified defects this phase was scoped to fix.
- **`_UNSET`-sentinel pattern is now established in two modules
  (`regime.py`, `trend.py`) without a shared implementation.** Each
  module defines its own private `_UNSET = object()` — correct and
  sufficient (identity is only ever compared within its own function),
  but a third classifier needing the same precomputed-value pattern in
  the future would duplicate this pattern a third time rather than reuse
  a shared helper. Not worth introducing a shared abstraction for two
  call sites; worth revisiting if a third one appears.
- **Environmental benchmark noise**: this session's own measurement was
  contaminated once by a live `futures_bot.api` process before being
  corrected — a reminder that any future benchmark on this same
  development machine should first confirm (`Get-Process python`) that
  nothing else is running, rather than assume a clean environment.

## Final recommendation

**Safe to proceed.** Both verified defects from Platform Verification
Phase 1 are now fixed, with the fixes themselves independently verified
(not just "the existing tests still pass," though they do): precomputed
values are proven byte-identical to fresh computation, `build_context`'s
full output is proven identical to calling every dimension independently,
and the stale-context reset is proven automatic at both construction and
every-bar granularity. Performance improved substantially (~35–46%
reduction in per-run context-generation cost, converging toward ~45% as
history grows, with the duplicate computation itself eliminated
completely — 800/800 calls, not just fewer) with zero classification,
scoring, or API change. The remaining O(n²) replay-cost concern is a
known, already-documented, unaddressed-by-design item (Phase 1
Recommendation #3) — worth planning for before a context-aware strategy
is deployed against a very long history or a fast live-polling loop, but
not a defect and not in this phase's scope. No further work is required
before the first context-aware strategy is built.
