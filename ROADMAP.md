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
- Decide a rotation/archival policy for `logs/decisions.jsonl` — currently
  unbounded (observed at 9.2 GB / 34.2M lines on this machine, KNOWN_ISSUES.md
  ISSUE-017). Reading it is now cheap regardless of size, but nothing stops
  it growing until it threatens disk space on a long-running deployment.

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
- **Phase 4 — wire it in (done, 2026-07-27).** `TradingEngine` gained
  `engine.ContextMode` (OFF/OBSERVE/ENABLED — OFF is the default for
  every existing caller, a complete no-op). `Strategy.on_bar`'s call
  signature is unchanged; context reaches a strategy only via a new,
  optional `self.context` attribute, set only in `ENABLED` mode for a
  strategy that opts in via a new `uses_context` class attribute
  (defaults `False` for every bundled strategy). `Trade` gained an
  optional `entry_context` field, attached in `TradingEngine._record_trade`
  (the single shared closing path for every trade). Same execution
  path for backtesting/paper/live — no duplicate pipeline. See Phase 9
  below for the full scope this landed alongside.
- **Phase 5 — persistence (maybe).** Whether `MarketContext` snapshots
  get stored for research/backtesting analysis. Would be a database
  schema change — needs explicit approval per CLAUDE.md section 8 —
  and isn't decided yet; don't assume it's wanted.
- **Phase 8 — completion and validation (done, 2026-07-27).** An
  11-part phase making the engine production-ready as an independent
  subsystem before integration: Part 1 (trend/liquidity/risk, see
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
- **Phase 9 — integration and A/B comparison (done, 2026-07-27).** See
  Phase 4 above for the `ContextMode`/`Trade.entry_context`/
  `Strategy.context` mechanics. Also added
  `backtest/context_comparison.py`'s `compare_context_impact` — runs the
  same strategy factory/settings/bars through `OBSERVE` (baseline) and
  `ENABLED` (may differ) via the same `run_backtest`, diffs the trade
  lists (`UNCHANGED`/`REMOVED_BY_CONTEXT`/`ADDED_BY_CONTEXT`/
  `ENTERED_DIFFERENTLY`/`EXITED_DIFFERENTLY`), and attaches the
  `MarketContext`/`EnvironmentScore` explaining each change (metrics
  reused directly from `BacktestMetrics`, nothing recomputed). Two real
  bugs found and fixed during manual verification: `TradingEngine.bars`
  (a bounded `deque`) doesn't support the slice indexing
  `liquidity.py`/`volatility.py` need (fixed: convert to `list` once
  per bar); `dataclasses.replace`'s result (needed to attach
  `entry_context` to a frozen `Trade`) was being discarded instead of
  written back into `PaperBroker.trades` (fixed). Verified directly:
  `OFF` byte-identical to a pre-integration-style backtest; `OBSERVE`
  decision-identical to `OFF`; `ENABLED` decision-identical to
  `OFF`/`OBSERVE` for any non-opted-in strategy. 27 new tests
  (1163 → 1190 total).
- **Platform Verification Phase 1 (done, 2026-07-27).** Independent,
  read-only audit of the Phase 9 integration —
  `docs/PLATFORM_VERIFICATION_PHASE1.md`. All `ContextMode`/execution-
  flow/backward-compatibility/`MarketContext`-consistency checks PASS,
  verified with reproductions and exact-equality regression tests, not
  just re-asserted. Two findings, neither fixed (measure-only scope):
  (1) `cProfile` shows ADX and volatility/ATR are each computed *twice*
  per bar (`regime.py` and `trend.py` both call `adx()`; `context_engine.py`
  and `regime.py` both call `analyze_volatility()`) — ~90% of all
  context-generation CPU time, a high-value future optimization target;
  (2) reusing one `Strategy` instance across two engine runs with
  different `ContextMode`s can leave a stale `self.context` from the
  first run — harmless today (no current caller reuses instances,
  confirmed by inspection) but a real, reproduced gap worth closing
  defensively before any future tooling does. 25 new tests
  (1190 → 1215 total). Confidence level: High.
- **Platform Verification Phase 2 (done, 2026-07-27).** Fixed both
  findings from Phase 1's audit — `docs/PLATFORM_VERIFICATION_PHASE2.md`.
  ADX/volatility dedup: `classify_regime`/`analyze_trend` gained
  sentinel-defaulted `precomputed_*` parameters (every existing caller
  unaffected); `ContextEngine.build_context` computes each once per bar
  and passes them through. Verified byte-identical output directly (not
  just via the passing suite) and `cProfile`-confirmed exactly-once call
  counts (800/800, down from 1,585/1,600 for 800 bars). Stale-context
  fix: `TradingEngine` now resets `Strategy.context` at construction and
  unconditionally every bar. Per-run context-generation cost down
  ~35-46% (converging toward ~45%, matching the ~90%-duplicate-halved
  prediction). 6 new tests (1215 → 1221 total). No remaining blockers
  before the first context-aware strategy is built.

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
- Market Context Engine — **complete and integrated into
  `TradingEngine`** through Phase 9 (foundation +
  Session/Volatility/Regime/Multi-Timeframe/Structure/Trend/Liquidity/
  Risk + configurable Context Scoring + engine validation + look-ahead
  audit + performance benchmark + context analytics + coverage report +
  architecture review + `ContextMode` integration + OFF-vs-ENABLED A/B
  comparison) (2026-07-27) — see "Market Context Engine (phased)"
  above; only Phase 5 (persistence, maybe, needs approval) and deciding
  whether/how any *specific* bundled strategy should adopt
  `uses_context = True` remain.
- **Team deployment (Tailscale + centralized TimescaleDB) — complete and
  verified against a live server** (2026-07-27). Both databases ported
  (`PgMarketDataStore`/`PgTradeStore`, same method surface as the SQLite
  originals, selected transparently via `FUTURES_BOT_DATABASE_URL`),
  Alembic-managed schema (`alembic/`, both databases' 19 tables, `bars` a
  real TimescaleDB hypertable), a verified data-migration script
  (`tools/migrate_to_timescaledb.py`), a backup script
  (`tools/backup_timescaledb.py`), `scripts/start-team.ps1`,
  `GET /api/system/health`, and Mission Control's `StatusBar`/`HealthGrid`
  wired to it. Three real bugs found and fixed during this session's own
  live-server verification (KNOWN_ISSUES.md ISSUE-010/011/012). See
  `PROJECT_STATE.md`'s "Team deployment" write-up and `TEAM_DEPLOYMENT.md`.
- **Real production data migration to TimescaleDB — completed** (2026-07-28).
  The actual `market_data.db`/`research.db` (3.5M `bars` rows, 14.8k
  `trades`, everything else) has been migrated for real, hitting and
  fixing one genuine blocker along the way (KNOWN_ISSUES.md ISSUE-014 —
  `bars.created_at NOT NULL` rejected 32.5% of real rows). See
  `PROJECT_STATE.md`'s "Last Completed Work".
- **Team Mode's core cross-machine networking bug — root-caused and
  fixed** (2026-07-28). Windows Firewall's Private-profile default
  (`BlockInbound`, zero rule for this app/port) silently dropped genuine
  tailnet peer traffic while same-machine tests kept succeeding via local
  delivery. `start-team.ps1` now auto-creates a rule scoped to Tailscale's
  own CGNAT range, self-elevating via UAC if needed. See KNOWN_ISSUES.md
  and `PROJECT_STATE.md`'s "Last Completed Work". **Still needed**: the
  user actually clicking the UAC prompt (or running the one-time elevated
  command) once, and a genuine second-machine Tailscale connection — a
  registered peer exists but was offline this session.
- **Stabilization Mode pass — 10 real bugs found and fixed** (2026-07-28):
  a flaky test, a check-then-set race present in three separate `start()`
  methods, a 9.2 GB unbounded log file being fully read into memory on
  every dashboard request, unhandled exceptions never being logged
  anywhere, a missing lock on a singleton accessor, and a blank frontend
  panel during a session's `starting` state. See KNOWN_ISSUES.md
  ISSUE-014 through ISSUE-020 and `CHANGELOG.md`'s 2026-07-28 entry for
  full detail.
