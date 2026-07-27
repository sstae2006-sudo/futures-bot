# Market Context Engine — Coverage Report

Generated as Phase 8, Part 7 ("Context Coverage Report") of the Market
Context Engine's completion phase (2026-07-27). Snapshot of every
implemented capability as of that date — re-derive test counts with
`pytest tests/test_context*.py --collect-only -q` if this file is ever
suspected stale relative to the code.

## Coverage table

| Context Module | Status | Test Coverage | Confidence Model | Dependencies | Integration Ready |
|---|---|---|---|---|---|
| **Session** | Real (Phase 2a) | 31 tests (`test_context_session.py`) | 1.0 whenever classifiable — deterministic given timestamp+calendar, no data uncertainty | `contracts.py` (CME calendar) | Yes — needs no bars, always classifiable |
| **Volatility** | Real (Phase 2b) | 22 tests (`test_context_volatility.py`) | Not a separate score — `VolatilityState`/ratio computed directly, `UNKNOWN` only when insufficient bars | `strategy.indicators.atr_series` | Yes — needs `atr_period + 1` bars (15 default) |
| **Regime** | Real (Phase 2c) | 21 tests (`test_context_regime.py`) | `[0,1]`, per-branch formula (e.g. `min(1, adx/50)`) | `strategy.indicators.adx`, `research.regime.classify_trend`, `context/volatility.py` | Yes — needs `2 * adx_period` bars (28 default) for full classification; degrades to volatility-only below that |
| **Multi-Timeframe Alignment** | Real (Phase 2d) | 14 tests (`test_context_timeframe.py`) | `alignment_score` `[0,1]`, rank-weighted agreement magnitude | `research.regime.classify_trend` (per timeframe) | Yes — needs a `bars_by_timeframe` mapping from the caller; degrades gracefully per-timeframe otherwise |
| **Structure** | Real (Phase 2e) | 17 tests (`test_context_structure.py`) | `structure_confidence` `[0,1]`, share of consistent pairwise swing comparisons | None (genuinely new; no existing swing/support-resistance equivalent) | Yes — needs `2 * swing_window + 1` bars (7 default) for any reading, more for a stable one |
| **Environment Score** | Real (Phase 2f, configurable Phase 8) | 30 tests (`test_context_scoring.py`) | `confidence` = fraction of the 6 sub-dimensions with real data | Reads `MarketContext`'s own fields only — no bars, no new computation | Yes — always populated; quality scales with how many sub-dimensions have data |
| **Trend (standalone)** | Real (Phase 8) | 14 tests (`test_context_trend.py`) | `[0,1]`, ADX-scaled (reuses `regime.py`'s constants) | `research.regime.classify_trend`, `strategy.indicators.adx` | Yes — direction needs only 2 closes; confidence needs `2 * adx_period` bars |
| **Liquidity** | Real (Phase 8) | 19 tests (`test_context_liquidity.py`) | `[0,1]`, distance-from-threshold shape (mirrors `regime.py`'s pattern) | `strategy.indicators.sma` | Yes — needs at least 1 bar (trivial ratio of 1.0), meaningful with `lookback` (20 default) |
| **Risk** | Real (Phase 8) | 17 tests (`test_context_risk.py`) | `[0,1]`, higher when driven by the direct volatility signal, lower for the regime-only fallback | `volatility_state` + `market_regime` (pure composite — no bars, no new indicator) | Yes — as good as its two inputs; `UNKNOWN` only if both are |
| **Cross-cutting validation** | Complete (Phase 8, Part 3) | 16 tests (`test_context_engine_validation.py`) | N/A (structural checks, not a data dimension) | All of the above | N/A |
| **Foundation (`MarketContext`/`ContextEngine` shape, serialization)** | Complete (Phase 1) | 20 tests (`test_context.py`) | N/A | All of the above | Yes |
| **Context Analytics** | Complete (Phase 8, Part 6) | 12 tests (`test_context_analytics.py`) | N/A (developer/research tool, not a context dimension) | `context/models.py` only | N/A — dev/research tool, not part of the trade-decision path |
| **Total** | — | **233 tests** | — | — | — |

Liquidity and Risk enum-only checks were still "safe defaults, never
classified" through Phase 7; both are real classification as of Phase
8. `TrendState`/`LiquidityState`/`RiskState` are the last three
dimensions completed — every dimension `context/models.py` defines a
field for is now real.

## What "Integration Ready" means here

Every dimension above is internally complete, tested, look-ahead-safe
(see `docs/CONTEXT_ENGINE_LOOKAHEAD_AUDIT.md`), and handles missing data
without raising. "Integration Ready" in this table means *that specific
dimension* is ready to be consumed once a future phase wires
`MarketContext` into `TradingEngine`/`Strategy` — it does **not** mean
that wiring has happened, or is being proposed here. Per this phase's
own explicit instructions, **no integration into `TradingEngine`,
`StrategyEngine`, `RiskEngine`, backtesting, live trading, or broker
code has occurred** — see `docs/CONTEXT_ENGINE_ARCHITECTURE_REVIEW.md`
for the verification of that boundary.

## Known limitations (carried into the Future Integration Plan)

- **No database persistence.** `MarketContext`/`EnvironmentScore`
  snapshots are never stored — Phase 5 in `ROADMAP.md` remains "maybe,
  needs explicit approval" and undecided.
- **Performance scales with history size for four dimensions**
  (volatility/regime/trend/timeframe) and structure by design — see
  `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`. A future integration
  should pass a bounded trailing window of bars, not full history, to
  avoid O(n²) cost over a long backtest replay.
- **`TrendState`/`MarketRegime` are two independent trend readings**
  (a simple direction-only signal vs. a volatility-coupled composite)
  that can legitimately disagree — this is intentional (see
  `context/trend.py`'s module docstring), not a bug, but a future
  consumer needs to know which one it actually wants.
- **`ScoringConfig`'s default weights are illustrative**, chosen to
  reproduce this phase's own worked example exactly, not derived from
  any backtest of which dimensions actually predict performance —
  genuine weight research is explicitly future work the configurable
  system (Phase 8, Part 2) now supports without code changes.
