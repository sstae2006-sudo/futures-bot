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
- **Phase 2c — regime/trend.** Implement
  `_classify_regime`/`_classify_trend` by reusing `research/regime.py`
  (`classify_trend`, currently applied post-trade for analytics) and
  `strategy/indicators.py` (`adx`, `ema_series`) — not by re-deriving
  the same math a second time.
- **Phase 3 — new dimensions.** `_classify_liquidity`/`_classify_risk`
  have no existing equivalent to reuse; genuinely new work.
- **Phase 4 — wire it in.** Decide how `TradingEngine.on_bar` actually
  gets a `MarketContext` to a strategy — most likely a `Strategy.on_bar`
  signature change. **Needs explicit approval per CLAUDE.md section 8**
  (protected: the strategy interface).
- **Phase 5 — persistence (maybe).** Whether `MarketContext` snapshots
  get stored for research/backtesting analysis. Would be a database
  schema change — needs explicit approval per CLAUDE.md section 8 —
  and isn't decided yet; don't assume it's wanted.

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
- Market Context Engine, Phases 1, 2a, and 2b (foundation + Session
  Context + Volatility Context) (2026-07-27) — see "Market Context
  Engine (phased)" above for what's left.
