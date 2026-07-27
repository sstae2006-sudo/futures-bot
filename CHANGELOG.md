# CHANGELOG.md

Every session appends an entry here. Don't edit past entries except to
mark something resolved with a date/commit — this is a history, not a
scratchpad.

## 2026-07-27 — Market Context Engine: Volatility Context (Phase 2b)

**Added**
- `src/futures_bot/context/volatility.py`: `analyze_volatility(timestamp,
  symbol, timeframe, bars, atr_period=14, average_lookback=20)` and
  `VolatilityContext` (`current_atr`, `average_atr`, `volatility_ratio`,
  `realized_volatility`, `state`). Reuses
  `strategy.indicators.atr_series` for ATR (no true-range math
  re-derived); `average_atr` is the mean of a trailing window of ATR
  values ending at the last bar given; `volatility_ratio =
  current_atr / average_atr` classified into `VolatilityState`
  (`LOW`/`NORMAL`/`HIGH`/`EXTREME`/`UNKNOWN`, unchanged since Phase 1)
  via fixed, documented thresholds (`<0.75`/`[0.75,1.25)`/
  `[1.25,2.0)`/`>=2.0` — matches the task's own worked example, ratio
  1.5 → HIGH). `realized_volatility` (stdev of simple close-to-close
  returns over the same trailing window, unannualized) is newly
  implemented — no existing equivalent. `classify_volatility_ratio`
  exported standalone for direct threshold testing. `to_dict`/`from_dict`.
- `tests/test_context_volatility.py` (22 tests): low-volatility period,
  high-volatility period (including an exact match against the task's
  worked example shape), an extreme spike, missing/insufficient data
  (no bars, fewer bars than `atr_period`, a single bar), a dedicated
  no-future-leakage class (`TestNoFutureDataLeakage` — proves a
  truncated-history reading is unaffected by bars appended after it,
  and that the trailing average doesn't get pulled toward an
  unrelated, much-wider earlier regime), serialization, and
  integration into `MarketContext` across multiple symbols/timeframes.

**Changed**
- `src/futures_bot/context/models.py`: added
  `MarketContext.volatility_context: Optional[VolatilityContext]`
  (`TYPE_CHECKING`-guarded import, same pattern as `session_context`);
  `to_dict`/`from_dict` updated to serialize it.
- `src/futures_bot/context/context_engine.py`: `_classify_volatility`
  is now real (delegates to `volatility.analyze_volatility`);
  `build_context` adds `confidence_scores["volatility"] = 1.0` only
  once `analyze_volatility` actually produced a non-`UNKNOWN` reading
  (i.e. enough history existed) — an `UNKNOWN` reading stays out of
  `confidence_scores` entirely, same contract the four still-stubbed
  dimensions already follow. The other four `_classify_*` methods are
  unchanged stubs.
- `docs/ARCHITECTURE.md`: "Market Context Engine" section updated —
  new "Volatility Context" subsection explaining what's reused
  (`atr_series`), what's new (`realized_volatility`), and specifically
  why `research/regime.py`'s `classify_volatility` tercile approach
  was *not* reused as-is (whole-series `sorted()` cutoffs aren't
  look-ahead-safe for real-time classification).

**Database changes**
- None.

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None. `volatility_context` is purely additive (defaults to `None`);
  `VolatilityState` itself is unchanged from Phase 1.

**Verified:** full suite green (1002 passed, 0 failed — 980 + 22 new).
`git status`/`git diff` confirm zero changes to `strategy/`,
`engine.py`, `risk/`, `brokers/`, `backtest/`, `research/regime.py` —
only `context/`, its new test file, and docs changed. Manually verified
the task's own worked example shape (`current_atr=18, average_atr=12,
volatility_ratio=1.5 → HIGH`) and the no-look-ahead property via
`TestNoFutureDataLeakage` before trusting it as a passing test.

**Commit hashes**
- Not yet committed as of this entry.

## 2026-07-27 — Market Context Engine: Session Context (Phase 2a)

**Added**
- `src/futures_bot/context/session.py`: `classify_session(timestamp,
  symbol, premarket_start_ct=...)` and `SessionContext`
  (`session`, `minutes_since_open`, `liquidity_expectation`,
  `is_market_open`). Classifies the seven futures-market session
  phases by reusing `contracts.py`'s existing CME calendar logic
  (`is_weekend_closure`, `is_cme_holiday`, `in_maintenance_halt`,
  `is_market_open`) and `research/regime.py`'s exact RTH boundaries —
  no new calendar built. `to_dict`/`from_dict`.
- `tests/test_context_session.py` (31 tests): normal trading day
  (including an exact match against the task's own spec example),
  weekend, holiday, overnight, the maintenance halt, market-open
  transitions, serialization, and integration into `MarketContext`.
  Named `test_context_session.py`, not `test_session.py` — that name
  was already taken by `futures_bot.session`'s unrelated tests
  (session-summary reporting).

**Changed**
- `src/futures_bot/context/models.py`: `SessionPhase` enum members
  renamed to the precise 7-phase spec (`OVERNIGHT`, `PRE_MARKET`,
  `OPENING_RANGE`, `MORNING_SESSION`, `LUNCH_SESSION`, `POWER_HOUR`,
  `MARKET_CLOSE` — was `OPENING_RANGE`/`MORNING`/`MIDDAY`/`AFTERNOON`/
  `CLOSE`/`OVERNIGHT`; confirmed unused anywhere before renaming).
  Added `MarketContext.session_context: Optional[SessionContext]`
  (`TYPE_CHECKING`-guarded import to avoid a circular dependency with
  `session.py`, which imports `SessionPhase` from this module);
  `to_dict`/`from_dict` updated to serialize it.
- `src/futures_bot/context/context_engine.py`: `_classify_session` is
  now real (delegates to `session.classify_session`); `build_context`
  sets `confidence_scores={"session": 1.0}` (deterministic
  classification, not a guess) while the other five dimensions remain
  unscored. The other five `_classify_*` methods are unchanged stubs.
- `tests/test_context.py`: one Phase-1 assertion
  (`test_context_engine_with_no_bars_returns_all_unknown`) updated —
  it asserted `confidence == 0.0` when *everything* was a stub; now
  that session classification is real and doesn't need bars, that's
  no longer true. Renamed and narrowed to check only the five
  dimensions still actually unimplemented.
- `docs/ARCHITECTURE.md`: "Market Context Engine" section updated —
  session boundary rationale, the bug found and fixed (see below), and
  the corrected "six stubs" → "five stubs, one real" description.

**Fixed (in new code from this same phase, not a regression)**
- A bug in this phase's own first draft: `minutes_since_open` was
  wrong throughout the entire 16:00–17:00 CT maintenance halt (e.g.
  reporting 0 at 16:30 instead of 30), because the original
  implementation measured elapsed time from
  `contracts.session_date()`'s session-start attribution, which
  assigns a halt moment to the *next* session (correct for
  `session_date`'s own kill-switch purpose, wrong for this
  calculation). Found via manual verification against the live module
  before writing it down as a test assertion, not by trusting the
  first implementation. Fixed with a self-contained "most recent
  17:00 CT at or before this moment" formula; regression-tested
  explicitly across the whole halt window.

**Database changes**
- None.

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None to any existing caller (nothing outside `context/` references
  `SessionPhase`'s renamed members — confirmed via `grep` before
  renaming). Within `context/`, `MarketContext.session` keeps working
  as a bare enum for callers that don't need the richer detail;
  `session_context` is purely additive (defaults to `None`).

**Verified:** full suite green (980 passed, 0 failed — 949 + 31 new).
Re-confirmed the architecture-review checks from the prior entry still
hold: zero references to `futures_bot.context` outside itself, zero
`git diff`/`git status` on `strategy/`, `engine.py`, `risk/`,
`brokers/`, `backtest/`, `research/regime.py` (untouched), both import
orders succeed with no cycle.

**Commit hashes**
- `b9b2cc3`.

---

## 2026-07-27 — Market Context Engine foundation

**Added**
- `src/futures_bot/context/models.py`: `MarketContext` — a typed,
  immutable value object (`timestamp`, `symbol`, `timeframe`, `session`,
  `market_regime`, `volatility_state`, `trend_state`, `liquidity_state`,
  `risk_state`, `confidence_scores`). Six state Enums
  (`SessionPhase`/`MarketRegime`/`VolatilityState`/`TrendState`/
  `LiquidityState`/`RiskState`), each with an `UNKNOWN` member so a
  context is always safely constructible with nothing known yet.
  `confidence` property (mean of `confidence_scores`, 0.0 if empty),
  `to_dict`/`from_dict` (JSON-safe, round-trips), `unknown_context()`
  helper.
- `src/futures_bot/context/context_engine.py`: `ContextEngine` —
  `build_context(timestamp, bars)` wires the above together; its six
  `_classify_*` methods are stubs returning `UNKNOWN` (no indicator
  math this phase, by design).
- `src/futures_bot/context/__init__.py`: package exports.
- `tests/test_context.py` (20 tests): construction, missing-values
  safety, serialization round-trips, and explicit guards that nothing
  in the existing decision path (`engine.py`, `strategy/base.py`)
  imports or references the new module.
- `docs/ARCHITECTURE.md`: new "Market Context Engine" section — target
  layering, the exact integration point
  (`engine.TradingEngine.on_bar`, between `risk.must_flatten` and
  `strategy.on_bar`, shared by live and backtest since both run through
  the same engine), and the reuse point with `research/regime.py` /
  `strategy/indicators.py` for whichever future phase implements real
  classification.

**Changed**
- Nothing existing. This module is imported by nothing else in the
  codebase yet — purely additive.

**Fixed**
- None.

**Database changes**
- None (explicitly out of scope this phase — would need approval per
  CLAUDE.md section 8).

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None.

**Verified:** full test suite green (949 passed, 0 failed — 929 +
20 new), including dedicated checks that `Strategy.on_bar`'s signature
is unchanged and that `TradingEngine` has no `context` reference.

**Post-hoc architecture review (same day):** re-verified against the
live code (not just the tests written alongside it) — zero references
to `futures_bot.context` anywhere outside itself; `strategy/`,
`engine.py`, `risk/`, `brokers/`, `backtest/`, `research/regime.py` all
show zero `git diff`/`git status` (untouched, not just unaffected);
both import orders (`context` before and after `engine`/`strategy`)
succeed with no cycle, and `context_engine.py`'s only in-repo
dependency (`futures_bot/models.py`) imports nothing from `context`, so
no cycle is structurally possible; every no-data code path
(`bars=None`, `bars=[]`, omitted, bare construction) exercised directly
and confirmed all-`UNKNOWN`/0.0 confidence, never an error.

**Notable finding during inspection (informed the design, not a bug):**
`research/regime.py` already implements session/trend/volatility
classification, applied post-trade for analytics
(`GET /api/regime/performance`), not in the live decision path. Rather
than risk a second, duplicate classification system, the new
`ContextEngine`'s stub methods explicitly document reusing those
functions (and `strategy/indicators.py`'s `atr`/`adx`/`ema_series`) as
the intended Phase 2 implementation, instead of re-deriving the same
thresholds from scratch.

**Commit hashes**
- Not yet committed as of this entry.

---

## 2026-07-27 — repeatable one-command startup system

**Added**
- `scripts/_common.ps1`: shared helpers — `Assert-RepoRoot`,
  `Assert-Venv`, `Write-Step`/`Write-Ok`/`Write-WarnLine`/
  `Write-Failure`, by-port process lookup/kill/wait
  (`Get-ProcessIdsOnPort`, `Stop-ProcessOnPort`, `Wait-ForPortFree`),
  HTTP wait/single-shot-check (`Wait-ForHttp`, `Test-HttpOk`), PID-file
  helpers (informational only, never used to decide what to kill).
- `scripts/start.ps1`: the one command — verifies repo/venv, runs
  `pip install -e .`/`npm install` unconditionally every boot (so
  dependencies are always current, not just "present"), checks
  `market_data.db` (warns, doesn't block), frees ports 8000/5173 of
  any stale processes, starts backend + frontend, waits for both to
  actually respond, opens the browser, prints a green summary.
- `scripts/stop.ps1`, `scripts/restart.ps1`, `scripts/status.ps1`
  (read-only — never starts/stops anything).
- `start.cmd` at repo root — double-click launcher (Windows doesn't
  run `.ps1` on double-click by default); pauses on failure so the
  console doesn't flash-close before the error is readable.

**Changed**
- `.gitignore`: added `.startup/` (PID files + redirected
  backend/frontend logs — machine-specific runtime state).
- `CLAUDE.md`: section 9 now names `scripts\start.ps1` as the
  recommended startup path (manual commands kept, documented as
  what's underneath / for isolated debugging); added a `scripts/` row
  to the section 7 File Ownership table.
- `BOOT_CHECKLIST.md`: section 4 now covers `scripts\start.ps1`;
  the old manual backend+frontend commands moved to section 5
  ("fallback — isolated debugging"), corrected to use `npx vite
  --host 127.0.0.1` instead of `npm run dev` (see Fixed below).

**Fixed**
- Nothing in existing project code (constraint: don't modify existing
  functionality). `scripts/start.ps1` *works around* two real,
  pre-existing frontend bugs discovered during verification rather
  than patching them — see KNOWN_ISSUES.md ISSUE-006 (`kill-vite.js`
  kills its own node.exe process, so `npm run dev` can never reach
  `vite`) and ISSUE-007 (Vite binds the IPv6 loopback by default, not
  `127.0.0.1`). `start.ps1` calls `frontend/node_modules/.bin/vite.cmd`
  directly with `--host 127.0.0.1`, sidestepping both without touching
  `kill-vite.js`, `package.json`, or `vite.config.ts`.

**Database changes**
- None.

**API changes**
- None.

**Frontend changes**
- None to source — see Fixed above for the two bugs found (not
  patched) and how `start.ps1` works around them.

**Breaking changes**
- None. `scripts\start.ps1` is new, additive orchestration; the manual
  two-command startup still works (with the `npx vite --host
  127.0.0.1` correction noted above).

**Verified end-to-end this session:** fresh boot from an unrelated
directory (proves location-independence) including killing a real
stale backend process on port 8000 first; `status.ps1` correctly
distinguishing "running" from "reachable"; a mid-run manual backend
kill correctly detected (backend down, frontend still up);
`restart.ps1` (stop → fresh start, new PIDs); `stop.ps1` (both ports
freed); missing `.venv` → immediate, specific failure message, nothing
proceeds; missing `market_data.db` → yellow warning, still a
successful (green) boot. Also confirmed a subtle side effect: booting
with `market_data.db` absent auto-creates a new empty one (pre-existing
SQLite/`ensure_schema()` behavior, not introduced by this work) — the
temporary empty file created during this test was verified empty (0
rows) before being removed so the real 927,682,560-byte database could
be restored; integrity check and exact row count (3,518,488)
reconfirmed clean afterward.

**Commit hashes**
- Not yet committed as of this entry.

---

## 2026-07-27 — permanent database validator

**Added**
- `src/futures_bot/market_data/validation.py`: read-only data-integrity
  validator (`validate_database`, `render_report`, CLI `main`). Checks:
  corrupted contract symbols, duplicate rows, missing/malformed
  timestamps, implausible years, invalid/non-numeric OHLC values, all
  four OHLC relationship invariants, negative/zero volume,
  `contract_rolls` chain consistency, session gaps (reusing the sync
  engine's existing `gaps` bookkeeping), a missing-trading-days
  heuristic, orphan metadata records, and schema drift (diffed
  directly against `store.py`'s `_SCHEMA`).
- `--validate-db` CLI flag (`futures_bot.cli`).
- `tests/test_market_data_validation.py` (33 tests).
- `docs/DATABASE_VALIDATION.md`.

**Changed**
- `tools/import_turtle_data.py`: `parse_ticker` now imports
  `is_valid_historical_ticker` from `market_data.validation` instead of
  maintaining its own copy of the ticker regex — one definition, not
  two.
- `BOOT_CHECKLIST.md`: added a validation step (section 7).

**Fixed**
- None (this session builds detection tooling; two issues it found
  were logged, not fixed — see Database changes below).

**Database changes**
- None. Read-only throughout — every connection this validator opens
  uses SQLite's `mode=ro` URI, which raises on any write attempt.
  Verified with a dedicated test that the database file's bytes are
  identical before and after a full `validate_database` run.

**API changes**
- New CLI flag only (`--validate-db`); no existing route/flag changed.

**Frontend changes**
- None.

**Breaking changes**
- None.

**Known issues discovered (logged, not fixed):**
- KNOWN_ISSUES.md ISSUE-004: `bars`' live schema has drifted from
  `store.py`'s current `_SCHEMA` (missing `NOT NULL`, `id` never became
  the declared `PRIMARY KEY AUTOINCREMENT`, `created_at` has no
  default) — pre-existing, not caused by anything in this session.
- KNOWN_ISSUES.md ISSUE-005: `US80Z` (a 1980 Treasury Bond contract)
  has 13 bars with genuine OHLC invariant violations, confirmed present
  in the raw `turtle_raw/US80Z.txt` source file itself.

**Commit hashes**
- Not yet committed as of this entry.

---

## 2026-07-26

**Added**
- `tzdata` as a base dependency (`pyproject.toml`).
- `CLAUDE.md`, `PROJECT_STATE.md`, `CHANGELOG.md`, `KNOWN_ISSUES.md`,
  `ROADMAP.md`, `BOOT_CHECKLIST.md`.

**Changed**
- `pyproject.toml`: moved `fastapi`, `uvicorn[standard]`,
  `python-multipart`, `openpyxl` from the optional `api` extra into
  base `dependencies`; removed the now-redundant `api` extra; added
  `joblib` to the `ml` extra.
- `.gitignore`: added `market_data*.db`, `data/`, `turtle_raw/`,
  `turtle_converted/`, `reports/`, root-level scratch CSVs,
  `research.db-shm`/`research.db-wal`, and the three local-only
  scratch scripts.
- Deploy/docs updated to drop the now-obsolete `pip install -e ".[api]"`
  install instructions (`README.md`, `docs/RESEARCH_INTERFACE.md`,
  `docs/USER_MANUAL.md`, `deploy/DEPLOYMENT.md`); removed the
  bolted-on `RUN pip install tzdata` step from both Dockerfiles now
  that it's a real dependency.
- Relocated `optimize.py`, `fetch_mes_data.py`,
  `pull_massive_flatfiles.py`, `convert_data.py`,
  `convert_turtle_data.py`, `import_turtle_data.py` from the repo root
  into `tools/`; updated `USER_MANUAL.md`'s `python optimize.py ...`
  example to `python tools/optimize.py ...` to match.

**Fixed**
- `python -m futures_bot.api` no longer fails with
  `ModuleNotFoundError` on a bare `pip install -e .` (missing
  fastapi/uvicorn/etc.) or `ZoneInfoNotFoundError` on Windows/slim
  Linux (missing `tzdata`).

**Removed**
- `src/futures_bot.egg-info/*` and `__pycache__/*.pyc` under `src`/
  `tests`, wrongly tracked since the original commit — untracked via
  `git rm --cached` (files still exist locally, just no longer in git).

**Database changes**
- None.

**API changes**
- None (dependency/packaging only).

**Frontend changes**
- None functionally; `frontend/` committed to git for the first time
  in this session's cleanup (source only, `node_modules`/`dist`
  gitignored).

**Breaking changes**
- `pip install -e ".[api]"` no longer works as an install command
  (the `api` extra was removed) — use plain `pip install -e .`. All
  in-repo docs/deploy configs referencing it were updated.

**Commit hashes**
- `8946de8` — pyproject.toml dependency fix
- `647c3ea` — housekeeping: gitignore + untrack build artifacts
- `ff9138b` — core feature build (backtest/market-data/research/live)
- `f861363` — frontend dashboard
- `214a961` — deploy configs + docs
- `3c17e11` — tools/ + relocated scripts
- `514dd1c` — persistent documentation framework (`CLAUDE.md` rewrite,
  `PROJECT_STATE.md`/`CHANGELOG.md`/`KNOWN_ISSUES.md`/`ROADMAP.md`/
  `BOOT_CHECKLIST.md` added, `.gitignore` WAL/SHM fix)

---

## 2026-07-26 (continued) — market_data.db turtle-data repair

**Added**
- `tests/test_tools_turtle_import.py` (15 tests): century-pivot date
  parsing, contract-symbol validation, and a direct regression test
  for the product_code-collision failure mode discovered mid-repair
  (see below).
- `tools/repair_turtle_corruption_2026_07_26.py`: the one-time delete
  step, kept on disk for auditability.
- `docs/DATABASE_CORRUPTION_REPORT.md`.

**Changed**
- `tools/convert_turtle_data.py`: `parse_date` now uses a fixed
  50-year pivot instead of Python's `%y` default.
- `tools/import_turtle_data.py`: added `parse_ticker` (validates the
  contract-symbol pattern, rejects malformed filenames); `contract` is
  now set to the validated ticker instead of a hardcoded
  `"CONTINUOUS"` placeholder. `product_code` is unchanged (still the
  full ticker) — see Known Issues / the report for why that's correct
  here, not a bug.
- `.gitignore`: no change needed — `market_data*.db` already covers
  the backup file created during this repair.

**Fixed**
- `market_data.db`: 17,668 Copper (`HG`) bars from 1959–1968 that were
  stored with a timestamp 100 years in the future (e.g. 2064 instead of
  1964) now resolve to the correct year.
- `market_data.db`: all 342,494 turtle-sourced rows' `contract` column
  now holds the real per-contract ticker instead of the placeholder
  `"CONTINUOUS"`.

**Removed**
- Nothing removed from `bars` net of the repair — 342,494 rows were
  deleted and 342,494 were re-imported (row count unchanged; see
  Database changes below for the near-miss where this briefly wasn't
  true).

**Database changes**
- `market_data.db`, table `bars`: deleted and re-imported all
  `source='turtletrader'` rows (342,494). **Near-miss:** the first
  reimport attempt used a generic root as `product_code` instead of
  the full ticker, which collided every overlapping trading day across
  contract-months on the schema's `(product_code, resolution,
  timestamp)` uniqueness index and silently dropped ~90% of the data
  (342,494 → 34,331 rows). Caught immediately by comparing row counts;
  the live database was restored from a verified backup
  (`market_data.backup_20260726_221257.db`) before any further action,
  and the fix was corrected to keep `product_code` as the full ticker.
  No data was permanently lost. One source file
  (`turtle_raw/GC001F.txt`) uses a different schema and a malformed
  filename; correctly rejected by the new validation, left unfixed.

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None.

**Commit hashes**
- Not yet committed as of this entry — pending user confirmation
  (constraint: never commit `market_data*.db` or any backup; stage the
  importer/test/doc changes explicitly by path).

---

## Historical note (pre-2026-07-26)

Commit `8e55490` ("Phase 1 framework") was the only commit in the repo
before this session. Everything reflected in commits `647c3ea` through
`3c17e11` above was already-written, long-uncommitted working-tree
content (the full backtest/research/ML/frontend/deploy build) that
this session organized into a real git history — it was not written in
this session. Treat those six commits as a one-time historical
reconstruction, not a record of when those features were actually
built.
