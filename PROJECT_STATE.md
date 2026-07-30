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
- Test suite: **1720 tests as of 2026-07-29** (default run, no
  `FUTURES_BOT_DATABASE_URL`: 1669 passed, 51 skipped, 0 failed). The 51
  skips are every team-deployment live-server test (`test_pg_market_data_store_live.py`,
  `test_pg_trade_store_live.py`, `test_pg_account_store_live.py`,
  `test_pg_collaboration_store_live.py`, `test_db_health.py`'s live cases,
  `test_api_market_data_live.py`, `test_migrate_to_timescaledb.py`) —
  they skip cleanly by design when no reachable Postgres/TimescaleDB is
  configured, not a failure. With `FUTURES_BOT_DATABASE_URL` pointed at
  `deploy/docker-compose.yml`'s `timescaledb` service, all 1720 run for
  real (see "Team deployment" below for the exact count and what those
  tests actually verify against a live server). One test
  (KNOWN_ISSUES.md ISSUE-002) is a known
  test-order-dependent flake — treat an isolated failure there as the
  known flake, not a new regression, until it's root-caused. Requires
  the `ml` extra (`pip install -e ".[dev,ml]"`) for the ML
  dataset/training/predict test modules to even collect — without it
  they fail collection with `ModuleNotFoundError`, not a real failure.
  Requires the `db` extra (`pip install -e ".[dev,ml,db]"`) for every
  team-deployment test file to even collect (same
  `ModuleNotFoundError`-not-a-real-failure caveat).
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
- Market Context Engine **complete and integrated into `TradingEngine`**
  (2026-07-27, `src/futures_bot/context/` + `engine.py`): typed
  `MarketContext` value object + `ContextEngine`. **Every dimension is
  real** — `session.py`, `volatility.py`, `regime.py`, `timeframe.py`,
  `structure.py`, `trend.py`, `liquidity.py`, `risk.py` — plus a
  combined 0-100 informational score (`scoring.py`'s
  `score_environment`, weights configurable via `ScoringConfig`, never
  consulted for a trade decision) and developer analytics
  (`analytics.py`'s `analyze_context_batch`, distribution reports over
  a batch of contexts). Internally validated (no circular imports, no
  duplicated logic, deterministic, missing-data-safe — see
  `tests/test_context_engine_validation.py`), audited for look-ahead
  bias with no issues found (`docs/CONTEXT_ENGINE_LOOKAHEAD_AUDIT.md`),
  and benchmarked (`docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`,
  `tools/benchmark_context_engine.py`). **Wired into `TradingEngine`
  via `engine.ContextMode`** (OFF/OBSERVE/ENABLED — OFF is the default
  for every existing caller, a complete no-op; verified byte-identical
  trades against a pre-integration-style backtest). `Trade` gained an
  optional `entry_context` field; `Strategy` gained an optional
  `context` attribute plus a `uses_context` per-strategy opt-in flag —
  every existing bundled strategy is unaffected in every mode. An OFF-
  vs-ENABLED A/B comparison framework
  (`backtest/context_comparison.py`) runs both through the same
  `run_backtest`/`TradingEngine` and produces a trade-level diff. See
  `docs/ARCHITECTURE.md`'s "Market Context Engine" section (the
  "Integration into `TradingEngine`" subsection covers the execution
  flow in detail) and `docs/CONTEXT_ENGINE_COVERAGE.md` for the full
  per-dimension breakdown. **Platform Verification Phase 2 (2026-07-27)**
  eliminated the duplicate ADX/volatility computation Phase 1's audit
  found (`adx()`/`analyze_volatility()` now each run exactly once per
  bar, not twice — `cProfile`-confirmed, ~35-46% reduction in per-run
  context-generation cost) and closed the stale-`Strategy.context` gap
  defensively (`TradingEngine` now resets it at construction and every
  bar, unconditionally, regardless of mode). See
  `docs/PLATFORM_VERIFICATION_PHASE2.md`.
- FastAPI research server + React dashboard covering all of the above,
  plus an autonomous paper-trading/nightly-jobs layer
  (`research_server/`).
- Tradovate live-broker adapter; trade-import/reconciliation pipeline.
- Deploy: Dockerfiles (CLI + API), docker-compose, systemd units,
  bare-metal deployment doc.
- **Team deployment (Tailscale + centralized TimescaleDB) — complete,
  verified against a live server, and the real production data migration
  is now done (2026-07-28).** `FUTURES_BOT_DATABASE_URL` unset (default)
  is byte-identical to before; set, `PgMarketDataStore`/`PgTradeStore`
  transparently replace the SQLite stores for every caller, schema
  managed by Alembic, `bars` a real TimescaleDB hypertable,
  `GET /api/system/health` + Mission Control wired to it,
  `tools/migrate_to_timescaledb.py`/`tools/backup_timescaledb.py`/
  `scripts/start-team.ps1` all built and verified. The real
  `market_data.db`/`research.db` (3.5M `bars` rows, 14.8k `trades`) has
  been migrated for real, and Team Mode's core networking bug (Windows
  Firewall blocking inbound tailnet traffic) is root-caused and fixed —
  `start-team.ps1` now auto-creates the needed rule. See "Last Completed
  Work" below and `TEAM_DEPLOYMENT.md`. Not yet done: the user actually
  creating the firewall rule (needs one UAC click) and a genuine
  second-machine Tailscale connection (a registered peer exists but was
  offline this session).
- **Team Collaboration Platform MVP — lightweight accounts + Active Work
  Registry (2026-07-28).** `src/futures_bot/accounts/` (`users`/
  `organizations`, four fixed roles, full CRUD API) and
  `src/futures_bot/collaboration/` (claimable work items with claim/
  release/complete/reassign, an append-only activity log, warn-only
  file-path overlap detection, a pre-merge summary endpoint) — both
  SQLite + Postgres, Alembic-migrated, wired into two new real (not mock)
  Mission Control panels. Deliberately not an authentication system yet.
  See "Last Completed Work" below.
- **SIL Phase 2 "Workflow Integration" (2026-07-28).** Built on the MVP
  above: the full work-item lifecycle + `owner_type`, Overlap Engine V2
  (imports/routes/DB tables/components/config/keywords, one explainable
  confidence score), live git branch info, a merge-readiness dashboard
  (`test_status` always `"unknown"` — no CI integration exists),
  a searchable activity timeline (work-item events + real git commits),
  `tools/work_item_cli.py`, and Mission Control's 7-tab
  `CollaborationWorkspace`. See "Last Completed Work" below and
  `docs/ARCHITECTURE.md`'s "Team Collaboration Platform" section.
- **User Registration & Organization Management (2026-07-28).** An
  auto-generated personal `api_key` + profile fields on `users`, a
  role→capability table (`accounts/permissions.py`, advisory only),
  org-scoped work items (`work_items.org_id`), a frontend-only
  `session.tsx` "current user" concept (still no backend auth), and a
  full onboarding UI: Welcome, Register (create-or-join an org, role
  computed automatically), Profile, Organization Settings, Team Members.
  See "Last Completed Work" below.

## Broken / Incomplete Features

- No CI configured — tests only run when a session runs them by hand.
- No Python formatter/linter configured (no ruff/black/mypy).
- KNOWN_ISSUES.md ISSUE-004 (`bars` schema drift) and ISSUE-005
  (`US80Z` genuine OHLC violations) — both diagnosed, neither fixed.

## Priorities

See ROADMAP.md.

## Last Completed Work

2026-07-29: **Fix: Mission Control's hardcoded fake data
(KNOWN_ISSUES.md ISSUE-040).** Found during "SIL Research Engine 2.0
Phase 1"'s audit (Fork D): `ResearchSummaryCard`/`DatabaseSummaryCard`/
`MarketContextSummaryCard`/`PerformanceCard`/`HealthGrid`/`ActivityFeed`/
`AlertCenter`/`RoadmapPanel` all rendered hardcoded placeholder numbers
from a "design/scaffold pass" (`missionControlData.ts`) -- a fake profit
factor of 1.42, a fake $18.6 expectancy, a fake "187 completed
backtests," a fake "94% operational" score -- sitting on the same live
page as real, API-backed panels with no visual distinction, directly
contradicting the page's own module docstring ("nothing on this page is
mocked"). `StatusBar.tsx` (global chrome) had the same problem
independently (git branch/commit frozen to one specific past commit).
Fixed one panel at a time, always toward a real API or an honest
removal: `ResearchSummaryCard`/`DatabaseSummaryCard` wired to
`GET /api/system/overview` (extended with 5 new server-computed
aggregate fields, `api/services.py::system_overview`) and the already-real
`GET /api/market-data/overview`; `MarketContextSummaryCard`/`PerformanceCard`
removed (no live regime reading exists; the latter duplicated the
already-real `InfrastructurePanel`); `HealthGrid` rebuilt from only
genuinely checkable signals; `ActivityFeed` wired to the already-built
real timeline; `AlertCenter` derives real alerts from existing
automation/health status fields; `RoadmapPanel` kept as explicitly-labeled
static reference; `StatusBar` wired to the real, already-built
`GET /api/git/branch-info`. 20 new tests (9 backend using real backtest
runs through the actual engine, not synthetic mocks; 11 frontend
asserting the old fake literals never appear). Full suite green: 1669
backend (1660->1669), 145 frontend (134->145), 0 failed either side,
`tsc -b`/`oxlint` clean. No architecture doc update needed (frontend-only
plus one additive `SystemOverview` schema extension, no new subsystem) --
see `KNOWN_ISSUES.md` ISSUE-040 for full detail.

2026-07-29: **Fix: Integration Queue was O(N^2) in queue size
(KNOWN_ISSUES.md ISSUE-039).** Found by actually measuring the
just-shipped Milestone 2 pipeline instead of assuming it was fine:
`GET /api/integration/queue` took ~145ms at 1 item but ~23s at 50 items
(measured via `TestClient`, real repo files) — each queued item's
readiness was computed via a fully independent call that re-fetched
every active item from the DB and re-parsed every one of their files
from scratch, no caching, giving roughly N x N file re-parses for N
items. Fixed: `overlap_v2.analyze_files`/`compute_overlap_v2` gained an
optional `file_cache` parameter (memoizes each file's parse per
request); `get_integration_queue` fetches the active set once and
shares one cache across every item instead of each item repeating both
from scratch; `generate_integration_review` (which separately computed
Overlap Engine V2 twice on identical inputs) now reuses
`merge_readiness`'s already-computed warnings via a new
`OverlapWarningLike` structural-typing `Protocol`. No item's score
changed — confirmed by the existing golden-equivalence test plus 6 new
regression tests directly counting file reads. Re-measured: 50 items
now ~6.3s (was ~23s, ~3.7x faster), scaling roughly linearly instead of
quadratically. Remaining cost (~120ms/item, `git` subprocess spawns for
per-item branch info) is inherent and documented, not eliminated — it
degrades gracefully now instead of exploding. See `KNOWN_ISSUES.md`
ISSUE-039 for the full before/after numbers.

2026-07-29: **SIL Phase 6 "Integration Coordinator" Milestone 2 —
Intelligent Review Pipeline.** Builds directly on Milestone 1 (below) —
neither the Worker Registry nor the Integration Queue were redesigned.
Adds a PERSISTENT Integration Review: a new `integration_reviews` table
(`collaboration/store.py`/`pg_store.py`, Alembic `b91531ca7583`),
append-only (no update/delete path anywhere — a deliberate, one-off
exception to this package's "compute live, never persist" rule, since
the point is a historical trail, not a fresher live number), generated
via `POST`/`GET /api/work-items/{id}/reviews`
(`api/collaboration_service.py::generate_integration_review`/
`list_integration_reviews`). Wires together only existing primitives —
no new scoring model: `merge_readiness.py` (overlap + branch info), a
new Conflict Resolution Assistant (`collaboration/conflict_resolution.py`
— suggested resolution + integration order layered on Overlap Engine
V2's existing scoring), a minimal, explicitly-honest subsystem lookup
(`collaboration/architecture_map.py` — a hand-curated path-prefix table,
NOT a real architecture/dependency graph; SIL Phase 5's "Trading
Intelligence Layer" was proposed, never built, and this milestone does
not pretend otherwise), and a shared Validation Planning heuristic
(`collaboration/validation_planning.py`, extracted unchanged from
`tools/local_validate.py` so the CLI and the API share one file→test
mapping instead of two). Every review also logs `review_generated`
(and `integration_recommended` when `level == "ready"`) into the
existing `work_item_activity` timeline — reuse, not a second timeline
mechanism. Also closed the one write path Milestone 1 deliberately
deferred: `collaboration/maintenance.py::_mark_stale_workers_offline`
flips a stale worker's `status` to `offline` (via a new
`store.set_worker_status`, which never touches `last_heartbeat_at` — a
worker's next real heartbeat recovers it). `WORKER_STALE_AFTER_SECONDS`/
`is_worker_stale` moved from `api/worker_service.py` into
`collaboration/__init__.py` so both the API layer and `maintenance.py`
share one definition (`collaboration/` must not import from `api/`).
Mission Control: `IntegrationQueuePanel.tsx`'s existing row expansion
(not a new panel — "avoid duplicate dashboards" was explicit in this
milestone's own spec) gained a "Generate Integration Review" button plus
the latest review's summary/recommendation/affected subsystems/
validation recommendation/conflict resolutions and a bare
confidence-trend string across history. 60 new tests this milestone (55
backend: three new-module unit-test files — `test_architecture_map.py`,
`test_conflict_resolution.py`, `test_validation_planning.py` — plus
store/API/maintenance additions including a concurrent-review-creation
test and five stale-worker-cleanup tests; 5 frontend). Full backend
suite verified green after every logical chunk; see
`docs/ARCHITECTURE.md`'s "SIL Phase 6" section (Milestone 2 subsection)
for the full design.

2026-07-29: **SIL Phase 6 "Integration Coordinator" Milestone 1 — Worker
Registry + Integration Queue.** Scoped deliberately (a design-review
subagent validated the plan and pushed back on several framings before
implementation started) as a foundation, not the full spec's much larger
"Integration Branch"/"Coordinator Memory"/"Rollback Intelligence" pieces
— those are explicit, documented Milestone 2+ items
(`ROADMAP.md`'s Future section), not built now. Built: a `workers` table
(`collaboration/store.py`/`pg_store.py`, Alembic `6794ec903e7c`) — a
generic Worker Registry (`WORKER_TYPES`, deliberately not `OWNER_TYPES`:
human developers, Claude Code sessions, other/future AI agents,
validation/research workers, background services, future distributed
compute workers) with upsert heartbeats (`POST
/api/workers/{id}/heartbeat`, `api/worker_service.py`) and JSON
capability tags; the Integration Queue (`GET /api/integration/queue`,
`GET /api/work-items/{id}/review`) — a *view* over `work_items` reusing
`merge_readiness.py`/`overlap_v2.py`, no new persisted queue, ordered by
confidence score with an explicit `priority`→`updated_at` tiebreak.
Mission Control's `WorkforcePanel.tsx`/`IntegrationQueuePanel.tsx`;
`CollaborationWorkspace.tsx`'s pre-existing "Merge Queue" tab (a plain
client-side filter, no real scoring) renamed to "Testing / Ready for
Review" to avoid two different things both called "queue." 42 new tests
this milestone (35 backend: 1599 total, 51 skipped, 0 failed; 7
frontend: 129 total, 0 failed). See `docs/ARCHITECTURE.md`'s "SIL Phase
6" section.

2026-07-29: **SIL Phase 6 collaboration audit — 5 findings, 2 fixed with
regression tests, 3 documented and deliberately deferred.** Requested
before any new "Integration Coordinator" feature work, per that
feature's own spec ("fix all legitimate bugs before introducing major
new functionality"). Fixed: orphaned zero-user organizations from
registration's two-call non-atomicity (now counted, never deleted, in
`GET /api/automation/status`); a missing index on
`work_item_activity.created_at` (Alembic `bb0017abe70b`). Documented,
not fixed: an `api_key`-harvesting endpoint chain (needs real auth to
close properly, not a superficial patch); `git_watcher.py`'s
cross-process dedup race under multi-machine Team Mode (low severity);
a measured SQLite concurrent-writer ceiling (~100 threads) directly
relevant to the next feature's design. See `CHANGELOG.md`'s matching
entry and `KNOWN_ISSUES.md` ISSUE-034 through ISSUE-038. 1564 backend
tests, 0 failed.

2026-07-29: **Pull-only auto-sync (`collaboration/git_sync.py`).** A
third opt-in scheduler alongside SIL Phase 4's git-watcher/maintenance
(same daemon-thread shape) that fast-forwards this checkout from its
remote on an interval — never pushes, never touches a dirty working
tree, never merges/rebases a diverged history. `automation.git_sync_enabled`
(default `False`, independent of `automation.enabled`). Prerequisite
fixed first: local `main` was 60 commits ahead of `origin/main` — nothing
this whole session had ever been pushed; pushed with explicit
confirmation. 31 new tests, including 6 specifically covering the
enabled/git_sync_enabled independent-gating logic (this exact function
was restructured to add the new toggle, and a wrong gate is the kind of
regression that fails silently). Full suite green: 1560 backend, 122
frontend. See `CHANGELOG.md`'s matching entry.

2026-07-29: **SIL Phase 4 "Intelligent Automation Layer" — 6 milestones,
built on the existing Collaboration Platform with no redesign.** Full
detail in `docs/ARCHITECTURE.md`'s "SIL Phase 4" section and
`KNOWN_ISSUES.md` ISSUE-029 through ISSUE-033; this is the summary.

- **Milestone A — AI context bundle**: `collaboration/context_bundle.py`,
  `POST /api/collaboration/context-bundle`, `work_item_cli.py context` —
  one call aggregating active/similar-past work, overlap warnings,
  recent commits, branch info, and relevant KNOWN_ISSUES/ROADMAP
  excerpts. Pure aggregation of existing systems, no new persisted index.
- **Milestone B — draft work items + background git-watcher**:
  `work_items.is_draft` (Alembic `7a3f9c2e1b0d`), `collaboration/git_watcher.py`
  (same daemon-thread shape as `MarketDataScheduler`) — periodically
  drafts exactly one work item covering uncommitted changes not covered
  by any active item, self-healing and idempotent, never touching a real
  item. Approve/discard via API or CLI.
- **Milestone C — local validation**: `tools/local_validate.py` maps
  changed files to their likely tests (confirmed against real examples;
  not every module has a same-named test file), falls back to the full
  suite rather than silently under-testing, also runs tsc-b/lint/vitest
  for frontend changes.
- **Milestone D — documentation draft assistant**: `tools/draft_changelog.py`
  drafts a CHANGELOG.md-style entry from commits + completed/merged work
  items since CHANGELOG.md's own last commit, writes to a gitignored
  scratch file, never edits the real file.
- **Milestones E+F — automation status panel + maintenance job**:
  `collaboration/maintenance.py` discards stale drafts and checks DB
  health; `GET /api/automation/status` + Mission Control's new
  `AutomationPanel.tsx` show both schedulers' state and let a human
  approve/discard drafts inline.
- Both schedulers default to **disabled** (`automation.enabled` in
  config.yaml) — same convention `research_server.enabled` already
  established, specifically so the existing ~1500-test suite's
  `create_app()` calls never start a real background thread.
- **5 real bugs found and fixed during development**, all caught before
  shipping: `git_info._git()`'s whole-string `.strip()` truncating the
  first line of `git status --porcelain` output (ISSUE-029); the
  git-watcher's own drafts self-suppressing their files from the next
  cycle (ISSUE-030); an off-by-one at the exact staleness cutoff
  (ISSUE-031); `maintenance.py` eagerly importing `db.health`, breaking
  the "SQLite-only never needs the `db` extra" guarantee (ISSUE-032);
  three frontend test fixtures missing the new `is_draft` field, caught
  by `local_validate.py` on its own first real use (ISSUE-033).
- Full suite verified green after every milestone: 1593 tests (1542
  passed, 51 skipped) backend, 121 passed frontend (21 files).

2026-07-28: **Live incident response — Team Mode boot through Live
Session, four real bugs found and fixed while actually using it.** Not a
planned pass — a live "boot Team Mode and use it" session. Full detail in
`CHANGELOG.md`'s matching dated entry and `KNOWN_ISSUES.md` ISSUE-025
through ISSUE-028; this is the summary.

- **ISSUE-025 (Critical)**: Team Mode's production frontend build
  silently kept the local-dev loopback API URL (`http://127.0.0.1:8000`)
  baked in — `start-team.ps1`'s empty-string `VITE_API_BASE_URL` trick
  doesn't survive Windows' PowerShell → npm.cmd → cmd.exe → vite
  child-process chain. Broke every teammate's API calls. Fixed by basing
  the default on `import.meta.env.DEV`/`PROD` instead.
- **ISSUE-026 (Critical)**: `PgAccountStore` returned `None` for
  `notification_preferences`, crashing every response containing a user
  (including registration's own response) with a Pydantic
  `ValidationError` → 500. Fixed by normalizing `None` → `{}`, matching
  the SQLite store. Verifying this fix's live-Postgres regression test
  accidentally wiped a teammate's just-registered account via the test
  fixture's `TRUNCATE` cleanup — documented as an incident/lesson in
  ISSUE-026, damage limited to accounts data, re-registration succeeded.
- **ISSUE-027 (High)**: a sole org owner who demoted themselves via Team
  Members lost `manage_members` on the next refresh, locking themselves
  out of fixing anyone's role including their own (permissions are
  advisory-only, not enforced server-side). Fixed: you can no longer
  edit your own role from that page, ever.
- **ISSUE-028 (High)**: the Live Session start form defaulted to a
  hardcoded, months-expired contract ticker (`MESH6`) — the session
  reported "running" forever with zero bars, no error surfaced (an empty
  feed result isn't a feed error). Fixed: the field now starts empty
  with guidance on picking the current quarterly contract code.
- Also found and fixed the local docker-compose TimescaleDB running 3
  Alembic migrations behind (brought current via `alembic upgrade
  head`), and cleaned up leftover debug `print()` statements in
  `feeds/massive.py` (converted to `log.debug()`).
- 4 new regression tests, each confirmed to fail against its bug's
  pre-fix code before the fix landed. Full backend and frontend suites
  verified passing after every change.

2026-07-28: **Stabilization Mode: concurrency and edge-case sweep.** A
~1-hour skeptical bug hunt across the same day's two preceding entries
(SIL Phase 2, User Registration & Organization Management). Full detail
in `CHANGELOG.md`'s matching dated entry and `KNOWN_ISSUES.md` ISSUE-022
through ISSUE-024; this is the summary.

- **Fixed a real concurrent-claim race** (ISSUE-022, High severity):
  `claim_work_item` could let two racing callers both "win" silently,
  with neither told about the conflict. Fixed with an atomic
  conditional `UPDATE` + rowcount check in both SQLite and Postgres
  stores.
- **Fixed silent action failures** (ISSUE-023): every
  claim/release/complete/advance handler in `WorkItemTable.tsx` had no
  error handling and this app has no error boundary — a rejected call
  vanished with no feedback. Now caught and displayed.
- **Fixed a Register retry dead-end** (ISSUE-024): a failed account
  creation after a successful org creation left an orphaned org and no
  way to retry without hitting "already exists." The created org id is
  now cached across retries.
- **Fixed a scoping gap**: `TeamPanel.tsx` (Mission Control) still
  showed every registered user globally instead of the signed-in user's
  own organization — missed when org-scoping landed elsewhere. Also
  added an online/offline badge and refreshed a stale docstring.
- Every fix independently confirmed against a regression test that
  fails on the pre-fix code (verified by temporarily reverting each fix
  and re-running its test). Full backend suite (1468 passed, 50
  skipped) and full frontend suite (110 passed, up from 107; `tsc -b`/
  `oxlint` clean) verified passing after every fix.
- **Swept and found clean**: every other check-then-set-shaped method,
  username uniqueness (DB-level, already atomic), every frontend
  `setInterval` (all have matching cleanup), the org-scoping SQL
  builder (parameterized, no injection risk). Two items from the
  stabilization brief ("Architecture Intelligence Engine", a persisted
  "dependency graph") don't exist as literal systems in this codebase —
  noted rather than fabricating findings against something not built.

2026-07-28: **User Registration & Organization Management.** Expands the
lightweight accounts MVP into a full onboarding experience, staying
explicitly no-auth. Full detail in `CHANGELOG.md`'s matching dated entry
(same day, appears above SIL Phase 2's entry below); this is the summary.

- **Auto-generated personal API key** (`fbot_`-prefixed, unique) plus
  profile fields (timezone, preferred AI model, default branch prefix,
  notification preferences) on `users` — new Alembic revision
  `1be0295e6dbe`. `UserMeOut` (includes the key) vs. `UserOut` (never
  does) keeps one member's key from leaking into another's roster view.
- **`accounts/permissions.py`**: a flat role→capability table
  (manage_organization/manage_members/manage_work/view) — advisory only,
  consulted by the frontend, not enforced server-side (there's still no
  request-level identity to check it against).
- **`work_items.org_id`** (nullable, new Alembic revision `9f68d758839f`)
  — "each organization contains Active Work/AI Workers/Collaboration
  data" is now actually true; overlap/conflict/merge-readiness/
  pre-work-check are all org-scoped when an `org_id` is supplied,
  `None` still means "no filter" for every session-less caller.
- **`session.tsx`** — a frontend-only "current user" concept (a user id
  in `localStorage`, resolved via `GET /api/users/{id}/me`); explicitly a
  UX convenience, never a security boundary, since there's still no
  backend session. `RequireSession` gates the whole app behind having
  registered/picked an account once.
- **New pages**: Welcome, Register (create-or-join an org; role is
  computed automatically, never a form field), Profile (preferences +
  API key display/regenerate), Organization Settings (rename, gated),
  Team Members (roster, online status, role changes, gated).
- **Found and fixed a real bug**: `Profile.tsx` crashed if `currentUser`
  was `null` when its `useApi` fetcher first ran (a stale/cleared session
  while already mounted) — fixed, regression test added.
- **~20 new backend tests, 22 new frontend tests** (107 total, up from
  85) — `tsc -b`/`oxlint` clean, full backend suite (1467 passed, 50
  skipped) verified passing.

2026-07-28: **SIL Phase 2 "Workflow Integration" — the Collaboration
Platform becomes the actual workflow, not just infrastructure.** Built
directly on the MVP below with no redesign. Full detail in `CHANGELOG.md`'s
matching dated entry (appears above the MVP entry below); this is the
summary.

- **Full work-item lifecycle**: `STATUSES` grows from
  open/claimed/completed to a full pipeline
  (`planned→claimed→in_progress→testing→ready_for_review→merged→completed`,
  advisory not enforced — a review can send work backward) via a new
  `update_status()`/`POST /api/work-items/{id}/status`. `owner_type`
  (human/ai) added to `work_items` (Alembic-migrated on Postgres, `ALTER
  TABLE` on SQLite, defaults to `human` for every pre-existing row).
- **Overlap Engine V2** (`collaboration/overlap_v2.py`): shared Python/TS
  imports, API route paths, DB table names, frontend component names,
  config files, and title/description keywords — one explainable 0-100
  confidence score with a factor breakdown, additive alongside the
  original file-path-only V1 (untouched, still what `create_work_item`/
  `check_overlap`/`merge_summary` use). New `GET /api/work-items/{id}/
  overlap-v2`, `GET /api/work-items/conflicts` (bulk pairwise scan),
  `POST /api/work-items/pre-work-check` (the "AI awareness" contract —
  callable before a work item even exists).
- **Live git branch info** (`collaboration/git_info.py`): branch age,
  ahead/behind vs a base branch, last commit — via `git` subprocess
  calls, nothing persisted, best-effort (detached HEAD/no upstream/not a
  repo degrade to notes, never an exception). `GET /api/git/branch-info`,
  `GET /api/work-items/{id}/branch-info`.
- **Merge readiness dashboard** (`collaboration/merge_readiness.py`): one
  explainable 0-100 score from Overlap V2 risk, branch age, behind-base
  count, and change size. `test_status` is always the literal `"unknown"`
  — no CI integration exists, and guessing would be worse than admitting
  that gap. `POST /api/work-items/merge-readiness`.
- **Searchable activity timeline** (`collaboration/timeline.py`): merges
  `work_item_activity` with real git commits into one filterable feed
  (event_type/query/since/until). `GET /api/activity/timeline`. While
  building it, found and fixed a real bug: SQLite's `datetime('now')` is
  only second-resolution, so rapid same-second events (realistic for an
  automated/AI-driven claim-then-complete) tied and sorted unpredictably
  — fixed with a `rowid` tiebreaker, regression test added.
- **`tools/work_item_cli.py`**: create/list/claim/release/complete/
  status/check subcommands, no API server needed. CLAUDE.md's session
  protocol gained a new step requiring non-trivial work to run `check`
  and register a work item first — turns the "AI awareness" requirement
  into something sessions actually do, not just a documented ask.
- **Mission Control's `CollaborationWorkspace`**: 7 tabs (My Active Work,
  Team Active Work, AI Workers, Recent Activity, Merge Queue, Conflict
  Warnings, Ready For Review), every one real data, 15s polling like
  `InfrastructurePanel`. Extracted the list/action rendering out of
  `WorkRegistryPanel` into a shared `WorkItemTable` (claim/release/
  complete/advance-to-next-stage, a compact lifecycle-stage indicator)
  used by both.
- **~70 new backend tests, 13 new frontend tests** (85 total, up from
  72) — `tsc -b` and `oxlint` both clean. Full backend suite (1433
  passed, 47 skipped — live-Postgres only) and full frontend suite
  verified passing.
- Still deliberately out of scope: real authentication, a true
  architecture/dependency graph, semantic merge assistance, real CI
  integration, persistent AI worker processes, a distributed worker
  network — see `docs/ARCHITECTURE.md`'s "Team Collaboration Platform"
  section and `ROADMAP.md`'s "Future" section.

2026-07-28: **Team Collaboration Platform: lightweight accounts, live
Mission Control data, and an Active Work Registry.** Full detail in
`CHANGELOG.md`'s matching dated entry (same day, appears above the
Stabilization Mode entry below); this is the summary.

- **Lightweight user accounts** (`src/futures_bot/accounts/`): `users`/
  `organizations` tables (SQLite + Postgres, Alembic-migrated), four fixed
  roles (owner/admin/member/viewer), full CRUD API
  (`/api/organizations`, `/api/users`). Deliberately not an authentication
  system — no password, no session, no login route.
- **Mission Control now shows real data** for infrastructure and team:
  `GET /api/system/infrastructure` (CPU/memory/disk via a new `psutil`
  dependency, job-queue depth from the real `jobs` table),
  `InfrastructurePanel`/`TeamPanel` replacing mock arrays. "Restart
  services" deliberately not wired — this API has no auth in front of it
  yet.
- **Active Work Registry** (`src/futures_bot/collaboration/`): claimable
  work items (title/owner/branch/status/estimated files/priority) with
  claim/release/complete/reassign, an append-only activity log, warn-only
  file-path overlap detection (`collaboration/overlap.py`,
  no_risk→critical), a Mission Control panel, and
  `POST /api/work-items/merge-summary` for a pre-merge overlap/conflict
  summary. Deliberately out of scope: a dependency/architecture graph,
  semantic merge analysis, persistent AI workers, distributed compute —
  each a substantially larger follow-on effort, see ROADMAP.md.
- **Team Mode's firewall-rule elevation wait is now bounded**, not
  indefinite — `Start-Process -Verb RunAs` can block synchronously on the
  UAC decision regardless of `-Wait`; fixed via a marker-file-based design
  after two other approaches (`Process.WaitForExit`, a `Start-Job`-wrapped
  launch) were tried and empirically shown to still block in this specific
  testing environment.
- **Found and documented (not fixed) a real timestamp-parsing bug**:
  `frontend/src/format.ts::dateTime()` parses SQLite's (local-mode)
  timezone-less timestamps as local time instead of UTC — affects every
  page rendering a local-mode timestamp. See KNOWN_ISSUES.md ISSUE-021.
  The new `TeamPanel.tsx` was written correctly from the start and isn't
  affected.
- **~95 new backend tests, 6 new frontend tests** — store unit tests,
  HTTP route tests, live-Postgres suites (skip cleanly without a server),
  and Mission Control panel tests. Full frontend suite (72 tests,
  typecheck, lint) and every touched backend test area verified passing.

2026-07-28: **Real production data migration completed, plus a
Stabilization Mode pass (10 real bugs found and fixed).** Full detail in
`CHANGELOG.md`'s matching dated entry and `KNOWN_ISSUES.md` ISSUE-014
through ISSUE-020; this is the summary.

- **Real `market_data.db`/`research.db` → TimescaleDB migration is done.**
  Backed up and integrity-checked both `.db` files first; dry-run
  confirmed real counts against the live `deploy/docker-compose.yml`
  `timescaledb` instance; the real `--yes` run hit ISSUE-014 (below)
  partway through `bars`, then completed and verified (destination row
  count ≥ source for every table in both databases) once fixed. This was
  the one item PROJECT_STATE.md's own "Recommended Next Task" flagged as
  an operator decision, not automated tooling work — it's now done.
- **Team Mode networking (Priority #1) root-caused and fixed.** Another
  device on the tailnet couldn't reach the backend even though it worked
  from this machine — confirmed directly: Windows Firewall's Private-
  profile default is `BlockInbound` with zero rule for this app/port, and
  a same-machine test succeeds because local delivery to an owned IP
  never crosses the filtered network path. `scripts/start-team.ps1` now
  calls a new `Confirm-TailscaleFirewallRule` (`scripts/_common.ps1`)
  that auto-creates a rule scoped to Tailscale's own CGNAT range
  (`100.64.0.0/10`, not "any remote address"), self-elevating via one UAC
  prompt if the session isn't already elevated. Verified up through the
  point requiring an actual human to click that prompt — the one step
  that genuinely can't be automated further without weakening it into a
  silent privilege escalation.
- **Six more real bugs found and fixed** during a broad Stabilization
  Mode sweep (API route sweep, frontend review, backend thread-safety
  audit): `bars.created_at NOT NULL` rejecting 32.5% of real rows
  (ISSUE-014), a flaky test caused by `time.monotonic()`'s clock
  granularity (ISSUE-015), a reproducible check-then-set race present in
  three separate `start()` methods across the research server/paper
  trader/live session managers (ISSUE-016, proven with a regression test
  that failed 3/3 pre-fix and passed 3/3 post-fix in all three cases),
  `GET /api/logs` reading an entire 9.2 GB `decisions.jsonl` into memory
  on every call (ISSUE-017, fixed to 0.022s via a bounded tail reader),
  a blank UI panel during `Live.tsx`'s `starting` state (ISSUE-018),
  unhandled exceptions never being logged anywhere (ISSUE-019), and a
  missing lock on `api/jobs.py`'s executor singleton (ISSUE-020, for
  consistency with every other singleton accessor in this codebase).
- **Verified, not just implemented:** full local-mode boot (backend +
  frontend, clean, via `scripts\start.ps1`); team-mode boot up through
  the human-UAC-interaction point; every `test_api_*.py` file (16 files,
  ~160 tests) individually; the full research-server test suite; the
  full frontend suite (65 tests, typecheck, lint).
- **Not done:** actually creating the Windows Firewall rule end-to-end
  (needs the user to click the UAC prompt, or run the one-time elevated
  `New-NetFirewallRule` command this session provided) and a genuine
  second-machine Tailscale connection (a registered peer, `rafaelballer`,
  exists but was offline this session).

2026-07-27: **Team deployment (Tailscale + centralized TimescaleDB) —
complete and verified against a live server.** Continuation of the entry
directly below, unblocked once Docker/WSL2 finished installing. Full
detail in `CHANGELOG.md`'s matching dated entry and `KNOWN_ISSUES.md`
ISSUE-010/011/012 (three real bugs found and fixed, all logged Resolved
immediately); this is the summary plus what's genuinely still open.

**Landed and verified against the live `deploy/docker-compose.yml`
`timescaledb` service (not just compiled/argued — actually run):**
- `deploy/docker-compose.yml`'s `timescaledb` service: brought up, health
  check passing, confirmed via `docker compose ps`.
- Alembic (`alembic/`, `alembic.ini`) manages both databases' schema now
  — two chained revisions, `db/schema.py` (`market_data.db`, 5 tables) and
  the new `db/research_schema.py` (`research.db`, 14 tables — money as
  `NUMERIC`, JSON-as-TEXT as native `JSONB`, INTEGER-flags as `Boolean`,
  timestamps as `TIMESTAMPTZ`). `alembic upgrade head` against the live
  instance creates all 19 tables and converts `bars` into a real
  TimescaleDB hypertable (`timescaledb_information.hypertables`
  confirmed directly, not assumed).
- `PgMarketDataStore` (built last session) actually exercised against the
  live server for the first time: `tests/test_pg_market_data_store_live.py`
  (13 tests, real round trips — idempotent upsert, coverage, sync-run
  lifecycle, gaps, contract rolls, hypertable confirmation) plus
  `tests/test_api_market_data_live.py` (2 tests hitting the real
  `GET /api/market-data/overview`/`GET /api/market-data/runs` routes
  against the live server — the level ISSUE-011 actually surfaced at).
- `src/futures_bot/research/pg_trade_store.py::PgTradeStore` (new this
  session) — full port of `TradeStore`'s ~60 methods, all 14
  `research.db` tables. `api/store.py::get_store()` now branches on
  `FUTURES_BOT_DATABASE_URL` exactly like `get_market_data_store()`
  already did; unset (every existing single-developer setup) is
  byte-identical. Verified: `tests/test_trade_store_parity.py` (3 tests,
  signature parity both directions) and `tests/test_pg_trade_store_live.py`
  (12 tests, real behavior per subsystem — trades, optimization trials
  w/ resume-lookup, runs, reports, jobs, experiments, ML model versioning
  + archive + deployment history, and the full client-import pipeline
  including FIFO lot matching and a failure path).
- `tools/migrate_to_timescaledb.py` (new) — real data migration
  (`--dry-run`/`--yes`, batched `ON CONFLICT DO NOTHING` via SQLAlchemy
  Core against both schema modules directly, source-vs-destination
  row-count verification). Deliberately reads the SQLite files directly
  via `sqlite3` (read-only connection, `mode=ro`) rather than through
  `MarketDataStore`/`TradeStore`'s own read methods — those can't return
  `bars.contract`/`.source` per row (see that method's own docstring), and
  going around `set_active_contract`'s roll-recording side effect avoids
  fabricating history during a bulk replay. `bars.id` is the one column
  never copied (KNOWN_ISSUES.md ISSUE-004: already `NULL` for every row
  in the live SQLite database — nothing to preserve). Verified against
  synthetic fixtures (never the real 927 MB/26 MB production files) built
  through the real `MarketDataStore`/`TradeStore` classes, covering all 19
  tables: `tests/test_migrate_to_timescaledb.py` (5 tests) — a first run
  reports real per-table insert counts (ISSUE-012, caught and fixed
  here), a second run against the same data is idempotent (0 new rows),
  data round-trips with correct types (Decimal/JSON dict/bool/ISO
  string), and `bars.id` is freshly generated, never copied.
- `tools/backup_timescaledb.py` (new) — `pg_dump`-based backup +
  `db_backups/last_backup.json` marker. Unit-tested
  (`tests/test_backup_timescaledb.py`, 8 tests: DSN-to-pg_dump-flags
  translation, password never leaks into the args list, marker shape,
  graceful failure both without `FUTURES_BOT_DATABASE_URL` and without
  `pg_dump` on PATH). The actual `pg_dump` invocation itself needs an
  operator with Postgres client tools installed — confirmed the graceful-
  failure path for real (this dev sandbox has no `pg_dump`), not the
  success path.
- `scripts/start-team.ps1` (new) — team-mode boot script. Syntax-parsed
  successfully; confirmed to fail safely (clear message, exit 1, no
  network/build side effects) when `FUTURES_BOT_DATABASE_URL` isn't set,
  before touching Tailscale detection or the frontend build. **Not run
  end-to-end** — that needs a real Tailscale network to actually bind a
  non-loopback address against, deliberately not attempted without an
  operator present for a genuinely network-exposing action.
- `GET /api/system/health` (new route, `api/routes/system.py`,
  `api/schemas.py::SystemHealthOut`) — backend liveness (the response
  itself is the proof), database configured/ok/latency/error
  (`db/health.py`), process uptime (`time.monotonic()` since import),
  last-backup timestamp (reads the marker above), and an honest
  "connected users" estimate (`api/connected_users.py`'s process-local
  15-minute sliding-window distinct-IP tracker — explicitly documented as
  approximate, since there's still no real auth/session system to count
  against). Every field degrades gracefully (no `FUTURES_BOT_DATABASE_URL`,
  a missing/corrupt backup marker, SQLAlchemy not installed at all) rather
  than 500ing. 20 new tests across `tests/test_api_system_health.py` (10),
  `tests/test_connected_users.py` (7), `tests/test_db_health.py` (6, one
  new case added to the 3 unconfigured/unreachable cases: configured-and-
  healthy against the live server).
- `db/engine.py::prime_engine` + `api/app.py::_maybe_prime_db_engine` — a
  real gap closed: `config.py::DeploymentSettings.pool_size`/
  `max_overflow`/`pool_recycle_seconds` were added last session but
  nothing ever actually passed them to `get_engine()` (every real call
  site used its bare defaults, silently ignoring `config.yaml`). Now
  primed once at API startup, tuned from `config.yaml` if present, a
  no-op for every SQLite-only setup.
- Mission Control's `StatusBar`/`HealthGrid` now consume the real
  `/api/system/health` payload for exactly the fields the plan scoped for
  them (`frontend/src/api.ts::getSystemHealth`, `types.ts::SystemHealth`):
  `StatusBar`'s version/environment badge/uptime; `HealthGrid` gained a
  conditionally-rendered "Team Database (TimescaleDB)" card, shown only
  when `database.configured` — the existing mock "Database"/"Research
  Database" cards (SQLite-file-oriented) are untouched, since team mode
  doesn't change what those describe. Every other Mission Control section
  stays mock, per the plan's own scope. Frontend typecheck/lint/vitest
  (64 tests) and a production `npm run build` all clean; **not verified
  visually in a browser this session** — Claude in Chrome was unavailable
  (connection failed/disabled), flagged explicitly rather than claimed.
- `docs/ARCHITECTURE.md`'s PERSISTENCE section, `CLAUDE.md`'s section-6
  doc table and File Ownership table, and `TEAM_DEPLOYMENT.md` itself
  (written by a corrective subagent pass, checked and two stale claims
  fixed — see below) all updated to match what's actually built.

**Three real bugs found and fixed during this session's own live-server
verification** (KNOWN_ISSUES.md ISSUE-010/011/012 for full detail):
`bars.id` needed `Identity()` (bare `autoincrement=True` doesn't apply to
a non-primary-key column); `PgMarketDataStore` returned native `datetime`
where `MarketDataStore` always returned a string (a real 500 on
`GET /api/market-data/overview`, not a hypothetical); and
`migrate_to_timescaledb.py`'s insert-count reporting trusted an unreliable
`result.rowcount` for a multi-row `ON CONFLICT DO NOTHING` — the exact
pitfall `pg_store.py::upsert_bars` already documented and avoided one
file away. All three are exactly the kind of finding "verify against a
real server" exists to catch, not signs the underlying design was wrong.

**A subagent needed a corrective follow-up this session**, worth
recording honestly: a first attempt at the `PgTradeStore` port drifted
out of scope (built `TEAM_DEPLOYMENT.md`/`scripts/start-team.ps1`/
`tools/backup_timescaledb.py` instead of the assigned Postgres port, and
left `TEAM_DEPLOYMENT.md` describing `PgTradeStore` as already built when
it wasn't) without completing the actual assignment. A second, narrowly-
scoped pass completed the real deliverable correctly, verified
independently afterward (not just trusting its self-report) — the parity
test, live test, and Alembic chain were all re-run and inspected directly
by this session before being accepted.

**Verified, not just implemented:**
- Concurrency (the approved plan's own Verification item #4): 30
  concurrent requests (mixed `/api/system/health`,
  `/api/market-data/overview`, `POST /api/backtest/run`,
  `/api/backtests`) against the shared TimescaleDB instance via a thread
  pool — 0 errors, 0 pool exhaustion, every backtest run produced a
  distinct id (no cross-request corruption), completed in under 2s.
- Full suite with `FUTURES_BOT_DATABASE_URL` unset: 1250 passed, 29
  skipped (every team-deployment live-server test, by design), 0 failed
  — every existing single-developer setup is untouched.
- Full suite with `FUTURES_BOT_DATABASE_URL` pointed at the live
  instance: see the exact count directly above this entry once this
  session's final isolated run finishes (a same-database concurrent-run
  collision produced spurious failures on the first attempt at this —
  documented as a testing-process note, not a product bug, once
  reproduced/ruled out).
- `market_data.db`'s tables (verified last session) confirmed still
  empty/untouched by any of this session's `research.db` work.

**Deliberately not done, by design:**
- The real production `market_data.db`/`research.db` (927 MB/26 MB) has
  **not** been migrated into the live TimescaleDB instance. The approved
  plan's own Verification section calls this "a separate, operator-run
  step against real data later," not an automated part of building the
  tool — see "Recommended Next Task" below for exactly what running it
  for real involves.
- `scripts/start-team.ps1` has not been run end-to-end against a genuine
  second Tailscale-connected machine — the approved plan's own
  Verification section flags this as the one thing that fundamentally
  can't be checked from a single-machine dev sandbox.
- Mission Control's real-data wiring was not checked visually in a
  browser (Claude in Chrome unavailable this session).

2026-07-27: **Team deployment (Tailscale + centralized TimescaleDB) — IN
PROGRESS, paused waiting on a local Docker/WSL2 install.** See
"Recommended Next Task" below for the exact resume point — this section
only summarizes what already landed.

Full plan: `C:\Users\sstae\.claude\plans\polymorphic-nibbling-lamport.md`
(approved). Goal: multiple developers reach one shared backend + one
shared TimescaleDB over a private Tailscale network, no auth yet, no
public exposure. First vertical slice (`market_data.db` — chosen as the
smaller of the two databases, to prove the whole pattern end to end
before porting the larger `research.db`) is done and tested; `research.db`,
Alembic, the data-migration script, deployment scripts, the health
endpoint, Mission Control wiring, and `TEAM_DEPLOYMENT.md` are not
started yet.

**Landed, tested, real (no live Postgres needed for any of this):**
- `config.py`: new `DeploymentSettings` (`deployment.environment`:
  development/team/production, pool tuning) on `Settings`, defaulting to
  today's behavior. Documented (commented out) in `config.example.yaml`.
- `pyproject.toml`: new `db` extra (`sqlalchemy>=2.0`, `psycopg[binary]>=3.1`,
  `alembic>=1.13`) — installed in the project `.venv`.
- `src/futures_bot/db/` (new package): `engine.py` (one pooled, process-
  wide SQLAlchemy `Engine`, `pool_pre_ping` for reconnect-on-drop,
  `FUTURES_BOT_DATABASE_URL` env var — same secret-handling convention as
  `MASSIVE_API_KEY`), `health.py` (`check_database_health()`, tested
  against both "unconfigured" and "configured but genuinely unreachable"
  — the latter confirmed to fail gracefully in ~5s via `connect_timeout`,
  not hang or crash), `schema.py` (SQLAlchemy Core `Table`/`MetaData` for
  all 5 `market_data.db` tables, Postgres/TimescaleDB DDL compiled and
  verified against the real dialect, **not yet run against a live
  server**).
- `src/futures_bot/market_data/pg_store.py::PgMarketDataStore` — full
  port of `MarketDataStore`, all 23 methods, Postgres/ANSI SQL
  (`ON CONFLICT DO NOTHING`/`DO UPDATE`, native `NUMERIC`/`TIMESTAMPTZ`),
  `bars` becomes a TimescaleDB hypertable. Money/timestamp handling is a
  genuine improvement (native types, same Decimal/tz-aware semantics),
  not just a mechanical port.
- **The factory seam**, `get_market_data_store()`, added to
  `market_data/store.py` itself (**not** `api/`, despite the original
  module docstring saying "`api/market_data_store.py`-style wiring" —
  moved after discovering `market_data/scheduler.py` and
  `research_server/paper_trader.py` can't import from `api/` per
  `docs/ARCHITECTURE.md`'s one-way dependency rule; `api/market_data_store.py`
  now just re-exports it as a thin wrapper, matching `api/store.py`'s
  existing role over `TradeStore`). Every real call site — `cli.py` ×4,
  `backtest/data.py`, `market_data/scheduler.py`, `research_server/paper_trader.py`,
  `api/market_data_service.py` ×7, `api/live_session.py`, `api/services.py`
  — now goes through it instead of constructing `MarketDataStore(default_db_path())`
  directly. Unset `FUTURES_BOT_DATABASE_URL` (every existing setup) →
  byte-identical current behavior, confirmed by the full test suite
  (1226 passed) and a dedicated check.
- **One real bug found and fixed along the way**: `api/market_data_service.py`
  read `store.path` (a `Path`, SQLite-only) to build `MarketDataOverviewOut.database_path`
  — would have raised `AttributeError` the first time this ran against
  Postgres. Fixed by adding a `.location` property to both store classes
  (SQLite: same as `.path`; Postgres: the connection DSN with the
  password redacted via SQLAlchemy's own `render_as_string(hide_password=True)`,
  never a hand-rolled string edit).
- `tests/test_market_data_store_parity.py` (5 new tests): introspection-
  based check that `MarketDataStore`/`PgMarketDataStore` expose the exact
  same public method set (both directions), plus factory-branching tests
  for both the unset and set `FUTURES_BOT_DATABASE_URL` cases.
- **Mission Control frontend scaffold** (plan item #7's frontend half):
  `frontend/src/pages/MissionControl.tsx` + 7 components in
  `frontend/src/components/mission-control/` (`StatusBar`, `HealthGrid`,
  `AlertCenter`, `ActivityFeed`, `QuickActions`, `RoadmapPanel`,
  `SummaryCards`), wired in as the new index route (`App.tsx`/`Layout.tsx`
  gained a "Home" nav section, the old index `Dashboard` route moved to
  `/dashboard`). Layout and component structure are real; every value is
  placeholder data from `missionControlData.ts` (explicitly documented in
  that file's own header) until `/api/system/health` exists — per the
  plan, only `StatusBar`/`HealthGrid`'s health fields are meant to switch
  to a real `useApi(getSystemHealth)` call once that route lands; every
  other section (Research/Database/Context summaries, Activity Feed,
  Alert Center, Roadmap) stays mock by design. This was omitted from this
  section's original write-up even though it landed in the same session —
  noted here 2026-07-27 during the next session's boot-checklist
  reconciliation (CLAUDE.md §6/§8 "stop and explain a discrepancy" step).

**Not started:** `research.db`/`TradeStore`'s equivalent Postgres port
(`api/store.py::get_store()` is still 100% SQLite-only — untouched, so
research.db access is completely unaffected by any of the above), Alembic
migration setup (dependency installed, `alembic/` directory not created
yet), `tools/migrate_to_timescaledb.py`, `scripts/start-team.ps1`,
`deploy/docker-compose.yml`'s new `timescaledb` service, `/api/system/health`,
Mission Control's real-data wiring for the health fields, and
`TEAM_DEPLOYMENT.md`.

2026-07-27: **Platform Verification Phase 2 — resolved both findings from
Phase 1's audit.** No new functionality; goal was "a cleaner and faster
implementation with zero behavioral changes." Full report:
`docs/PLATFORM_VERIFICATION_PHASE2.md`.

- **Duplicate ADX/volatility computation — fixed.** `context/regime.py`'s
  `classify_regime` and `context/trend.py`'s `analyze_trend` gained
  optional `precomputed_volatility`/`precomputed_adx` parameters
  (sentinel-defaulted so every existing caller is unaffected — the
  sentinel distinguishes "not supplied, compute it yourself" from
  "supplied, and it's genuinely `None`"). `ContextEngine.build_context`
  now computes `adx()`/`analyze_volatility()` exactly once per bar (a
  new `_compute_adx` helper) and passes both through. Verified
  byte-identical, not just argued: `tests/test_platform_verification_phase2.py`
  proves a precomputed-value call produces exactly the same
  `RegimeContext`/`TrendContext`/`MarketContext` as independently
  recomputing every dimension. `cProfile` confirms the fix directly:
  800/800 `adx()`/`analyze_volatility()` calls for 800 `build_context`
  invocations, down from 1,585/1,600 before.
- **Stale `Strategy.context` — fixed defensively.** `TradingEngine.__init__`
  now resets `self.strategy.context = None` immediately at construction;
  `on_bar` now sets it unconditionally every bar (the real value or
  `None`), not only inside the `ENABLED` branch. No caller has to
  remember to clear anything. Verified by inverting Phase 1's own test
  (`TestStaleStrategyContextAcrossReusedInstancesIsResolved` — now
  asserts the bug is gone) plus a new construction-time-only test.
- **Performance re-benchmarked**, same methodology/workload as Phase 1
  (400/800/1,600 bars): per-run marginal context-generation cost
  (`OBSERVE − OFF`, environment-noise-normalized) dropped 35.6%/44.1%/
  45.6% respectively, converging toward the ~45% Phase 1's own `cProfile`
  breakdown predicted (ADX+volatility were ~90% of context-gen CPU,
  each halved). Memory delta also improved modestly (~7-10%). A live
  `futures_bot.api` background process was found contending for CPU
  mid-benchmark (confirmed via `Get-CimInstance Win32_Process`, not a
  leftover diagnostic — the user's own process, stopped by the user, not
  by this session) — numbers above are from the clean, post-stop
  measurement.
- **Regression verification:** full suite 1,221 passed, 0 failed (1,215
  + 6 new). No difference in trades, metrics, P&L, or reports — the
  existing 8-metric exact-equality backward-compatibility checks all
  still pass, and the new equivalence tests prove *why* (the underlying
  math is provably identical).
- **KNOWN_ISSUES.md**: ISSUE-008 (duplicate computation) and ISSUE-009
  (stale context) both logged and marked Resolved.

**Final recommendation: safe to proceed.** Both verified defects are
fixed and independently verified; the O(n²) full-replay cost (a known,
already-documented, by-design characteristic, not a defect) remains open
per Phase 1's Recommendation #3 — worth planning for before a
context-aware strategy runs against very long history or fast live
polling, but not blocking.

2026-07-27: **Platform Verification Phase 1 — independent audit of the
Market Context Engine's `TradingEngine` integration.** A read-only audit
(no new features, no optimizations) proving the integration is correct
and introduces zero behavioral regressions, before any context-aware
strategy is built. Full report: `docs/PLATFORM_VERIFICATION_PHASE1.md`.

- **ContextMode behavior — all PASS:** `OFF` never calls
  `ContextEngine.build_context` (spy-verified across a full backtest);
  `OBSERVE` generates exactly one `MarketContext` per bar (call count ==
  bar count, no duplicate timestamps); `OBSERVE` cannot influence
  decisions (structurally — it never executes the line that sets
  `Strategy.context` at all); `ENABLED` matches `OFF` even for a
  strategy that opts in (`uses_context=True`) but never reads
  `self.context` — the weakest, most permissive case, not just the
  default.
- **Execution flow — all PASS:** traced `on_bar`'s exact statement
  order; confirmed `list(self.bars)` always includes the bar that just
  closed (no off-by-one); stress-tested a same-bar close-then-reenter
  "flip" scenario (28+ rapid trades) with zero cross-contamination
  between a closing trade's context and a same-bar new entry's context.
- **Backward compatibility — all 8 requested metrics PASS**, exact
  equality (not tolerance-based): entry/exit timestamps, entry/exit
  prices, exit reasons, net P&L, win rate, profit factor — identical
  across a pre-integration-style call, explicit `OFF`, and explicit
  `OBSERVE`.
- **MarketContext completeness/consistency — all PASS:** every
  completed trade's `entry_context` carries all nine required
  fields (session/trend/regime/structure/volatility/liquidity/risk/
  environment score/confidence); the bare enum fields (`market_regime`,
  `volatility_state`, etc.) are verified to always agree with their
  richer nested object — a structural guarantee from
  `context_engine.py`'s construction, not incidental.
- **Performance measured, not optimized:** end-to-end backtest overhead
  is real and grows worse than linearly (400 bars: 23x; 800 bars: 102x;
  1,600 bars: 206x) — consistent with the already-known O(n)-per-call/
  O(n²)-per-replay characteristic from Phase 8. **One significant,
  previously-undiscovered inefficiency found via `cProfile`:** ADX is
  computed twice per bar (once by `regime.py`, once by `trend.py`,
  identical inputs) and volatility/ATR is also computed twice (once
  directly by `context_engine.py`, once again inside `regime.py`) —
  together ~90% of all context-generation CPU time. Not fixed (measure-
  only scope); flagged as a high-value future optimization.
- **Hidden-issue audit — all PASS except one flagged maintenance risk:**
  no silent logic errors, no look-ahead bias, no circular imports
  (subprocess-verified), no memory leaks, no new thread-safety concerns.
  **One real, reproduced finding:** reusing a single `Strategy` instance
  across two engine runs with different `ContextMode`s can leave a
  stale `self.context` from the first run visible in the second (`OFF`
  never resets it). Does not affect any current caller — every existing
  call site constructs a fresh strategy instance per run, confirmed by
  inspection — but recommended as a defensive fix before any future
  tooling reuses instances (e.g. a parameter-grid sweep helper).
- 25 new tests (`tests/test_platform_verification_phase1.py`). Full
  suite green (1215 passed, 0 failed — 1190 + 25).

**Confidence level: High.** Zero correctness defects found; two
findings (one performance, one latent-but-harmless maintenance risk)
documented with reproductions, not just described.

2026-07-27: **Market Context Engine integration into `TradingEngine`.**
Wired the (already complete, Phase 8) Context Engine into the actual
trading path — backtesting, paper trading, and live trading, all
through the same `TradingEngine`/`run_backtest`/`build_engine` — without
changing any existing trading behavior. Key pieces:

- **`engine.ContextMode`** (OFF/OBSERVE/ENABLED), a three-way switch so
  "context is generated and recorded" and "context can influence a
  decision" are two separately verifiable guarantees. `OFF` (the
  default for `TradingEngine.__init__`/`build_engine`/`run_backtest` —
  no existing caller passes anything else) is a true no-op:
  `ContextEngine.build_context` is never called. `OBSERVE` generates
  exactly one `MarketContext` per processed bar and attaches it to
  every completed trade, but never sets `Strategy.context` — decisions
  are *provably* identical to `OFF` (the strategy never sees the
  object). `ENABLED` additionally sets `Strategy.context`, but only for
  a strategy whose own `uses_context` class attribute is `True` — every
  bundled strategy defaults to `False`, so an existing, unmodified
  strategy behaves identically in every mode.
- **`Trade.entry_context`** (new, optional field, `TYPE_CHECKING`-guarded
  forward reference to avoid a real import cycle with `models.py`):
  attached in `TradingEngine._record_trade` — the single shared closing
  path for every trade regardless of *why* it closed. Neither broker
  (`PaperBroker`/`TradovateBroker`) ever sets it or knows about
  `context/` at all.
- **`Strategy.context`/`uses_context`** (new, optional attributes on the
  base `Strategy` class, `TYPE_CHECKING`-only reference to `context/`):
  `Strategy.on_bar`'s call signature is completely unchanged — no
  existing strategy needed a single line of modification.
- **`backtest/context_comparison.py`**: `compare_context_impact` runs
  the same strategy factory/settings/bars through `OBSERVE` (baseline)
  and `ENABLED` (may differ) via the *same* `run_backtest` (no
  duplicate pipeline), diffs the trade lists
  (`UNCHANGED`/`REMOVED_BY_CONTEXT`/`ADDED_BY_CONTEXT`/
  `ENTERED_DIFFERENTLY`/`EXITED_DIFFERENTLY`), and attaches the
  `MarketContext`/`EnvironmentScore` that explains each change. Metrics
  (net profit, win rate, profit factor, expectancy, max drawdown, trade
  counts, average/largest winner/loser) come straight off the existing
  `BacktestMetrics` — nothing recomputed.
- **Two real bugs found and fixed during this integration's own manual
  verification** (not just via the tests written alongside it):
  (1) `TradingEngine.bars` is a bounded `deque`, which doesn't support
  the slice indexing `liquidity.py`/`volatility.py` rely on for their
  trailing windows -- fixed by converting to `list` once per bar before
  calling `ContextEngine.build_context`. (2) `dataclasses.replace`
  (needed to attach `entry_context` to a frozen `Trade`) returns a
  *new* object -- the first draft discarded it the moment
  `_record_trade` returned, so `PaperBroker.trades` (what
  `backtest.runner.run_backtest` actually reads to build
  `BacktestMetrics.trades`) still held the original, un-enriched trade.
  Fixed by writing the enriched trade back into
  `self.broker.trades[-1]`.
- 27 net new tests: 18 in `tests/test_engine_context_integration.py` +
  8 in `tests/test_backtest_context_comparison.py`, plus one net
  addition in `tests/test_context_engine_validation.py` where an
  obsolete "context/ is not integrated yet" boundary test was replaced
  with two tests checking the *actual* current invariant (risk/brokers
  still have zero reference; `engine.py`'s reference is gated by
  `ContextMode.OFF`'s default). Two similarly obsolete tests in
  `tests/test_context.py` were also rewritten (net zero count change)
  for the same reason. Full suite green (1190 passed, 0 failed).
  Verified directly, not just asserted: `OFF` mode produces
  byte-identical trades to a pre-integration-style backtest call;
  `OBSERVE` produces decisions identical to `OFF`; `ENABLED` produces
  decisions identical to `OFF`/`OBSERVE` for any strategy that hasn't
  opted in.

Docs updated: `CLAUDE.md`, `PROJECT_STATE.md`, `ROADMAP.md`,
`docs/ARCHITECTURE.md` (new "Integration into `TradingEngine`"
subsection), `CHANGELOG.md`.

2026-07-27: **Market Context Engine Phase 8 — completion and
validation** (11-part phase; the engine is now considered
production-ready as an independent, unintegrated subsystem):

- **Part 1 — the final three dimensions, real:** `context/trend.py`
  (standalone `TrendState`, reusing `research.regime.classify_trend` +
  `regime.py`'s ADX confidence constants — independent of
  `regime.py`'s volatility-coupled composite, available with far less
  history); `context/liquidity.py` (`LiquidityState` from relative
  volume, reusing `strategy.indicators.sma` — genuinely new
  classification logic, documented why); `context/risk.py`
  (`RiskState` as a pure composite of already-real `volatility_state`/
  `market_regime`, exactly as this method's own Phase-1 stub docstring
  anticipated — no new market-data analysis at all). Wired into
  `MarketContext` (new `trend_context`/`liquidity_context`/
  `risk_context` fields). 50 new tests
  (`test_context_trend.py`/`test_context_liquidity.py`/
  `test_context_risk.py`).
- **Part 2 — configurable scoring:** `scoring.ScoringConfig`
  centralizes all six weights (previously hardcoded module constants);
  `DEFAULT_SCORING_CONFIG` reproduces every pre-Phase-8 scoring test
  exactly (verified — zero test changes needed for the 21 pre-existing
  scoring tests to keep passing). `ContextEngine.__init__` gained an
  optional `scoring_config` parameter. 9 new tests.
- **Part 3 — engine validation:** `tests/test_context_engine_validation.py`
  (16 tests) encodes no-circular-imports, no-duplicated-logic (ATR/ADX/
  SMA/classify_trend each defined exactly once), no-duplicated-
  calendars/enums, module independence from `risk.manager`/`brokers`/
  `engine.py`, determinism, missing-data safety, `UNKNOWN` correctness,
  and confidence validity as lasting, executable checks. Found and
  fixed a real bug **in the validation test itself**: an early draft
  used `importlib.reload()` to check standalone-importability, which
  mints duplicate Enum class objects and broke `is`-identity for every
  later test in the same process — replaced with genuine subprocess
  isolation.
- **Part 4 — look-ahead bias audit:** `docs/CONTEXT_ENGINE_LOOKAHEAD_AUDIT.md`
  — explicit module-by-module reasoning for all 8 dimensions plus the
  combined score. No issues found; every module was already built
  under a "trailing-window-only" / "confirmation lag ≠ leakage" / "no
  bars read at all" discipline, each with its own dedicated test.
- **Part 5 — performance benchmark:** `tools/benchmark_context_engine.py`
  measured avg/worst-case timing and peak memory across 50–50,000 bars
  (results in `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`). Found
  and fixed one real inefficiency: `liquidity.py` was converting every
  bar's volume to `Decimal` even though only the trailing `lookback`
  bars are ever used — fixed to slice first, verified output-identical.
  ATR/ADX-based dimensions remain O(n) by necessity (Wilder's smoothing
  is seed-sensitive to truncation) — documented, not silently
  approximated.
- **Part 6 — context analytics:** `context/analytics.py`'s
  `analyze_context_batch`/`ContextAnalyticsReport` — session/regime/
  volatility/trend/liquidity/risk distributions, environment-score and
  confidence numeric summaries, and UNKNOWN-frequency-per-dimension
  over a batch of already-built contexts. Dev/research tool, no UI, not
  wired into `ContextEngine`. 12 new tests.
- **Part 7 — coverage report:** `docs/CONTEXT_ENGINE_COVERAGE.md` — a
  table of every dimension's status/test-count/confidence-model/
  dependencies/integration-readiness.
- **Part 8 — architecture review:** `docs/CONTEXT_ENGINE_ARCHITECTURE_REVIEW.md`
  — confirmed via `git status`/`git diff` and source-inspection tests
  that zero files outside `context/`/`tests/test_context*.py`/`docs/`/
  `tools/benchmark_context_engine.py` changed across this entire
  multi-phase effort, and that nothing outside `context/` references it
  either (checked in both directions).
- **Part 9 — testing:** 89 new tests this phase (1074 → 1163), full
  suite green throughout every part. One backward-compatibility edge
  case discovered and covered: `MarketContext.from_dict` correctly
  handles a dict shaped like an earlier phase's output (missing the
  newer `trend_context`/`liquidity_context`/`risk_context`/
  `environment_score` keys entirely, not just null) — new regression
  test in `test_context.py`.
- **Part 10 — documentation:** `CLAUDE.md`, `PROJECT_STATE.md`,
  `ROADMAP.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md` all updated;
  four new dedicated docs (coverage/lookahead-audit/performance-
  benchmark/architecture-review) plus the benchmark tool.

**Not done, deliberately:** no integration into `TradingEngine`,
`Strategy`, `RiskEngine`, backtesting, live trading, or broker code —
explicitly out of scope for this phase, needs its own explicit
approval per CLAUDE.md section 8.

2026-07-27: implemented the Context Scoring System
(`src/futures_bot/context/scoring.py`,
`score_environment`/`EnvironmentScore`) — combines every existing
`MarketContext` dimension into a single 0-100 "Market Environment
Score": how favorable current conditions look for a systematic strategy
to operate in *generally* (clear trend, normal volatility, a liquid
session, confirmed structure, ample liquidity, manageable risk) --
**not a directional signal, information only, per the task's own
instructions**: `EnvironmentScore` carries no broker/risk-manager/
engine reference of any kind, verified by a test inspecting the
module's own imports. Six dimensions each contribute a signed value
scaled by a documented maximum weight (Trend 20, Volatility 15, Session
10, Structure 20, Liquidity 15, Risk -10 -- chosen to reproduce the
task's own worked example exactly: `20+15+10+20+15-10 == 70`,
manually verified against the live module before trusting it as a test
assertion); the total is clamped to `[0, 100]`. A dimension with no
data contributes exactly `0.0` and is excluded from both the `reasons`
explanation list and the `confidence` fraction. Since
`liquidity_state`/`risk_state` are still `UNKNOWN` stubs everywhere
else in this codebase, they always contribute `0.0` through
`ContextEngine` today -- a real, already-exercised "missing data"
case, covered by a test asserting today's practical score ceiling (65,
not 100). `confidence` is the fraction of the six dimensions that
actually had data, independent of the score's own value (a test
constructs a "full data, worst possible readings" scenario to prove
this explicitly). Wired into `MarketContext` (new `environment_score`
field, always populated by `ContextEngine.build_context` via a
two-step construction: the base `MarketContext(...)` call, then
`scoring.with_environment_score` -- a `dataclasses.replace` -- since
the score depends on every other field already being set). 20 new
tests (`tests/test_context_scoring.py`, covering the exact worked
example, the reason-phrasing example, confidence aggregation,
clamping at both ends, missing-data handling, full `MarketContext`
integration, and the information-only import-boundary check). Full
suite green (1074 passed, 0 failed, 20 new). Trend (standalone),
liquidity, and risk remain stubs -- out of scope this phase.

2026-07-27: implemented Market Structure Context
(`src/futures_bot/context/structure.py`,
`analyze_structure`/`StructureContext`) — detects price structure from
confirmed swing points: higher-highs/higher-lows (`TrendState.BULLISH`
structure) or lower-highs/lower-lows (`TrendState.BEARISH`), nearest
support/resistance levels around the current price, and distance from
them. Strictly descriptive: `StructureContext` carries no broker/risk-
manager/engine reference of any kind, never generates a trade, never
overrides a strategy's own signal. No existing equivalent to reuse in
this codebase (genuinely new work, same disclosure `regime.py` gives
for liquidity/risk), though it reuses `TrendState` rather than a fourth
bullish/bearish/neutral vocabulary. A swing point is confirmed via a
standard fractal definition (a bar's high/low strictly beats every
high/low within `DEFAULT_SWING_WINDOW`=3 bars on both sides); this
requires bars chronologically after a candidate swing, but since every
bar this module sees is already-completed history, that's confirmation
lag, not a look-ahead violation -- documented explicitly since "avoid
future leakage" has been the standing theme of every prior phase, and
verified by a dedicated test that the most recent bars simply have no
confirmed swing yet. Caught and fixed a real test-data bug during
manual verification (not just via the tests written alongside it): an
early zigzag-fixture generator produced *rising* cycle lows regardless
of the intended drift direction, which would have silently mislabeled a
"downtrend" fixture as bullish. Wired into `MarketContext` (new
`structure_context` field, `ContextEngine._classify_structure` now
real). 17 new tests (`tests/test_context_structure.py`, covering
higher-highs/higher-lows, lower-highs/lower-lows, support/resistance
bracketing, the task's own worked example shape, a flat/no-structure
case, missing data, confirmation-lag-is-not-leakage, and an explicit
check that the module imports nothing from `risk.manager`/`brokers`/
`engine`). Full suite green (1054 passed, 0 failed, 17 new). Trend
(standalone), liquidity, and risk remain stubs — out of scope this
phase.

2026-07-27: implemented Multi-Timeframe Context
(`src/futures_bot/context/timeframe.py`,
`classify_timeframe_alignment`/`TimeframeAlignment`) — combines trend
direction across five canonical timeframes (`1m`/`5m`/`15m`/`1h`/`1d`)
into one alignment reading: a dict of timeframe -> `TrendState` for
whichever timeframes had data, plus `alignment_score` (the magnitude,
`[0.0, 1.0]`, of a rank-weighted average direction). Reuses
`research.regime.classify_trend` per timeframe -- the same function
`regime.py` already uses for its own trend signal -- rather than a
second trend definition; its "sideways" maps onto `TrendState.NEUTRAL`
(previously unused outside the still-stubbed `trend_state` field).
Look-ahead safety here is stricter than any single-stream classifier so
far: a coarser timeframe's last bar can look "at or before now" by
timestamp alone while still being in-progress (e.g. a 1-hour bar
opened at 09:00 hasn't closed by 09:05), so this module tracks each
timeframe's actual duration and only keeps a bar once its close time
has genuinely passed -- verified by a dedicated test constructing
exactly that scenario. Missing/short timeframe data is handled safely:
omitted, empty, or under-2-bar timeframes are simply left out of
`alignment`, never an error or a fabricated direction. Wired into
`MarketContext` (new `timeframe_alignment` field; `ContextEngine.build_context`
gained an optional `bars_by_timeframe` parameter, independent of its
existing `bars`/`self.timeframe`). 14 new tests
(`tests/test_context_timeframe.py`, covering the task's own worked
example shape, full agreement, an even bullish/bearish split, missing
data in several forms, the in-progress-bar leakage scenario, and
serialization). Full suite green (1037 passed, 0 failed, 14 new). Trend
(standalone), liquidity, and risk remain stubs — out of scope this
phase.

2026-07-27: implemented Market Regime Detection
(`src/futures_bot/context/regime.py`, `classify_regime`/`RegimeContext`) —
classifies overall market behavior into one of five mutually exclusive
`MarketRegime` values: `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`,
`HIGH_VOLATILITY`, `LOW_VOLATILITY`. Redefined `MarketRegime` from Phase
1's placeholder set (`TRENDING`/`RANGING`/`VOLATILE`) to this exact
taxonomy — confirmed zero usages outside `context/`'s own tests before
changing it (same discipline as `SessionPhase`'s Phase 2a rename).
Combines three reused signals rather than re-deriving any of them:
`strategy.indicators.adx` for trend strength (conventional ADX >= 25
"actually trending" threshold), `research.regime.classify_trend` for
trend direction (bullish/bearish/sideways, already look-ahead-safe and
already used for this purpose elsewhere), and this package's own
`volatility.analyze_volatility` for the volatility signal. Priority when
signals disagree is explicit: extreme volatility dominates trend/range
labeling; otherwise a strong, directional ADX reading wins; otherwise
low volatility is its own label; otherwise the default is `RANGING`.
`confidence` is always `[0.0, 1.0]` via a small documented formula per
branch (trending confidence `min(1.0, adx/50.0)` — matches the task's
own worked example, ADX 39 -> 0.78 exactly). No parameter optimization
this phase — every threshold is either reused from elsewhere in this
codebase or an unmodified textbook default (ADX 25). Wired into
`MarketContext` (new `regime_context` field, `market_regime` now real,
confidence recorded only once `classify_regime` produced a non-`UNKNOWN`
reading). 21 new tests (`tests/test_context_regime.py`, covering
trending-up/down, ranging, high/low volatility, extreme-volatility
priority over a concurrent trend, confidence-formula correctness,
missing/partial data, and a dedicated no-future-leakage test). Also
updated 6 pre-existing `tests/test_context.py` assertions/renamed 2
tests that referenced the old `MarketRegime` member names or assumed
`market_regime` was still an unconditional stub. Full suite green (1023
passed, 0 failed, 21 new). Trend/liquidity/risk remain stubs — out of
scope this phase.

2026-07-27: implemented Volatility Context (`src/futures_bot/context/volatility.py`,
`analyze_volatility`/`VolatilityContext`) — real ATR-ratio-based volatility
classification (`VolatilityState`: LOW/NORMAL/HIGH/EXTREME/UNKNOWN,
unchanged since Phase 1). Reuses `strategy.indicators.atr_series` (the same
Wilder's-smoothing ATR every strategy already uses) rather than
re-deriving true-range math; `current_atr` is the series' last value,
`average_atr` is the mean of a trailing window ending at that same value
(default lookback 20), `volatility_ratio = current_atr / average_atr`
classified via fixed, documented thresholds (matches the task's own
worked example: ratio 1.5 → HIGH). Also computes `realized_volatility`
(stdev of simple returns over the same window, unannualized — new, no
prior equivalent). Deliberately did **not** reuse
`research/regime.py`'s `classify_volatility`/`compute_regimes` as-is:
its tercile cutoffs are computed via `sorted()` over the *entire* bars
series up front, which is correct for its own post-hoc trade-labeling
use case but not look-ahead-safe for real-time "as of timestamp T"
classification — `analyze_volatility` only ever reads a trailing window
ending at the last bar it's given, verified directly by a dedicated
no-look-ahead test (`tests/test_context_volatility.py`'s
`TestNoFutureDataLeakage`: a truncated-history reading is provably
unaffected by bars appended after it). Wired into `MarketContext` (new
`volatility_context` field, `ContextEngine._classify_volatility` now
real, confidence recorded only once enough history exists to compute a
ratio). 22 new tests (`tests/test_context_volatility.py`, covering low-vol,
high-vol, missing/insufficient data, no-future-leakage, serialization,
and multi-symbol/timeframe support). Full suite green (1002 passed, 0
failed, 22 new). The other four `MarketContext` dimensions
(regime/trend/liquidity/risk) remain stubs — out of scope this phase.

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

**The real production data migration is done, Team Mode's core networking
bug is fixed, and the Team Collaboration Platform now covers the MVP
(accounts + Active Work Registry), SIL Phase 2 "Workflow Integration"**
(lifecycle, Overlap Engine V2, live git branch info, merge readiness,
activity timeline, the work-item CLI, and Mission Control's
`CollaborationWorkspace`), **User Registration & Organization
Management** (api_key, profile fields, permissions table, org-scoped
work items, the Welcome/Register/Profile/Organization Settings/Team
Members pages, and a frontend-only session concept), **and SIL Phase 4
"Intelligent Automation Layer"** (AI context bundle, draft work items +
background git-watcher, local validation command, documentation draft
assistant, automation status + Mission Control panel, maintenance job —
opt-in via `automation.enabled`, default off) — see "Last Completed
Work" above. What's left is a mix of genuine operator/human actions and
real follow-on feature work:

1. **Create the Windows Firewall rule for real** (or click the UAC prompt
   next time `scripts\start-team.ps1` runs unelevated). One-time, from an
   elevated PowerShell:
   `New-NetFirewallRule -DisplayName "futures-bot Team Mode (8000/tcp, Tailscale)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -RemoteAddress 100.64.0.0/10 -Profile Any`.
   Once it exists, every future `start-team.ps1` run detects it and skips
   straight past this step.
2. **Get a second machine on the tailnet and actually connect it** — a
   peer (`rafaelballer`) is already registered but was offline all
   session; this is the one thing that genuinely can't be verified from
   a single machine. Follow `TEAM_DEPLOYMENT.md`'s "Connecting a new
   developer" section, then confirm it can reach
   `http://100.91.121.119:8000` (or whatever this machine's Tailscale IP
   is) for both the API and the built dashboard.
3. **Visually verify Mission Control and the new onboarding flow in a
   browser** — Claude in Chrome was unavailable all session (connection
   failed/disabled); typecheck/lint/vitest are all clean (121 frontend
   tests passing) and the underlying API data was verified directly, but
   the actual rendered pages (Welcome, Register's 3 steps,
   `CollaborationWorkspace`'s 7 tabs, Profile, Organization Settings,
   Team Members, and — new — Mission Control's `AutomationPanel`)
   haven't been looked at. Run `/chrome` to reconnect, or look yourself
   at `http://127.0.0.1:5173` (local mode) — a fresh browser profile (no
   `localStorage`) will land on `/welcome` first. To actually see
   `AutomationPanel` doing something (not just "not enabled"), set
   `automation.enabled: true` in `config.yaml` first.
4. **Decide whether/how real authentication gets built.** Accounts
   (`accounts/`), the Active Work Registry (`collaboration/`), and the
   frontend's `session.tsx` are all deliberately built with no login/
   password/token — every route is reachable by anyone who can reach the
   port, and "the current user" is just whatever id a browser has in
   `localStorage`. A real auth layer (validating the auto-generated
   `api_key` as a bearer token would be the smallest next step, given it
   already exists) is the natural next move before this is used by more
   than one trusted person on a private tailnet.
5. **Consider the remaining Collaboration Platform follow-on work** — a
   true architecture/dependency graph (Overlap V2's import/route/table
   signals are real but shallow, not a persisted graph of indirect
   impact), semantic merge assistance beyond a risk score
   (duplicate-implementation detection, AI-assisted conflict
   resolution), real CI integration (`merge_readiness.py`'s `test_status`
   is deliberately always `"unknown"`), persistent AI worker processes
   (SIL Phase 4's git-watcher added automatic *detection* of
   unregistered work — nothing yet runs the work itself, a draft is only
   ever approved/discarded by a human or an AI-assisted session), and a
   distributed compute cluster — each substantially larger than what
   exists now, deliberately not started; `collaboration/` is built so
   they can layer on without a redesign. See `docs/ARCHITECTURE.md`'s
   "Team Collaboration Platform"/"SIL Phase 4" sections and
   `ROADMAP.md`'s "Future" section.
6. **Fix `frontend/src/format.ts::dateTime()`'s timestamp-parsing bug**
   (KNOWN_ISSUES.md ISSUE-021) — affects every page rendering a
   local-mode (SQLite) timestamp, deferred because it touches many call
   sites and deserves its own focused verification pass.
7. **Continue the Stabilization Mode sweep, if more time is available.**
   Not yet covered as thoroughly: the database layer (migrations/indexes/
   transactions beyond what recent fixes already touched), a systematic
   dead-code/duplicate-logic sweep, and POST/PUT routes with real
   payloads (only GET routes were swept in the sweep that found ISSUE-017).

Otherwise: `KNOWN_ISSUES.md` ISSUE-004 (schema migration, needs explicit
approval per CLAUDE.md section 8), ISSUE-005 (US80Z source-data
correction), deciding a rotation/archival policy for `logs/decisions.jsonl`
(ISSUE-017 made reading it cheap regardless of size, but it still grows
forever), a Python formatter/linter, CI setup, or the High-priority
roadmap items (walk-forward testing, Monte Carlo, parameter robustness).

---

The Market Context Engine is now complete, validated, integrated into
`TradingEngine`, independently audited (Platform Verification Phase 1,
2026-07-27), and **both findings from that audit are now fixed and
independently verified** (Platform Verification Phase 2, 2026-07-27 —
`docs/PLATFORM_VERIFICATION_PHASE2.md`): the ADX/volatility duplicate
computation is eliminated (`cProfile`-confirmed, ~35-46% per-run cost
reduction) and the stale-`Strategy.context` gap is closed defensively.
No further platform-hardening work is required before the first
context-aware strategy is built. The one remaining, non-blocking item on
record is the O(n²) full-replay cost (a known, by-design characteristic,
not a defect — Phase 1's Recommendation #3: pass a bounded trailing
window rather than full history, already fully supported by every
classifier's own signature, just not yet adopted by any caller) — worth
planning for before running a context-aware strategy against very long
history or a fast live-polling loop. Otherwise, the next decision is
**per-strategy adoption**: whether/how
any *specific* bundled strategy (`ema_crossover`,
`opening_range_breakout`, `vwap_reversion`, `trend_pullback`) should
actually set `uses_context = True` and consult `self.context` to change
its own decisions — a strategy-level design choice for each strategy
individually, not something to decide globally; use
`backtest/context_comparison.py`'s `compare_context_impact` to evaluate
any such change before adopting it. Also open: whether
`MarketContext`/`EnvironmentScore` snapshots should be persisted for
research (a database schema change, needs explicit approval per
CLAUDE.md section 8). Otherwise: decide whether to fix `kill-vite.js`'s self-kill bug directly (would
fix manual `npm run dev` too, but is a change to existing frontend
code) or leave `scripts\start.ps1`'s workaround as the standing
solution. Also open: ISSUE-004 (schema migration, needs explicit
approval per CLAUDE.md section 8) and ISSUE-005 (US80Z source-data
correction). Otherwise: CI setup (tests currently only run by hand), a
Python formatter/linter, or the High-priority roadmap items
(walk-forward testing, Monte Carlo, parameter robustness).
