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
- Test suite: 980 tests as of 2026-07-27 (949 + 31 new Session Context
  tests), full suite green (980 passed, 0 failed). One test
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
- Market Context Engine **in progress** (2026-07-27,
  `src/futures_bot/context/`): typed `MarketContext` value object +
  `ContextEngine`. **Session classification is real**
  (`session.py`'s `classify_session`, using `contracts.py`'s existing
  CME calendar logic, wired through `ContextEngine`/`MarketContext`);
  the other five classification dimensions are still stubs returning
  `UNKNOWN`. Not wired into `TradingEngine`/`Strategy` yet. See
  `docs/ARCHITECTURE.md`'s "Market Context Engine" section and
  ROADMAP.md for the follow-up phases.
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

Decide the next Market Context Engine phase: implement real
`_classify_regime`/`_classify_volatility`/`_classify_trend` by reusing
`research/regime.py`/`strategy/indicators.py` (see
`docs/ARCHITECTURE.md`), and decide how `TradingEngine.on_bar` should
actually pass a `MarketContext` to strategies (likely a `Strategy.on_bar`
signature change — needs explicit approval per CLAUDE.md section 8).
Otherwise: decide whether to fix `kill-vite.js`'s self-kill bug directly (would
fix manual `npm run dev` too, but is a change to existing frontend
code) or leave `scripts\start.ps1`'s workaround as the standing
solution. Also open: ISSUE-004 (schema migration, needs explicit
approval per CLAUDE.md section 8) and ISSUE-005 (US80Z source-data
correction). Otherwise: CI setup (tests currently only run by hand), a
Python formatter/linter, or the High-priority roadmap items
(walk-forward testing, Monte Carlo, parameter robustness).
