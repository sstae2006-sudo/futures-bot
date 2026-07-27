# ROADMAP.md

Current priorities and forward-looking plans. Move finished items into
Completed; re-prioritize what's left as it changes. Don't let this
section sit empty and get guessed at — if a priority isn't listed
here, ask before assuming it matters.

## Current Priorities

**Critical**
- Importer reliability (turtle-data corruption in KNOWN_ISSUES.md
  ISSUE-001 resolved 2026-07-26 — see Completed; other import paths
  not separately audited)
- Startup reliability
- Dependency management
- Backend stability

**High**
- Walk-forward testing
- Monte Carlo
- Parameter robustness

**Medium**
- UX polish
- Dark mode
- Exports
- Fix `bars` schema drift (KNOWN_ISSUES.md ISSUE-004, needs explicit
  approval per CLAUDE.md section 8 — it's a schema change)

**Low**
- New strategies
- Fix `US80Z` genuine OHLC violations in raw source data
  (KNOWN_ISSUES.md ISSUE-005)

## Future Roadmap

Not fleshed out beyond the priorities above yet, except the one
concrete phased plan below. Inventing multi-session plans nobody's
actually committed to would be worse than leaving the rest honestly
thin. Add real plans here as they're decided; a future Claude session
should never have to guess what's on this list.

### Market Context Engine (phased)

Target architecture: `Market Data → Context Engine → Strategy Engine →
Risk Engine → Execution`. See `docs/ARCHITECTURE.md`'s "Market Context
Engine" section for the full rationale.

- **Phase 1 — foundation (done, 2026-07-27).** `src/futures_bot/context/`:
  typed, immutable `MarketContext` value object and a `ContextEngine`
  scaffold. Every classification method is a stub returning `UNKNOWN`.
  Not wired into `TradingEngine`/`Strategy` — purely additive, verified
  by dedicated tests and a green full suite.
- **Phase 2a — Session Context (done, 2026-07-27).**
  `context/session.py`'s `classify_session`/`SessionContext` — real
  classification of the seven session phases (`OVERNIGHT`,
  `PRE_MARKET`, `OPENING_RANGE`, `MORNING_SESSION`, `LUNCH_SESSION`,
  `POWER_HOUR`, `MARKET_CLOSE`), reusing `contracts.py`'s existing CME
  calendar logic and `research/regime.py`'s exact RTH boundaries.
  Wired into `MarketContext`/`ContextEngine`. 31 new tests
  (`tests/test_context_session.py`).
- **Phase 2b — Volatility Context (done, 2026-07-27).**
  `context/volatility.py`'s `analyze_volatility`/`VolatilityContext` —
  real ATR-ratio-based classification (`current_atr`/`average_atr`
  from a trailing window, `volatility_ratio`, `VolatilityState`),
  reusing `strategy.indicators.atr_series`. Deliberately did not reuse
  `research/regime.py`'s `classify_volatility` tercile approach as-is —
  its whole-series `sorted()` cutoffs aren't look-ahead-safe for
  real-time use; see `docs/ARCHITECTURE.md`'s "Volatility Context"
  writeup. Wired into `MarketContext`/`ContextEngine`. 22 new tests
  (`tests/test_context_volatility.py`), including a dedicated
  no-future-leakage test.
- **Phase 2c — Market Regime Detection (done, 2026-07-27).**
  `context/regime.py`'s `classify_regime`/`RegimeContext` — classifies
  overall market behavior into `TRENDING_UP`/`TRENDING_DOWN`/`RANGING`/
  `HIGH_VOLATILITY`/`LOW_VOLATILITY` (`MarketRegime`, redefined from
  Phase 1's placeholder set). Combines `strategy.indicators.adx` (trend
  strength), `research/regime.py`'s `classify_trend` (trend direction),
  and `context/volatility.py`'s `analyze_volatility` (volatility
  signal) — no math re-derived. Explicit priority order when signals
  disagree (documented in `docs/ARCHITECTURE.md`); `confidence` via a
  small formula per branch, no parameter optimization this phase.
  Wired into `MarketContext`/`ContextEngine`. 21 new tests
  (`tests/test_context_regime.py`).
- **Phase 2d — Multi-Timeframe Context (done, 2026-07-27).**
  `context/timeframe.py`'s `classify_timeframe_alignment`/
  `TimeframeAlignment` — trend direction across five canonical
  timeframes (`1m`/`5m`/`15m`/`1h`/`1d`), combined into an `alignment`
  dict plus a rank-weighted `alignment_score` (`[0.0, 1.0]`). Reuses
  `research/regime.py`'s `classify_trend` per timeframe. Stricter
  look-ahead handling than any prior phase: tracks each timeframe's
  actual bar duration so an in-progress coarser-timeframe candle can
  never leak in just because its timestamp looks "at or before now".
  Wired into `MarketContext`/`ContextEngine` (new optional
  `bars_by_timeframe` parameter on `build_context`). 14 new tests
  (`tests/test_context_timeframe.py`).
- **Phase 2e — Market Structure Context (done, 2026-07-27).**
  `context/structure.py`'s `analyze_structure`/`StructureContext` —
  confirmed-swing-point structure (higher-highs/higher-lows or
  lower-highs/lower-lows), nearest support/resistance, and distance
  from them. Genuinely new work (no existing equivalent to reuse),
  reusing `TrendState` for its trend vocabulary. Strictly descriptive —
  no broker/risk/engine reference, never generates a trade. Confirmation
  lag (a swing point needs bars after it to confirm) is explicitly
  documented as distinct from a look-ahead violation. Wired into
  `MarketContext`/`ContextEngine`. 17 new tests
  (`tests/test_context_structure.py`).
- **Phase 2f — Context Scoring System (done, 2026-07-27).**
  `context/scoring.py`'s `score_environment`/`EnvironmentScore` —
  combines every existing dimension into a single 0-100 "Market
  Environment Score" (Trend/Volatility/Session/Structure/Liquidity/Risk,
  weights chosen to reproduce the task's own worked example exactly:
  `20+15+10+20+15-10 == 70`), plus a `confidence` (fraction of
  dimensions with real data) and a `reasons` explanation list.
  Information only, never a trading signal — verified by an
  import-boundary test. At the time this phase landed,
  `liquidity_state`/`risk_state` were still stubs (contributing `0.0`
  always) — both are real as of Phase 8 below. Wired into
  `MarketContext` (new `environment_score` field, always populated). 20
  new tests (`tests/test_context_scoring.py`).
- **Phase 3 — trend/liquidity/risk (done, 2026-07-27, as part of
  Phase 8).** `context/trend.py` (`TrendState`, reusing
  `research.regime.classify_trend` + `regime.py`'s ADX confidence
  constants), `context/liquidity.py` (`LiquidityState` from relative
  volume, reusing `strategy.indicators.sma` — genuinely new
  classification logic), `context/risk.py` (`RiskState` as a pure
  composite of `volatility_state`/`market_regime` — no new market-data
  analysis). 50 new tests. See "Phase 8" below for the full scope this
  landed alongside.
- **Phase 4 — wire it in.** Decide how `TradingEngine.on_bar` actually
  gets a `MarketContext` to a strategy — most likely a `Strategy.on_bar`
  signature change. **Needs explicit approval per CLAUDE.md section 8**
  (protected: the strategy interface). **Not started** — Phase 8 was
  explicitly a completion/validation phase for the engine as an
  independent subsystem, not an integration phase.
- **Phase 5 — persistence (maybe).** Whether `MarketContext` snapshots
  get stored for research/backtesting analysis. Would be a database
  schema change — needs explicit approval per CLAUDE.md section 8 —
  and isn't decided yet; don't assume it's wanted.
- **Phase 8 — completion and validation (done, 2026-07-27).** An
  11-part phase making the engine production-ready as an independent
  subsystem before any integration: Part 1 (trend/liquidity/risk, see
  "Phase 3" above), Part 2 (configurable scoring —
  `scoring.ScoringConfig` centralizes all six weights, previously
  hardcoded constants; `ContextEngine.__init__` gained an optional
  `scoring_config` parameter; default config verified to reproduce
  every pre-Phase-8 test exactly), Part 3 (engine validation — no
  circular imports/duplicated logic/duplicated calendars, module
  independence, determinism, missing-data safety, confidence validity,
  all encoded as executable tests in
  `tests/test_context_engine_validation.py`), Part 4 (look-ahead audit
  — `docs/CONTEXT_ENGINE_LOOKAHEAD_AUDIT.md`, no issues found), Part 5
  (performance benchmark — `tools/benchmark_context_engine.py`,
  `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`; found and fixed one
  real inefficiency in `liquidity.py`), Part 6 (`context/analytics.py`'s
  developer/research distribution reports), Part 7
  (`docs/CONTEXT_ENGINE_COVERAGE.md`), Part 8
  (`docs/CONTEXT_ENGINE_ARCHITECTURE_REVIEW.md` — confirmed zero
  changes outside `context/`/tests/docs/tools across this entire
  multi-phase effort). 89 new tests this phase (1074 → 1163 total).
  **Integration into `TradingEngine`/`Strategy` explicitly not started**
  — see Phase 4 above, needs its own approval.

## Completed

Major subsystems already built (see PROJECT_STATE.md "Completed
Features" for detail, CHANGELOG.md for the commits that brought them
into git history):

- Core framework: risk manager, paper broker, session/contract
  handling, decision journal.
- Backtest engine + reports; four reference strategies.
- Market-data sync pipeline (Massive contracts + flat-file APIs).
- Grid-search optimizer with walk-forward validation.
- ML research workstation (dataset/training/prediction).
- FastAPI research server + autonomous paper-trading layer.
- Tradovate live-broker adapter; trade-import/reconciliation pipeline.
- React research dashboard.
- Deploy tooling (Docker, docker-compose, systemd, bare-metal guide).
- Dependency/packaging fix so `pip install -e .` runs the API
  standalone (2026-07-26).
- Git history cleanup: ~121 untracked files organized into 6 real
  commits, `market_data*.db` and other large/local data gitignored
  (2026-07-26).
- Persistent documentation framework (`CLAUDE.md`, `PROJECT_STATE.md`,
  `CHANGELOG.md`, `KNOWN_ISSUES.md`, `ROADMAP.md`, `BOOT_CHECKLIST.md`)
  (2026-07-26).
- Turtle-data corruption in `market_data.db` diagnosed and repaired:
  century-pivot timestamp bug and a hardcoded `contract` placeholder,
  both fixed with regression test coverage (2026-07-26,
  KNOWN_ISSUES.md ISSUE-001).
- Permanent, read-only database validator (`--validate-db`) covering
  16 integrity classes, with 33 tests and `docs/DATABASE_VALIDATION.md`
  (2026-07-27). Surfaced two new findings on first run against the
  live database — see Medium/Low priorities above.
- Repeatable one-command startup system (`scripts\start.ps1` +
  stop/restart/status, `start.cmd`) (2026-07-27).
- Market Context Engine — **complete as an independent subsystem**
  through Phase 8 (foundation + Session/Volatility/Regime/
  Multi-Timeframe/Structure/Trend/Liquidity/Risk + configurable
  Context Scoring + engine validation + look-ahead audit + performance
  benchmark + context analytics + coverage report + architecture
  review) (2026-07-27) — see "Market Context Engine (phased)" above;
  only Phase 4 (wiring into `TradingEngine`/`Strategy`, needs approval)
  and Phase 5 (persistence, maybe) remain.
