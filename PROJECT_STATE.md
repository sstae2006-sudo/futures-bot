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
- Test suite: 881 tests, 880 passing + 1 known test-order-dependent
  flake (see KNOWN_ISSUES.md). Requires the `ml` extra
  (`pip install -e ".[dev,ml]"`) for the ML dataset/training/predict
  test modules to even collect — without it they fail collection with
  `ModuleNotFoundError`, not a real failure.
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

- **Data integrity bug, unresolved:** 1980s–90s historical import
  populated the contract/ticker column with date values instead of
  contract symbols in `market_data.db` (see KNOWN_ISSUES.md). Not yet
  diagnosed or fixed.
- No CI configured — tests only run when a session runs them by hand.
- No Python formatter/linter configured (no ruff/black/mypy).

## Priorities

See ROADMAP.md.

## Last Completed Work

2026-07-26: dependency audit/fix, clean-venv verification, full test
suite run, git history cleanup (6 commits turning ~121 untracked files
into a real history), `CLAUDE.md`/`PROJECT_STATE.md`/`CHANGELOG.md`/
`KNOWN_ISSUES.md`/`ROADMAP.md`/`BOOT_CHECKLIST.md` created. See
CHANGELOG.md for the full breakdown.

## Recommended Next Task

Get explicit go-ahead to diagnose and fix the `market_data.db`
contract-symbol corruption (KNOWN_ISSUES.md, Critical). Back up and
verify integrity before any write — the DB is ~1 GB+ and no fresh
backup has been taken since the corruption was reported.
