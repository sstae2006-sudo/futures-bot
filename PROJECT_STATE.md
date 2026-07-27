# PROJECT_STATE.md

Snapshot of where the project actually stands. Update this after every
session (section 6 of CLAUDE.md). If the running project doesn't match
this file, stop and explain why before coding — don't silently
continue on a stale assumption.

## Current Version

`0.7.0` (`pyproject.toml`). No CI/release process configured yet —
version is bumped by hand.

## Backend Status

- Installs cleanly: `pip install -e .` pulls every dependency needed to
  run the API (fastapi/uvicorn/python-multipart/openpyxl/tzdata are
  base deps, not an optional extra — fixed 2026-07-26).
- Boots and serves real requests: verified in a clean venv, zero manual
  installs, `python -m futures_bot.api` → `GET /api/system/overview`
  returns real data from `research.db`.
- **Official startup method as of 2026-07-27: `scripts\start.ps1`**
  (or double-click `start.cmd`). One command boots backend + frontend
  together, verified end-to-end repeatedly this session (fresh boot,
  status check, mid-run backend kill detected correctly, restart,
  clean stop, missing-venv hard-failure, missing-database
  degraded-but-successful boot). See `BOOT_CHECKLIST.md` section 4 and
  `CLAUDE.md` section 9.
- Test suite: 1163 tests as of 2026-07-27 (1074 + 89 new Market Context
  Engine Phase 8 tests — Trend/Liquidity/Risk State, configurable
  scoring, engine validation, and context analytics), full suite green
  (1163 passed, 0 failed). One test
  (KNOWN_ISSUES.md ISSUE-002) is a known
  test-order-dependent flake — treat an isolated failure there as the
  known flake, not a new regression, until it's root-caused. Requires
  the `ml` extra (`pip install -e ".[dev,ml]"`) for the ML
  dataset/training/predict test modules to even collect — without it
  they fail collection with `ModuleNotFoundError`, not a real failure.
- Python: 3.12.10. Project `.venv` at repo root already has `dev`+`ml`
  extras installed.
- `python -m futures_bot.cli --validate-db` runs a permanent, read-only
  data-integrity validator against `market_data.db` — see
  `docs/DATABASE_VALIDATION.md`. **Currently exits 1 (VALIDATION
  FAILED)** on the live database: two known, not-yet-fixed findings
  (KNOWN_ISSUES.md ISSUE-004 schema drift, ISSUE-005 genuine OHLC
  violations in raw `US80Z` source data). A different or additional
  FAIL is a real regression worth stopping for.

## Frontend Status

- Vite + React + TypeScript dashboard in `frontend/`.
- **`npm run dev` is currently broken on Windows** (KNOWN_ISSUES.md,
  new issue logged 2026-07-27): its `kill-vite.js` pre-step kills its
  own node.exe process, so `vite` never starts. Not fixed (this
  session was told not to modify existing frontend files) —
  `scripts\start.ps1` works around it by calling `vite.cmd` directly
  with `--host 127.0.0.1`, confirmed working end-to-end. Manual
  frontend debugging should use `npx vite --host 127.0.0.1` in
  `frontend/`, not `npm run dev`, until the underlying script is fixed.
- `npm run lint` (oxlint) and `npm test` (vitest) are wired up; there's
  no `npm run format` script.

## Completed Features

- Contract specs (MES/MNQ/M2K/MYM), CME session arithmetic, risk
  manager (kill switch, trade cap, trading-hours filter, force-flat),
  durable state, paper broker, structured decision journal.
- Backtest engine + HTML/text reports; four reference strategies (EMA
  crossover, opening range breakout, VWAP reversion, trend pullback).
- Market-data pipeline: sync/scheduler/store backed by Massive's
  contracts and flat-file APIs.
- Grid-search optimizer with train/validation split and walk-forward
  option; ML research workstation (dataset build, training, prediction).
- Market Context Engine **complete as an independent subsystem**
  (Phase 8, 2026-07-27, `src/futures_bot/context/`): typed
  `MarketContext` value object + `ContextEngine`. **Every dimension is
  real** — `session.py`, `volatility.py`, `regime.py`, `timeframe.py`,
  `structure.py`, `trend.py`, `liquidity.py`, `risk.py` — plus a
  combined 0-100 informational score (`scoring.py`'s
  `score_environment`, weights configurable via `ScoringConfig`, never
  consulted for a trade decision) and developer analytics
  (`analytics.py`'s `analyze_context_batch`, distribution reports over
  a batch of contexts). Internally validated (no circular imports, no
  duplicated logic, deterministic, missing-data-safe — see
  `tests/test_context_engine_validation.py`), audited for look-ahead
  bias with no issues found (`docs/CONTEXT_ENGINE_LOOKAHEAD_AUDIT.md`),
  and benchmarked (`docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`,
  `tools/benchmark_context_engine.py`). **Not wired into
  `TradingEngine`/`Strategy` yet — deliberately, pending explicit
  approval** (verified: zero changes to `strategy/`/`engine.py`/`risk/`/
  `brokers/`/`backtest/` across every phase of building this — see
  `docs/CONTEXT_ENGINE_ARCHITECTURE_REVIEW.md`). See
  `docs/ARCHITECTURE.md`'s "Market Context Engine" section and
  `docs/CONTEXT_ENGINE_COVERAGE.md` for the full per-dimension
  breakdown.
- FastAPI research server + React dashboard covering all of the above,
  plus an autonomous paper-trading/nightly-jobs layer
  (`research_server/`).
- Tradovate live-broker adapter; trade-import/reconciliation pipeline.
- Deploy: Dockerfiles (CLI + API), docker-compose, systemd units,
  bare-metal deployment doc.

## Broken / Incomplete Features

- No CI configured — tests only run when a session runs them by hand.
- No Python formatter/linter configured (no ruff/black/mypy).
- KNOWN_ISSUES.md ISSUE-004 (`bars` schema drift) and ISSUE-005
  (`US80Z` genuine OHLC violations) — both diagnosed, neither fixed.

## Priorities

See ROADMAP.md.

## Last Completed Work

2026-07-27: **Market Context Engine Phase 8 — completion and
validation** (11-part phase; the engine is now considered
production-ready as an independent, unintegrated subsystem):

- **Part 1 — the final three dimensions, real:** `context/trend.py`
  (standalone `TrendState`, reusing `research.regime.classify_trend` +
  `regime.py`'s ADX confidence constants — independent of
  `regime.py`'s volatility-coupled composite, available with far less
  history); `context/liquidity.py` (`LiquidityState` from relative
  volume, reusing `strategy.indicators.sma` — genuinely new
  classification logic, documented why); `context/risk.py`
  (`RiskState` as a pure composite of already-real `volatility_state`/
  `market_regime`, exactly as this method's own Phase-1 stub docstring
  anticipated — no new market-data analysis at all). Wired into
  `MarketContext` (new `trend_context`/`liquidity_context`/
  `risk_context` fields). 50 new tests
  (`test_context_trend.py`/`test_context_liquidity.py`/
  `test_context_risk.py`).
- **Part 2 — configurable scoring:** `scoring.ScoringConfig`
  centralizes all six weights (previously hardcoded module constants);
  `DEFAULT_SCORING_CONFIG` reproduces every pre-Phase-8 scoring test
  exactly (verified — zero test changes needed for the 21 pre-existing
  scoring tests to keep passing). `ContextEngine.__init__` gained an
  optional `scoring_config` parameter. 9 new tests.
- **Part 3 — engine validation:** `tests/test_context_engine_validation.py`
  (16 tests) encodes no-circular-imports, no-duplicated-logic (ATR/ADX/
  SMA/classify_trend each defined exactly once), no-duplicated-
  calendars/enums, module independence from `risk.manager`/`brokers`/
  `engine.py`, determinism, missing-data safety, `UNKNOWN` correctness,
  and confidence validity as lasting, executable checks. Found and
  fixed a real bug **in the validation test itself**: an early draft
  used `importlib.reload()` to check standalone-importability, which
  mints duplicate Enum class objects and broke `is`-identity for every
  later test in the same process — replaced with genuine subprocess
  isolation.
- **Part 4 — look-ahead bias audit:** `docs/CONTEXT_ENGINE_LOOKAHEAD_AUDIT.md`
  — explicit module-by-module reasoning for all 8 dimensions plus the
  combined score. No issues found; every module was already built
  under a "trailing-window-only" / "confirmation lag ≠ leakage" / "no
  bars read at all" discipline, each with its own dedicated test.
- **Part 5 — performance benchmark:** `tools/benchmark_context_engine.py`
  measured avg/worst-case timing and peak memory across 50–50,000 bars
  (results in `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`). Found
  and fixed one real inefficiency: `liquidity.py` was converting every
  bar's volume to `Decimal` even though only the trailing `lookback`
  bars are ever used — fixed to slice first, verified output-identical.
  ATR/ADX-based dimensions remain O(n) by necessity (Wilder's smoothing
  is seed-sensitive to truncation) — documented, not silently
  approximated.
- **Part 6 — context analytics:** `context/analytics.py`'s
  `analyze_context_batch`/`ContextAnalyticsReport` — session/regime/
  volatility/trend/liquidity/risk distributions, environment-score and
  confidence numeric summaries, and UNKNOWN-frequency-per-dimension
  over a batch of already-built contexts. Dev/research tool, no UI, not
  wired into `ContextEngine`. 12 new tests.
- **Part 7 — coverage report:** `docs/CONTEXT_ENGINE_COVERAGE.md` — a
  table of every dimension's status/test-count/confidence-model/
  dependencies/integration-readiness.
- **Part 8 — architecture review:** `docs/CONTEXT_ENGINE_ARCHITECTURE_REVIEW.md`
  — confirmed via `git status`/`git diff` and source-inspection tests
  that zero files outside `context/`/`tests/test_context*.py`/`docs/`/
  `tools/benchmark_context_engine.py` changed across this entire
  multi-phase effort, and that nothing outside `context/` references it
  either (checked in both directions).
- **Part 9 — testing:** 89 new tests this phase (1074 → 1163), full
  suite green throughout every part. One backward-compatibility edge
  case discovered and covered: `MarketContext.from_dict` correctly
  handles a dict shaped like an earlier phase's output (missing the
  newer `trend_context`/`liquidity_context`/`risk_context`/
  `environment_score` keys entirely, not just null) — new regression
  test in `test_context.py`.
- **Part 10 — documentation:** `CLAUDE.md`, `PROJECT_STATE.md`,
  `ROADMAP.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md` all updated;
  four new dedicated docs (coverage/lookahead-audit/performance-
  benchmark/architecture-review) plus the benchmark tool.

**Not done, deliberately:** no integration into `TradingEngine`,
`Strategy`, `RiskEngine`, backtesting, live trading, or broker code —
explicitly out of scope for this phase, needs its own explicit
approval per CLAUDE.md section 8.

2026-07-27: implemented the Context Scoring System
(`src/futures_bot/context/scoring.py`,
`score_environment`/`EnvironmentScore`) — combines every existing
`MarketContext` dimension into a single 0-100 "Market Environment
Score": how favorable current conditions look for a systematic strategy
to operate in *generally* (clear trend, normal volatility, a liquid
session, confirmed structure, ample liquidity, manageable risk) --
**not a directional signal, information only, per the task's own
instructions**: `EnvironmentScore` carries no broker/risk-manager/
engine reference of any kind, verified by a test inspecting the
module's own imports. Six dimensions each contribute a signed value
scaled by a documented maximum weight (Trend 20, Volatility 15, Session
10, Structure 20, Liquidity 15, Risk -10 -- chosen to reproduce the
task's own worked example exactly: `20+15+10+20+15-10 == 70`,
manually verified against the live module before trusting it as a test
assertion); the total is clamped to `[0, 100]`. A dimension with no
data contributes exactly `0.0` and is excluded from both the `reasons`
explanation list and the `confidence` fraction. Since
`liquidity_state`/`risk_state` are still `UNKNOWN` stubs everywhere
else in this codebase, they always contribute `0.0` through
`ContextEngine` today -- a real, already-exercised "missing data"
case, covered by a test asserting today's practical score ceiling (65,
not 100). `confidence` is the fraction of the six dimensions that
actually had data, independent of the score's own value (a test
constructs a "full data, worst possible readings" scenario to prove
this explicitly). Wired into `MarketContext` (new `environment_score`
field, always populated by `ContextEngine.build_context` via a
two-step construction: the base `MarketContext(...)` call, then
`scoring.with_environment_score` -- a `dataclasses.replace` -- since
the score depends on every other field already being set). 20 new
tests (`tests/test_context_scoring.py`, covering the exact worked
example, the reason-phrasing example, confidence aggregation,
clamping at both ends, missing-data handling, full `MarketContext`
integration, and the information-only import-boundary check). Full
suite green (1074 passed, 0 failed, 20 new). Trend (standalone),
liquidity, and risk remain stubs -- out of scope this phase.

2026-07-27: implemented Market Structure Context
(`src/futures_bot/context/structure.py`,
`analyze_structure`/`StructureContext`) — detects price structure from
confirmed swing points: higher-highs/higher-lows (`TrendState.BULLISH`
structure) or lower-highs/lower-lows (`TrendState.BEARISH`), nearest
support/resistance levels around the current price, and distance from
them. Strictly descriptive: `StructureContext` carries no broker/risk-
manager/engine reference of any kind, never generates a trade, never
overrides a strategy's own signal. No existing equivalent to reuse in
this codebase (genuinely new work, same disclosure `regime.py` gives
for liquidity/risk), though it reuses `TrendState` rather than a fourth
bullish/bearish/neutral vocabulary. A swing point is confirmed via a
standard fractal definition (a bar's high/low strictly beats every
high/low within `DEFAULT_SWING_WINDOW`=3 bars on both sides); this
requires bars chronologically after a candidate swing, but since every
bar this module sees is already-completed history, that's confirmation
lag, not a look-ahead violation -- documented explicitly since "avoid
future leakage" has been the standing theme of every prior phase, and
verified by a dedicated test that the most recent bars simply have no
confirmed swing yet. Caught and fixed a real test-data bug during
manual verification (not just via the tests written alongside it): an
early zigzag-fixture generator produced *rising* cycle lows regardless
of the intended drift direction, which would have silently mislabeled a
"downtrend" fixture as bullish. Wired into `MarketContext` (new
`structure_context` field, `ContextEngine._classify_structure` now
real). 17 new tests (`tests/test_context_structure.py`, covering
higher-highs/higher-lows, lower-highs/lower-lows, support/resistance
bracketing, the task's own worked example shape, a flat/no-structure
case, missing data, confirmation-lag-is-not-leakage, and an explicit
check that the module imports nothing from `risk.manager`/`brokers`/
`engine`). Full suite green (1054 passed, 0 failed, 17 new). Trend
(standalone), liquidity, and risk remain stubs — out of scope this
phase.

2026-07-27: implemented Multi-Timeframe Context
(`src/futures_bot/context/timeframe.py`,
`classify_timeframe_alignment`/`TimeframeAlignment`) — combines trend
direction across five canonical timeframes (`1m`/`5m`/`15m`/`1h`/`1d`)
into one alignment reading: a dict of timeframe -> `TrendState` for
whichever timeframes had data, plus `alignment_score` (the magnitude,
`[0.0, 1.0]`, of a rank-weighted average direction). Reuses
`research.regime.classify_trend` per timeframe -- the same function
`regime.py` already uses for its own trend signal -- rather than a
second trend definition; its "sideways" maps onto `TrendState.NEUTRAL`
(previously unused outside the still-stubbed `trend_state` field).
Look-ahead safety here is stricter than any single-stream classifier so
far: a coarser timeframe's last bar can look "at or before now" by
timestamp alone while still being in-progress (e.g. a 1-hour bar
opened at 09:00 hasn't closed by 09:05), so this module tracks each
timeframe's actual duration and only keeps a bar once its close time
has genuinely passed -- verified by a dedicated test constructing
exactly that scenario. Missing/short timeframe data is handled safely:
omitted, empty, or under-2-bar timeframes are simply left out of
`alignment`, never an error or a fabricated direction. Wired into
`MarketContext` (new `timeframe_alignment` field; `ContextEngine.build_context`
gained an optional `bars_by_timeframe` parameter, independent of its
existing `bars`/`self.timeframe`). 14 new tests
(`tests/test_context_timeframe.py`, covering the task's own worked
example shape, full agreement, an even bullish/bearish split, missing
data in several forms, the in-progress-bar leakage scenario, and
serialization). Full suite green (1037 passed, 0 failed, 14 new). Trend
(standalone), liquidity, and risk remain stubs — out of scope this
phase.

2026-07-27: implemented Market Regime Detection
(`src/futures_bot/context/regime.py`, `classify_regime`/`RegimeContext`) —
classifies overall market behavior into one of five mutually exclusive
`MarketRegime` values: `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`,
`HIGH_VOLATILITY`, `LOW_VOLATILITY`. Redefined `MarketRegime` from Phase
1's placeholder set (`TRENDING`/`RANGING`/`VOLATILE`) to this exact
taxonomy — confirmed zero usages outside `context/`'s own tests before
changing it (same discipline as `SessionPhase`'s Phase 2a rename).
Combines three reused signals rather than re-deriving any of them:
`strategy.indicators.adx` for trend strength (conventional ADX >= 25
"actually trending" threshold), `research.regime.classify_trend` for
trend direction (bullish/bearish/sideways, already look-ahead-safe and
already used for this purpose elsewhere), and this package's own
`volatility.analyze_volatility` for the volatility signal. Priority when
signals disagree is explicit: extreme volatility dominates trend/range
labeling; otherwise a strong, directional ADX reading wins; otherwise
low volatility is its own label; otherwise the default is `RANGING`.
`confidence` is always `[0.0, 1.0]` via a small documented formula per
branch (trending confidence `min(1.0, adx/50.0)` — matches the task's
own worked example, ADX 39 -> 0.78 exactly). No parameter optimization
this phase — every threshold is either reused from elsewhere in this
codebase or an unmodified textbook default (ADX 25). Wired into
`MarketContext` (new `regime_context` field, `market_regime` now real,
confidence recorded only once `classify_regime` produced a non-`UNKNOWN`
reading). 21 new tests (`tests/test_context_regime.py`, covering
trending-up/down, ranging, high/low volatility, extreme-volatility
priority over a concurrent trend, confidence-formula correctness,
missing/partial data, and a dedicated no-future-leakage test). Also
updated 6 pre-existing `tests/test_context.py` assertions/renamed 2
tests that referenced the old `MarketRegime` member names or assumed
`market_regime` was still an unconditional stub. Full suite green (1023
passed, 0 failed, 21 new). Trend/liquidity/risk remain stubs — out of
scope this phase.

2026-07-27: implemented Volatility Context (`src/futures_bot/context/volatility.py`,
`analyze_volatility`/`VolatilityContext`) — real ATR-ratio-based volatility
classification (`VolatilityState`: LOW/NORMAL/HIGH/EXTREME/UNKNOWN,
unchanged since Phase 1). Reuses `strategy.indicators.atr_series` (the same
Wilder's-smoothing ATR every strategy already uses) rather than
re-deriving true-range math; `current_atr` is the series' last value,
`average_atr` is the mean of a trailing window ending at that same value
(default lookback 20), `volatility_ratio = current_atr / average_atr`
classified via fixed, documented thresholds (matches the task's own
worked example: ratio 1.5 → HIGH). Also computes `realized_volatility`
(stdev of simple returns over the same window, unannualized — new, no
prior equivalent). Deliberately did **not** reuse
`research/regime.py`'s `classify_volatility`/`compute_regimes` as-is:
its tercile cutoffs are computed via `sorted()` over the *entire* bars
series up front, which is correct for its own post-hoc trade-labeling
use case but not look-ahead-safe for real-time "as of timestamp T"
classification — `analyze_volatility` only ever reads a trailing window
ending at the last bar it's given, verified directly by a dedicated
no-look-ahead test (`tests/test_context_volatility.py`'s
`TestNoFutureDataLeakage`: a truncated-history reading is provably
unaffected by bars appended after it). Wired into `MarketContext` (new
`volatility_context` field, `ContextEngine._classify_volatility` now
real, confidence recorded only once enough history exists to compute a
ratio). 22 new tests (`tests/test_context_volatility.py`, covering low-vol,
high-vol, missing/insufficient data, no-future-leakage, serialization,
and multi-symbol/timeframe support). Full suite green (1002 passed, 0
failed, 22 new). The other four `MarketContext` dimensions
(regime/trend/liquidity/risk) remain stubs — out of scope this phase.

2026-07-27: implemented Session Context (`src/futures_bot/context/session.py`,
`classify_session`/`SessionContext`) — real classification of the seven
futures-market session phases (`OVERNIGHT`, `PRE_MARKET`, `OPENING_RANGE`,
`MORNING_SESSION`, `LUNCH_SESSION`, `POWER_HOUR`, `MARKET_CLOSE`), reusing
`contracts.py`'s existing CME calendar logic (`is_weekend_closure`,
`is_cme_holiday`, `in_maintenance_halt`, `is_market_open`) and
`research/regime.py`'s exact RTH boundaries rather than inventing a new
calendar. Handles weekends/holidays (classify as `OVERNIGHT` with
`is_market_open=False`/`liquidity_expectation="NONE"`, not an eighth
"closed" phase) and the daily maintenance halt. Found and fixed a real
bug during manual verification (not just via the tests written
alongside it): `minutes_since_open` was wrong throughout the 16:00–17:00
CT halt because `contracts.session_date()` attributes a halt moment to
the *upcoming* session, the wrong reference point for that specific
calculation — fixed with a self-contained "most recent 17:00 CT"
formula, independent of `session_date()`'s kill-switch-oriented
semantics. Wired into `MarketContext` (new `session_context` field,
`ContextEngine._classify_session` now real) and verified against the
task's own spec example exactly (`OPENING_RANGE`, 12 minutes,
`"HIGH"` liquidity at 08:42 CT). `tests/test_context_session.py` (31
tests, not `test_session.py` — that name was already taken by
`futures_bot.session`'s unrelated tests). Full suite green (980
passed, 0 failed, 31 new). The other five `MarketContext` dimensions
(regime/volatility/trend/liquidity/risk) remain stubs — out of scope
this phase, per instructions.

2026-07-27: built the foundation for a Market Context Engine
(`src/futures_bot/context/{models,context_engine,__init__}.py`) —
a typed, immutable `MarketContext` value object (session/regime/
volatility/trend/liquidity/risk state, each an Enum with an `UNKNOWN`
fallback so missing values are always safely representable, plus a
`confidence_scores` dict and `to_dict`/`from_dict` serialization) and
a `ContextEngine` whose `build_context()` wires it together with six
classification methods, all deliberately stubbed to return `UNKNOWN`
(no indicator math this phase, per the task's own scope). Purely
additive: nothing in `engine.py`/`strategy/`/`risk/` imports or
references it yet, verified both by dedicated tests (`context` not in
`TradingEngine`'s namespace, `Strategy.on_bar`'s signature unchanged)
and by the full suite staying green (949 passed, 20 new). Found
`research/regime.py` already implements very similar
session/trend/volatility classification (post-trade, for analytics,
not real-time) during inspection — documented as the reuse point for
whichever future phase implements real classification, specifically to
avoid building a second, duplicate system. Integration point and
target layering (Market Data → Context Engine → Strategy Engine → Risk
Engine → Execution) documented in `docs/ARCHITECTURE.md`.

2026-07-27: built a repeatable one-command startup system
(`scripts/{_common,start,stop,restart,status}.ps1`, `start.cmd`) —
verifies repo/venv, runs `pip install -e .`/`npm install`
unconditionally every boot (always current), checks `market_data.db`,
frees ports 8000/5173 of stale processes by-port (authoritative;
verified more precise than PID tracking), starts backend+frontend,
waits for both to actually respond, opens the browser, prints a green
summary. Found and worked around a genuine pre-existing bug along the
way: `npm run dev`'s `kill-vite.js` kills its own node.exe process, so
`start.ps1` calls `vite.cmd` directly (with `--host 127.0.0.1`, since
Vite otherwise binds the IPv6 loopack `localhost` resolves to on this
machine) instead — see Frontend Status and KNOWN_ISSUES.md. Verified
end-to-end: fresh boot, status check, mid-run backend kill, restart,
clean stop, missing-venv hard-failure, missing-database
degraded-success path.

2026-07-27: built a permanent, read-only database validator
(`src/futures_bot/market_data/validation.py`, wired into
`python -m futures_bot.cli --validate-db`) covering corrupted symbols,
duplicate rows, missing/malformed timestamps, OHLC invariant
violations, negative/zero volume, contract-roll chain consistency,
session gaps (reusing the sync engine's existing bookkeeping), a
missing-trading-days heuristic, orphan metadata records, and schema
drift (diffed directly against `store.py`'s `_SCHEMA`). 33 new tests.
Running it against the live database for the first time surfaced two
new, previously-unknown issues (ISSUE-004, ISSUE-005) — logged, not
fixed, per this task's "do not modify existing data" constraint.
`docs/DATABASE_VALIDATION.md` written; `BOOT_CHECKLIST.md` updated
with the new step.

2026-07-26: dependency audit/fix, clean-venv verification, git history
cleanup (6 commits turning ~121 untracked files into a real history),
persistent documentation framework created (`CLAUDE.md`/
`PROJECT_STATE.md`/`CHANGELOG.md`/`KNOWN_ISSUES.md`/`ROADMAP.md`/
`BOOT_CHECKLIST.md`), and the `market_data.db` turtle-data corruption
(KNOWN_ISSUES.md ISSUE-001) diagnosed and repaired — including a
near-miss during the repair (a proposed fix collided contract data and
dropped ~90% of it on reimport; caught immediately, rolled back from a
verified backup, corrected) documented in full in
docs/DATABASE_CORRUPTION_REPORT.md. See CHANGELOG.md for the complete
breakdown.

## Recommended Next Task

The Market Context Engine is now complete and validated as an
independent subsystem (Phase 8, 2026-07-27) — every dimension real,
internally validated, look-ahead-audited, benchmarked, and documented.
The next decision is **integration**: how `TradingEngine.on_bar` should
actually pass a `MarketContext` to strategies (likely a
`Strategy.on_bar` signature change), and whether/how `EnvironmentScore`
should influence a strategy's own decisions — **needs explicit approval
per CLAUDE.md section 8** (the strategy interface is a protected
surface); see `docs/ARCHITECTURE.md`'s "Market Context Engine" section
("The exact integration point") and
`docs/CONTEXT_ENGINE_ARCHITECTURE_REVIEW.md` for the current,
purely-observational state to preserve until that approval is given.
Otherwise: decide whether to fix `kill-vite.js`'s self-kill bug directly (would
fix manual `npm run dev` too, but is a change to existing frontend
code) or leave `scripts\start.ps1`'s workaround as the standing
solution. Also open: ISSUE-004 (schema migration, needs explicit
approval per CLAUDE.md section 8) and ISSUE-005 (US80Z source-data
correction). Otherwise: CI setup (tests currently only run by hand), a
Python formatter/linter, or the High-priority roadmap items
(walk-forward testing, Monte Carlo, parameter robustness).
