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
| `TEAM_DEPLOYMENT.md` | Team-mode deployment (Tailscale + shared TimescaleDB/Postgres): server setup, schema migration, data migration, backend startup, backups, onboarding, updating the server, troubleshooting. Single-developer setups (the default) never need this file. |

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
7. For non-trivial work (more than a single-file/doc-only fix), check
   the Active Work Registry before starting — `python
   tools/work_item_cli.py check --files <files you plan to touch>` (or
   `POST /api/work-items/pre-work-check`) — and register a work item
   (`... create --title "..." --files ... --owner-type ai` for an
   AI-assisted session) so Mission Control and any other
   human/AI collaborator can see it. Never blocks; a `critical`/`high`
   overlap warning means coordinate or pick something else, not "stop."
   See `src/futures_bot/collaboration/`'s module docstring.

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
| `src/futures_bot/api/` | FastAPI app + routes (backtests, optimizer, ML, market data, live, research server, trades, reports, imports, accounts, collaboration). |
| `src/futures_bot/backtest/` | Backtesting engine, metrics, HTML/text report generation. |
| `src/futures_bot/research/` | Feature engineering, grid-search optimizer, ML dataset/training/prediction, trade import, trade store. |
| `src/futures_bot/research_server/` | Autonomous layer: paper trader, nightly jobs, orchestrator. Distinct from `research/` — this is the always-on process, not the analysis library. |
| `src/futures_bot/strategy/` | Strategy implementations (`ema_crossover`, `opening_range_breakout`, `vwap_reversion`, `trend_pullback/`). |
| `src/futures_bot/market_data/` | Market-data sync/scheduler/store and the vendor contracts client. |
| `src/futures_bot/brokers/` | Paper broker (used everywhere except live CLI trading) and the Tradovate live broker adapter. |
| `src/futures_bot/risk/` | Risk manager — daily loss kill switch, trade caps, trading-hours filter, force-flat. |
| `src/futures_bot/context/` | Market Context Engine — **complete and integrated into `TradingEngine` (2026-07-27)**, every dimension real: `MarketContext` value object, `ContextEngine` (configurable via `scoring_config`), `session.py`, `volatility.py`, `regime.py`, `timeframe.py`, `structure.py`, `trend.py`, `liquidity.py`, `risk.py` (all classification dimensions), `scoring.py` (`EnvironmentScore`, configurable weights via `ScoringConfig`), `analytics.py` (dev/research distribution reports). Wired into `engine.py` via `ContextMode` (OFF/OBSERVE/ENABLED — OFF is the default for every existing caller, a complete no-op). See section 8, `docs/ARCHITECTURE.md`'s "Market Context Engine" section, and `docs/CONTEXT_ENGINE_COVERAGE.md`/`CONTEXT_ENGINE_LOOKAHEAD_AUDIT.md`/`CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`/`CONTEXT_ENGINE_ARCHITECTURE_REVIEW.md`. |
| `src/futures_bot/db/` | Team-deployment mode's Postgres/TimescaleDB plumbing (2026-07-27): `engine.py` (pooled SQLAlchemy `Engine`, `FUTURES_BOT_DATABASE_URL`), `health.py` (`check_database_health()`), `schema.py`/`research_schema.py` (Core `Table`/`MetaData` for `market_data.db`'s 5 tables and `research.db`'s 16 as of 2026-07-28, the source Alembic autogenerates against). Imported lazily everywhere else — a SQLite-only setup never needs the `db` extra installed. See `TEAM_DEPLOYMENT.md`. |
| `src/futures_bot/accounts/` | Lightweight user/organization accounts, MVP (2026-07-28) + Registration & Organization Management (2026-07-28): `store.py`/`pg_store.py` (`users`/`organizations` tables, SQLite + Postgres, `get_account_store()` factory), four fixed roles (owner/admin/member/viewer), an auto-generated personal `api_key` (placeholder for future auth, not enforced anywhere yet) plus profile fields (timezone/preferred AI model/default branch prefix/notification preferences), `permissions.py` (a flat, advisory-only role→capability table — no request-level identity exists yet to enforce it against). Deliberately not an authentication system — no password, no backend session, no login route; see that module's own docstring for how it's meant to support stronger auth later without a redesign. The frontend's `frontend/src/session.tsx` is a separate, explicitly non-authoritative "current user" concept (a `localStorage` id) built on top of this — see its own docstring. |
| `src/futures_bot/collaboration/` | Active Work Registry, MVP (2026-07-28) + SIL Phase 2 "Workflow Integration" (2026-07-28) + SIL Phase 4 "Intelligent Automation Layer" (2026-07-29): `store.py`/`pg_store.py` (`work_items`/`work_item_activity` tables — the full `planned→claimed→in_progress→testing→ready_for_review→merged→completed` lifecycle, `owner_type` human/ai, `is_draft` for git-watcher output, claim/release/complete/reassign/`update_status`/`approve_draft_work_item`/`discard_draft_work_item`, an append-only activity log), `overlap.py` (V1: warn-only file-path overlap), `overlap_v2.py` (V2: shared imports/API routes/DB tables/frontend components/config files/title keywords, one explainable 0-100 confidence score — additive, V1 untouched), `git_info.py` (live, read-only branch/ahead-behind/last-commit/changed-files/commits-since via `git` subprocess calls, nothing persisted), `merge_readiness.py` (explainable 0-100 score; `test_status` always `"unknown"` — no CI integration exists), `timeline.py` (searchable feed merging work-item activity with real git commits), `context_bundle.py` (SIL Phase 4: one-call aggregation of active/similar-past work, overlap, commits, branch info, relevant docs — no new architecture graph, no semantic search), `git_watcher.py`/`maintenance.py` (SIL Phase 4: two background schedulers, `automation.enabled` in config.yaml default `False`, see `docs/ARCHITECTURE.md`'s "SIL Phase 4" section for the exact dedup/supersede/staleness contracts). `tools/work_item_cli.py`/`local_validate.py`/`draft_changelog.py` are the terminal front ends. See that package's own docstring for what's still deliberately out of scope (a real dependency/architecture graph, semantic merge analysis, a persistent AI-worker execution layer — SIL Phase 4 added automatic *detection* of unregistered work, not execution of it). |
| `alembic/` | Schema migrations for the team-deployment Postgres/TimescaleDB path only (2026-07-27) — `alembic upgrade head`, never automatic on boot. SQLite (the default) has no migration history here; it still self-creates via each store's own `ensure_schema()`. |
| `deploy/` | Dockerfiles, docker-compose (including the `timescaledb` service for team-deployment mode, 2026-07-27), systemd units. |
| `docs/` | Architecture, research server, ML workstation, strategy authoring, trade importer, trading workflow, user manual. |
| `tools/` | Data-maintenance/ops scripts (contract building, schema fixes, backup/merge/restore, and — 2026-07-27 — `migrate_to_timescaledb.py`/`backup_timescaledb.py` for team-deployment mode), plus — 2026-07-28 — `work_item_cli.py` (terminal front end for the Active Work Registry; see section 6's step 7), plus — 2026-07-29, SIL Phase 4 — `local_validate.py` (maps uncommitted changes to their likely tests, falls back to the full suite when the mapping is incomplete) and `draft_changelog.py` (drafts a CHANGELOG.md-style entry to a gitignored scratch file, never edits the real file). Not part of the installable package. |
| `scripts/` | `start.ps1`/`stop.ps1`/`restart.ps1`/`status.ps1` — the official one-command boot/shutdown/status system (2026-07-27) for the single-developer/local-SQLite path. `start.cmd` at repo root double-click-launches `start.ps1`. See section 9 and `BOOT_CHECKLIST.md`. `start-team.ps1` (2026-07-27) is the separate team-deployment entry point — see `TEAM_DEPLOYMENT.md`, not `BOOT_CHECKLIST.md`. |
| `market_data.db` | Historical OHLCV market-data cache. Gitignored, can reach ~1 GB+; regenerate via `tools/pull_massive_flatfiles.py` and related scripts, never commit it or a backup. Team-deployment mode (`FUTURES_BOT_DATABASE_URL` set) replaces this file with a shared TimescaleDB instance instead — see `TEAM_DEPLOYMENT.md`. |
| `research.db` | Backtests/trades/experiments/ML-model research database. Gitignored. Same team-deployment substitution as `market_data.db` above. |

## 8. Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full
system-layer diagram (frontend → API → core engine → research layer →
persistence) and dependency-direction rules — keep it current when
architecture changes.

**Target layering (Context Engine complete and integrated):** Market
Data → Context Engine → Strategy Engine → Risk Engine → Execution.
`context/` is fully built, validated, and wired into `TradingEngine` via
`engine.ContextMode` (OFF/OBSERVE/ENABLED) — see `docs/ARCHITECTURE.md`'s
"Market Context Engine" section for the exact execution flow, the
configuration system, and the known limitations before touching
`engine.py`, `strategy/`, or `risk/` further. `Strategy.on_bar`'s call
signature and every existing bundled strategy's behavior are unchanged
(`context/` only reaches a strategy via the optional `self.context`
attribute, set only in `ENABLED` mode for a strategy that explicitly
opts in via `uses_context = True`); `OFF` (the default for every
existing caller) is a complete no-op. Deciding whether/how a *specific*
strategy should actually consult `self.context` to change its own
decisions, and whether context snapshots get persisted for research,
both still need explicit approval per the protected list below.

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
