# Platform Verification Phase 1 — Market Context Integration Audit

Performed 2026-07-27 to prove the Market Context Engine's integration
into `TradingEngine` (the immediately preceding session's work) is
completely correct and introduces zero behavioral regressions, before
any context-aware trading logic is built on top of it. Read-only audit:
no new features, no optimizations — one genuine finding was surfaced and
is documented below, not fixed, per this phase's explicit scope.

New test coverage added this phase: `tests/test_platform_verification_phase1.py`
(25 tests). Combined with the existing `tests/test_engine_context_integration.py`
and `tests/test_backtest_context_comparison.py`, this audit is backed by
**77 dedicated tests**, not just manual inspection.

## 1. ContextMode behavior

| Item | Verdict | Evidence |
|---|---|---|
| `OFF` never generates `MarketContext` | **PASS** | `TestOneContextPerProcessedBar::test_off_mode_never_calls_build_context_at_all` — a spy `ContextEngine` records zero calls across a full backtest in `OFF`. |
| `OBSERVE` generates exactly one `MarketContext` per processed bar | **PASS** | `TestOneContextPerProcessedBar::test_context_engine_is_called_exactly_once_per_on_bar` and `TestNoDuplicateContextGenerationEndToEnd` (this phase) — call count equals bar count exactly, timestamps match 1:1, no duplicate timestamps. |
| `OBSERVE` never influences trading decisions | **PASS** | `TestExistingBehaviorUnchanged::test_observe_mode_produces_decisions_identical_to_off` (pre-existing) plus this phase's `TestBackwardCompatibilityRegression::test_observe_mode_also_matches_on_every_metric` — identical entry/exit timestamps, prices, exit reasons, net P&L, win rate, profit factor. Structurally guaranteed, not just tested: `OBSERVE` never executes the line that sets `Strategy.context` at all (see `engine.py`'s `on_bar`), so a strategy has no code path through which it could read the object. |
| `ENABLED` behaves identically to `OFF` for every existing bundled strategy | **PASS** | `TestBackwardCompatibilityRegression::test_enabled_mode_matches_for_a_strategy_that_opts_in_but_never_reads_context` (this phase) proves it even for a strategy that *does* set `uses_context = True` but never branches on `self.context` — the weakest case, not just the default (`uses_context = False`) case already covered elsewhere. |

## 2. Execution flow

Traced `on_bar`'s exact statement order in `engine.py`:

```
0. self.bars.append(bar)             -- bar enters history first
0. market_context = _build_market_context(now)   -- reads list(self.bars), includes this bar
0. Strategy.context set (ENABLED + uses_context only)
1. Broker resolves resting stop/target -> _record_trade if a position closed
2. risk.must_flatten -> _flatten (-> _record_trade) if forced
3. strategy.on_bar(self.bars, position)
3b. optional AI signal_filter (entry signals only)
4. _handle_signal -> risk.can_enter -> broker.submit_bracket -> _pending_entry_context = market_context
```

| Item | Verdict | Evidence |
|---|---|---|
| `MarketContext` generated exactly once per bar | **PASS** | Step 0 is the only call site; `_build_market_context` itself makes at most one `ContextEngine.build_context` call. Verified by call-count spy across a full backtest (see above), not just by reading the code. |
| Propagated correctly to the strategy | **PASS** | `Strategy.context` is set from the *same* `market_context` local variable used everywhere else this bar — never rebuilt, never read from a stale source. |
| Propagated correctly to the trade record | **PASS** | `_handle_signal` captures the *same* `market_context` into `self._pending_entry_context` only on a successful entry; `_record_trade` (the single shared closing path for every trade, regardless of cause) attaches it. Verified under a same-bar close-then-reenter stress test (`TestSameBarCloseThenReentry`, this phase, 28+ rapid flips) with zero cross-contamination. |
| Never duplicated | **PASS** | One `build_context` call per bar (verified); `_pending_entry_context` is consumed and cleared (`= None`) in every `_record_trade` call, so it cannot be attached to two different trades. |

## 3. Backward compatibility

All eight regression checks the task requested, run against a 600-bar
randomized backtest (`tests/test_platform_verification_phase1.py::TestBackwardCompatibilityRegression`):

| Metric | Verdict |
|---|---|
| Entry timestamps | **PASS** — identical, bar for bar |
| Exit timestamps | **PASS** |
| Entry prices | **PASS** |
| Exit prices | **PASS** |
| Exit reasons | **PASS** |
| Net P&L | **PASS** |
| Win rate | **PASS** |
| Profit factor | **PASS** |

**No difference found, at any precision.** These are exact-equality
assertions (`Decimal`/`datetime`/`str` comparison, not tolerance-based),
run against three separate call shapes: a pre-integration-style call
with no `context_mode` argument at all, an explicit `ContextMode.OFF`
call, and an explicit `ContextMode.OBSERVE` call. All three produce
byte-identical results.

## 4. MarketContext completeness and internal consistency

Every trade produced by an `OBSERVE`/`ENABLED` backtest was checked
(`TestMarketContextCompletenessAndConsistency`, this phase):

| Item | Verdict |
|---|---|
| Session present | **PASS** |
| Trend present | **PASS** |
| Regime present | **PASS** |
| Structure present | **PASS** |
| Volatility present | **PASS** |
| Liquidity present | **PASS** |
| Risk present | **PASS** |
| Environment Score present, in `[0, 100]` | **PASS** |
| Confidence present, in `[0.0, 1.0]` | **PASS** |
| Internal consistency (bare enum == nested object's own field) | **PASS** |
| `EnvironmentScore.score` == clamped sum of its own `breakdown` | **PASS** |

The internal-consistency guarantee is **structural, not incidental**:
`context_engine.py`'s `build_context` sets every bare-enum field
(`market_context.market_regime`, `.volatility_state`, etc.) from the
*same* intermediate object it also attaches as the rich nested context
(`.regime_context`, `.volatility_context`, etc.) — verified directly
rather than merely trusted.

## 5. Performance

**Methodology note, reported honestly:** initial wall-clock
measurements on this development machine showed severe, inconsistent
variance (the same 800-bar `OBSERVE` backtest measured anywhere from
2.0s to 28.5s across different attempts) traced to a runaway diagnostic
process from an earlier, overly ambitious benchmark attempt (10,000
bars) competing for CPU in the background — killed once identified.
Numbers below are from clean, uncontended runs, cross-checked against
`cProfile`'s CPU-attributed call graph (immune to wall-clock
contention) for the *relative* breakdown.

**End-to-end backtest overhead** (`SimpleEMA`-style strategy, random-walk bars):

| Bars | OFF | OBSERVE | Ratio |
|-----:|----:|--------:|------:|
| 400  | 23.7ms | 552.0ms | 23.3x |
| 800  | 21.5ms | 2,186.5ms | 101.9x |
| 1,600 | 52.2ms | 10,757.2ms | 206.3x |

Growth is **worse than linear, consistent with the already-known
O(n) per-call / O(n²)-over-a-full-replay characteristic** documented in
Phase 8's `CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md` — `TradingEngine.bars`
grows by one bar per call, and four of the eight classification
dimensions (volatility, regime, trend, timeframe) plus structure
re-derive their result from the *entire* history available each time.

**Peak memory overhead** (`tracemalloc`, `OBSERVE` minus `OFF`):

| Bars | Delta |
|-----:|------:|
| 400  | 322.9 KB |
| 800  | 698.6 KB |
| 1,600 | 1,386.2 KB |

Scales linearly with bar count, bounded by `TradingEngine.bars`' own
retention cap (2000, or 4× a strategy's `warmup_bars`) — no leak, no
unbounded growth (see Part 6 below for the explicit check).

**Unnecessary work identified — one significant finding, not fixed
(measurement only, per this phase's scope):**

`cProfile` against a real 800-bar `OBSERVE` backtest attributes **~71%**
of all context-generation CPU time to `strategy.indicators.adx` and
another **~19%** to `volatility.analyze_volatility`/`atr_series` —
**~90% combined**. Tracing the call graph: `context/regime.py`'s
`classify_regime` calls `adx()` directly; `context/trend.py`'s
`analyze_trend` **also** calls `adx()` directly, with the exact same
`bars`/`period` — this is the same computation performed **twice** per
bar, unnecessarily (both calls are correct, both produce the identical
result — this is pure waste, not a correctness bug). The same pattern
repeats one level down: `context/context_engine.py`'s
`_classify_volatility` calls `analyze_volatility()` directly, while
`regime.classify_regime` **also** calls `analyze_volatility()` internally
to obtain its own volatility signal — a second duplicate computation.
Call counts confirm this precisely: 1,585 `adx()` calls and 1,600
`analyze_volatility()` calls were recorded for 800 `build_context`
invocations (≈2× each, as expected for a clean duplication).

**Recommendation (not implemented — flagged for a future, dedicated
optimization pass):** thread a single computed `adx`/`VolatilityContext`
result through `ContextEngine.build_context` once, and have
`regime.classify_regime`/`trend.analyze_trend` accept it as an optional
pre-computed argument instead of each deriving it independently. Given
the measured proportions, this would plausibly cut context-generation
CPU cost by somewhere in the 40–60% range without changing any
classification's output — a genuine, high-value, low-risk optimization
target for whenever a future phase is authorized to make it, but
deliberately **not implemented in this audit phase**, which was scoped
to measure and report only.

## 6. Hidden-issue audit

| Category | Verdict | Notes |
|---|---|---|
| Silent logic errors | **PASS** | None found in the integration code itself (`engine.py`, `models.py`, `strategy/base.py`, `backtest/runner.py`, `backtest/context_comparison.py`). |
| Off-by-one mistakes | **PASS** | `_build_market_context` is called *after* `self.bars.append(bar)`, so `list(self.bars)[-1]` is always the bar that just closed — verified directly, not assumed. Same-bar close-then-reenter sequencing verified under a 28-trade rapid-flip stress test with zero cross-contamination. |
| Look-ahead bias | **PASS** | No new risk introduced — `_build_market_context` reads only `self.bars` (history up to and including the current bar); every individual classifier's own look-ahead safety was already audited in Phase 8 (`docs/CONTEXT_ENGINE_LOOKAHEAD_AUDIT.md`) and is unchanged by this integration. |
| Duplicate context generation | **WARN** (naming clarification) | *Per-bar* generation is exactly-once, verified (Part 1/2 above) — this is what "duplicate context generation" in the requirements literally asks about, and it is a clean PASS. The duplicate work found in Part 5 (ADX/volatility computed twice *within* a single `build_context` call) is a distinct, lower-level inefficiency inside `context/`'s own classifiers, not a second `MarketContext` being generated — flagged there, not double-counted here. |
| Circular dependencies | **PASS** | `models.py`'s and `strategy/base.py`'s references to `context/` are both `TYPE_CHECKING`-guarded (verified: `"MarketContext" not in vars(module)` at runtime); `engine.py`'s reference is real, by design, one-directional (`engine.py -> context/`, never the reverse). Verified by subprocess-isolated standalone imports of every affected module (`TestNoCircularImports`), not just reasoned about. |
| Memory leaks | **PASS** | `ContextEngine` holds no accumulating state across calls (confirmed by direct inspection: only `symbol`/`timeframe`/`scoring_config`, all set once at construction). `TradingEngine._pending_entry_context` holds at most one reference, always cleared after use. `TradingEngine.bars` is a pre-existing bounded `deque`, unaffected by this integration. Peak memory scales linearly with bar count, bounded by that same cap (Part 5). |
| Thread-safety concerns | **PASS** (no new concerns) | `TradingEngine` was already designed for single-threaded, one-instance-per-run operation (unsynchronized instance attributes throughout, e.g. `strategy_error_count`); `_pending_entry_context` and `Strategy.context` follow the same existing, unsynchronized pattern. No new shared mutable state was introduced between separate `TradingEngine`/`ContextEngine` instances — each owns its own. |
| Future maintenance risks | **WARN** — one real finding | See below. |

### Maintenance risk found: stale `Strategy.context` across a reused instance

**Verified by direct reproduction, not theoretical:** if the *same*
`Strategy` instance is passed to two separate `TradingEngine`/
`run_backtest` calls — first with `ContextMode.ENABLED` (setting
`self.context`), then with `ContextMode.OFF` or a mode where the
strategy doesn't opt in — `self.context` from the *first* run is still
sitting there during the second, because neither `OFF` nor a
non-opted-in path ever resets it. Confirmed with
`tests/test_platform_verification_phase1.py::TestKnownLimitationStaleStrategyContextAcrossReusedInstances`.

**Does not affect any current behavior:** every existing call site in
this codebase (`cli.py`, `api/services.py`, `api/live_session.py`,
`research_server/paper_trader.py`) constructs a fresh `Strategy`
instance per run — confirmed by direct inspection of all four call
sites, none reuse an instance across runs. This is why every
backward-compatibility check in Part 3 still passed cleanly.

**Recommendation (not fixed, per this phase's scope):** before a future
phase encourages patterns like looping over parameter grids with a
manually-reused strategy instance (a plausible future need for
research/optimization tooling), have `on_bar` unconditionally reset
`self.strategy.context = None` at the start of every bar, then
re-populate it only under the `ENABLED` + `uses_context` condition —
closing the gap defensively rather than relying on every future caller
remembering to construct a fresh instance.

## Confidence level

**High.** Every item in this audit was verified with an executable
test, a direct reproduction, or a structural code guarantee — not
assumed because other tests passed. Two findings were surfaced (one
performance, one latent-but-currently-harmless maintenance risk); zero
correctness defects. 77 dedicated tests now cover this integration
specifically (25 new this phase), on top of the existing full suite.

## Recommendations before the first context-aware strategy is built

1. **Address the ADX/volatility duplicate computation** (Part 5) before
   relying on `ContextMode.ENABLED`/`OBSERVE` in any performance-sensitive
   context (a long backtest, a parameter sweep, live polling at a fast
   interval) — not required for correctness, but a meaningful, low-risk
   win once authorized.
2. **Close the stale-`Strategy.context` gap** (Part 6) defensively before
   any tooling starts reusing strategy instances across runs (e.g. a
   future grid-search or A/B-sweep helper) — cheap to fix, currently
   harmless only because no such tooling exists yet.
3. **Budget for the O(n²) replay cost** when designing how a
   context-aware strategy will actually be backtested — either accept
   it for now, or revisit the "pass a bounded trailing window, not full
   history" recommendation already on record in
   `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`.
4. No changes are required to `ContextMode`, `Trade.entry_context`,
   `Strategy.context`/`uses_context`, or the A/B comparison framework
   themselves — all four are verified correct as built.
