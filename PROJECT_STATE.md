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
- Test suite: 896 tests, all passing as of 2026-07-26 (881 + 15 new
  turtle-import regression tests). One test
  (KNOWN_ISSUES.md ISSUE-002) is a known test-order-dependent flake —
  didn't reproduce in the latest full run, but treat an isolated
  failure there as the known flake, not a new regression, until it's
  root-caused. Requires the `ml` extra (`pip install -e ".[dev,ml]"`)
  for the ML dataset/training/predict test modules to even collect —
  without it they fail collection with `ModuleNotFoundError`, not a
  real failure.
- Python: 3.12.10. Project `.venv` at repo root already has `dev`+`ml`
  extras installed.

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

## Priorities

See ROADMAP.md.

## Last Completed Work

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

No Critical items remain open in ROADMAP.md as of this writing.
Consider CI setup (tests currently only run by hand) and a Python
formatter/linter, or move to the High-priority roadmap items
(walk-forward testing, Monte Carlo, parameter robustness).
