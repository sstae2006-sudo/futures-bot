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
- Test suite: 929 tests as of 2026-07-27 (896 + 33 new database-
  validator tests). One test (KNOWN_ISSUES.md ISSUE-002) is a known
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
- `npm install` + `npm run dev` confirmed to have `node_modules`
  present; full `npm run dev`/`npm test` run not re-verified this
  session (no frontend code changed).
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

Decide whether to fix ISSUE-004 (schema migration, needs explicit
approval per CLAUDE.md section 8) and/or ISSUE-005 (US80Z source-data
correction). Otherwise: CI setup (tests currently only run by hand), a
Python formatter/linter, or the High-priority roadmap items
(walk-forward testing, Monte Carlo, parameter robustness).
