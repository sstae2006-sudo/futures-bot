# CLAUDE.md

Read this file completely before making any changes. It is the
authoritative source for architecture, priorities, coding standards,
and project goals. Do not violate any rule below.

Your first responsibility every session is **maintaining project
continuity**, not writing code. Rebuild complete project context
before making a single modification — see section 6.

## 1. Project Identity

**Project Name:** Futures Bot

**Purpose:** Professional futures trading research, backtesting,
optimization, machine learning, paper trading, market data management,
and analytics platform.

**Status:** Commercial-quality software under active development.
Never treat this as a prototype.

## 2. Mission

Build the highest-quality futures trading platform possible.

Every decision should improve:

- reliability
- maintainability
- scalability
- statistical correctness
- user experience

Never optimize for "quick." Always optimize for "correct."

## 3. Non-Negotiable Principles

- Never remove functionality.
- Never simplify working systems.
- Never rewrite large modules unless required.
- Never replace advanced systems with simpler ones.
- Never delete code because it "looks unused."
- Preserve backwards compatibility.
- Every change must improve the software.

## 4. Coding Rules

- Prefer fixing over rewriting.
- Preserve architecture.
- Small commits.
- Document architectural changes.
- No duplicate code.
- Strong typing.
- Clear naming.
- Meaningful logging.
- Graceful error handling.
- No silent failures.
- No hardcoded secrets — broker/vendor credentials (Tradovate,
  `MASSIVE_API_KEY`) are env-var only; `config.yaml` is tracked and
  must never hold literal secrets.
- No Python formatter/linter is configured yet (no ruff/black/mypy in
  `pyproject.toml`). Frontend has `npm run lint` (oxlint) and `npm
  test` (vitest) but no `format` script. Don't invent output from
  tools that aren't wired up — flag the gap instead.

## 5. Project Standards

Always optimize for: maintainability, readability, scalability,
performance, reliability, future development, consistency.

Never optimize for writing the fewest lines of code. Optimize for the
next developer who opens this repository in six months.

## 6. Session Protocol

This project maintains its state in dedicated files instead of chat
history. **No important knowledge should exist only inside a
conversation.** Read all of them at the start of a session, keep them
current at the end of one, and if any is missing, create it. If
information in them conflicts, STOP and explain the conflict instead
of guessing.

| File | Holds |
|---|---|
| `CLAUDE.md` | This file — identity, mission, rules, process. |
| `PROJECT_STATE.md` | Current version, backend/frontend status, completed vs. broken features, last completed work, recommended next task. |
| `CHANGELOG.md` | Per-session record: added/changed/fixed/removed, DB/API/frontend changes, breaking changes, commit hashes. |
| `KNOWN_ISSUES.md` | Every discovered bug — severity, files, possible cause, status. Never delete an entry; mark it Resolved with a date and commit instead. |
| `ROADMAP.md` | Current priorities and forward plans; a Completed section for finished work. |
| `docs/ARCHITECTURE.md` | System-layer diagram, dependency direction, both databases' roles. |
| `BOOT_CHECKLIST.md` | The concrete commands for the verification steps below. |

**Before touching code:**

1. Read everything in the table above.
2. Run `BOOT_CHECKLIST.md` — verify git branch/commits/status, project
   structure, backend startup, frontend startup, Python environment,
   package versions. If the running project doesn't match
   `PROJECT_STATE.md`, stop and explain why — never silently continue.
3. Understand existing code before changing it: identify every file
   involved, its dependencies, affected API routes and frontend pages,
   database usage, imports, and which docs will need updating. Never
   rewrite functionality because you don't understand it yet — read
   first.
4. Search for an existing implementation before writing a new one.
5. Produce an implementation plan.
6. Wait for approval if the change is major (schema, API routes,
   strategy interface, backtest engine — section 8's protected list).

**Making the change:**

- Prefer extending existing systems over rewriting them.
- Avoid duplicate logic; keep naming and architecture consistent.
- Do not introduce technical debt or shortcuts.

**After every change, update:**

- `PROJECT_STATE.md` (version, backend/frontend status, completed/
  broken features, priorities, last completed work, next task).
- `CHANGELOG.md` (new dated entry — added/changed/fixed/removed, DB/
  API/frontend changes, breaking changes, commit hash if committed).
- `KNOWN_ISSUES.md` (add any newly discovered bug; mark fixed ones
  Resolved with date + commit rather than deleting them).
- `ROADMAP.md` (move finished items to Completed; re-prioritize).
- `docs/ARCHITECTURE.md`, if the change is architectural (diagrams,
  startup flow, folder responsibilities, API/DB relationships).
- Run tests, verify startup (backend + frontend), verify the actual
  endpoints/buttons affected — not just that a mock-backed unit test
  is green.

**End of session, report:**

Completed, files modified, new bugs discovered, known blockers,
suggested next task, estimated completion percentage, and the
recommended first command for next session (e.g. `python -m
futures_bot.api`, `npm run dev`).

## 7. File Ownership

| Path | Meaning |
|---|---|
| `frontend/` | React + TypeScript (Vite) research dashboard. Thin — calls the API, no business logic of its own. |
| `src/futures_bot/api/` | FastAPI app + routes (backtests, optimizer, ML, market data, live, research server, trades, reports, imports). |
| `src/futures_bot/backtest/` | Backtesting engine, metrics, HTML/text report generation. |
| `src/futures_bot/research/` | Feature engineering, grid-search optimizer, ML dataset/training/prediction, trade import, trade store. |
| `src/futures_bot/research_server/` | Autonomous layer: paper trader, nightly jobs, orchestrator. Distinct from `research/` — this is the always-on process, not the analysis library. |
| `src/futures_bot/strategy/` | Strategy implementations (`ema_crossover`, `opening_range_breakout`, `vwap_reversion`, `trend_pullback/`). |
| `src/futures_bot/market_data/` | Market-data sync/scheduler/store and the vendor contracts client. |
| `src/futures_bot/brokers/` | Paper broker (used everywhere except live CLI trading) and the Tradovate live broker adapter. |
| `src/futures_bot/risk/` | Risk manager — daily loss kill switch, trade caps, trading-hours filter, force-flat. |
| `src/futures_bot/context/` | Market Context Engine (in progress, 2026-07-27) — `MarketContext` value object, `ContextEngine`, `session.py`'s `SessionContext`/`classify_session`, `volatility.py`'s `VolatilityContext`/`analyze_volatility`, `regime.py`'s `RegimeContext`/`classify_regime`, `timeframe.py`'s `TimeframeAlignment`/`classify_timeframe_alignment`, and `structure.py`'s `StructureContext`/`analyze_structure` (all real; trend/liquidity/risk are still stubs). Not wired into `TradingEngine`/`Strategy` yet. See section 8 and `docs/ARCHITECTURE.md`'s "Market Context Engine" section. |
| `deploy/` | Dockerfiles, docker-compose, systemd units. |
| `docs/` | Architecture, research server, ML workstation, strategy authoring, trade importer, trading workflow, user manual. |
| `tools/` | Data-maintenance/ops scripts (contract building, schema fixes, backup/merge/restore). Not part of the installable package. |
| `scripts/` | `start.ps1`/`stop.ps1`/`restart.ps1`/`status.ps1` — the official one-command boot/shutdown/status system (2026-07-27). `start.cmd` at repo root double-click-launches `start.ps1`. See section 9 and `BOOT_CHECKLIST.md`. |
| `market_data.db` | Historical OHLCV market-data cache. Gitignored, can reach ~1 GB+; regenerate via `tools/pull_massive_flatfiles.py` and related scripts, never commit it or a backup. |
| `research.db` | Backtests/trades/experiments/ML-model research database. Gitignored. |

## 8. Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full
system-layer diagram (frontend → API → core engine → research layer →
persistence) and dependency-direction rules — keep it current when
architecture changes.

**Target layering (in progress):** Market Data → Context Engine →
Strategy Engine → Risk Engine → Execution. `context/` exists but isn't
wired in yet; session, volatility, regime, multi-timeframe-alignment,
and structure classification are real, trend/liquidity/risk are still
stubs — see `docs/ARCHITECTURE.md`'s "Market
Context Engine" section for the exact integration point before
touching `engine.py`, `strategy/`, or `risk/` to wire it in.

**Never change without explicit approval:**

- Database schema (`market_data.db` / `research.db`)
- API routes
- Strategy interface (`strategy/base.py`)
- Backtest engine

## 9. Startup Instructions

**Recommended: `scripts\start.ps1`** (or double-click `start.cmd` at
the repo root) — the official one-command boot. Verifies the repo,
activates the venv, runs `pip install -e .`/`npm install` unconditionally
(always current), checks `market_data.db`, frees ports 8000/5173 of any
stale processes, starts backend + frontend, waits for both to actually
respond, opens the browser, and prints a green summary. Any failure
stops immediately with what failed and how to fix it — see
`BOOT_CHECKLIST.md`. Companions: `scripts\status.ps1` (read-only
check), `scripts\stop.ps1`, `scripts\restart.ps1`.

What it does underneath, useful for backend-only/frontend-only
debugging during development:

- **Backend:** `python -m futures_bot.api`
- **Frontend:** `cd frontend && npx vite --host 127.0.0.1` — **not**
  `npm run dev`: that script's `kill-vite.js` pre-step kills its own
  node.exe process via `taskkill /F /IM node.exe` (image-name matching
  doesn't exclude the caller), so `&& vite` never runs. Confirmed
  empirically 2026-07-27 — see `KNOWN_ISSUES.md`. `scripts\start.ps1`
  already works around this by calling `vite.cmd` directly; do the
  same manually rather than `npm run dev` until that's fixed.

No guessing beyond this — these are the only supported entry points.

## 10. Definition of Done

Claude must satisfy ALL of these before calling work complete:

- Code works
- No console errors, no Python errors, no frontend errors
- No broken endpoints, no missing imports, no broken buttons
- `PROJECT_STATE.md`, `CHANGELOG.md`, `KNOWN_ISSUES.md`, `ROADMAP.md`
  updated (and `docs/ARCHITECTURE.md` if the change was architectural)
- Tests pass
- Startup instructions (section 9) still work

## 11. Golden Rule

This project is intended to become commercial software. Assume every
change may eventually affect paying customers.

- Prioritize correctness over speed.
- Prioritize maintainability over cleverness.
- Do not create technical debt.
- Do not make assumptions.
- When uncertain, investigate rather than guess.

Every session should leave the repository in a state where a
brand-new Claude conversation can recover nearly 100% of project
context simply by reading the maintained documents in section 6. No
important knowledge should exist only inside chat history.
