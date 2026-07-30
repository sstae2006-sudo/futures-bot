# CHANGELOG.md

Every session appends an entry here. Don't edit past entries except to
mark something resolved with a date/commit — this is a history, not a
scratchpad.

## 2026-07-29 — Fix: Mission Control's fake data (KNOWN_ISSUES.md ISSUE-040)

Found during "SIL Research Engine 2.0 Phase 1"'s audit: Mission Control's
`ResearchSummaryCard`/`DatabaseSummaryCard`/`MarketContextSummaryCard`/
`PerformanceCard`/`HealthGrid`/`ActivityFeed`/`AlertCenter`/`RoadmapPanel`
were all rendering hardcoded placeholder numbers from a "design/scaffold
pass" (`missionControlData.ts`) -- a fake profit factor of 1.42, a fake
$18.6 expectancy, a fake "187 completed backtests," a frozen boot-sequence
activity feed, a fake "94% operational" health score -- sitting on the
same live page as real, API-backed panels with no visual distinction.
`StatusBar.tsx` (global chrome, every page) had the same problem
independently: `GIT_BRANCH`/`GIT_COMMIT` frozen to one specific past
commit.

**Fixed, one panel at a time, always toward a real API or an honest
removal, never a different fake:**
- `ResearchSummaryCard` -- extended `GET /api/system/overview` with five
  new fields computed server-side in `api/services.py::system_overview`
  (`avg_profit_factor`/`avg_expectancy`/`best_strategy`/`latest_backtest_*`)
  from real completed backtest runs -- plain means over each run's own
  already-persisted, engine-computed values, never a re-derived formula.
  `None` when there's no data yet, never a fabricated default.
- `DatabaseSummaryCard` -- wired to the already-real `GET /api/market-data/overview`.
- `MarketContextSummaryCard`/`PerformanceCard` -- **removed**, not
  patched: Context Engine has no live "current regime" to report (OFF by
  default), and `PerformanceCard` was a fake duplicate of the already-real
  `InfrastructurePanel` right next to it.
- `HealthGrid` -- rebuilt from only genuinely checkable real signals
  (system health, system overview, research-server status, live-session
  status); components with no real check available were dropped, not
  faked. The fake 94% score became a real roll-up of the actual fetched
  cards' tones.
- `ActivityFeed` -- wired to the already-built, real
  `GET /api/activity/timeline` instead of a frozen boot-sequence snapshot.
- `AlertCenter` -- derives real alerts from automation schedulers'
  `last_error` fields and real team-database reachability -- pure
  display-layer relabeling of already-computed status, no new alerting
  backend built.
- `RoadmapPanel` -- kept as explicitly-labeled **static reference**
  content (a "static reference" tag added) rather than faked further;
  the fake completion-percentage bar was dropped.
- `StatusBar` -- branch/commit now come from the real, already-built
  `GET /api/git/branch-info`; the fake "Last Startup" row was dropped
  (the already-real Uptime field says the same thing honestly).

20 new tests (9 backend, using real backtest runs through the actual
engine, not synthetic mocks; 11 frontend, asserting the old fake
literals never appear and the new real ones do). Full suite green: 1669
backend (1660 -> 1669, 0 failed), 145 frontend (134 -> 145, 0 failed),
`tsc -b`/`oxlint` clean. See `KNOWN_ISSUES.md` ISSUE-040 for full detail,
including one small out-of-scope type-completeness gap noted for later
(`MaintenanceStatus`'s TS type missing two fields the backend already has).

## 2026-07-29 — Fix Team Mode boot failure + document PowerShell env-var setup

Root cause of a recurring "`FUTURES_BOT_DATABASE_URL` is not set in this
shell" failure on `scripts\start-team.ps1`: the variable was only ever
being set transiently in whatever PowerShell window it was typed into
(the running `futures-bot-timescaledb` Docker container itself was fine
the whole time — verified via `check_database_health()`, 293ms latency).
Fixed by setting it as a persistent Windows User environment variable
(`[System.Environment]::SetEnvironmentVariable(..., 'User')`) so every
future PowerShell window has it automatically, not just the one it was
set in.

`TEAM_DEPLOYMENT.md` updated: every bash `export FUTURES_BOT_DATABASE_URL=...`
example now has a PowerShell equivalent alongside it (the doc was
bash-only despite this project's own dev environment being Windows — a
real, direct contributor to the confusion), a new "Setting
`FUTURES_BOT_DATABASE_URL` on Windows" section explains the transient-
vs-persistent distinction up front, and a new Troubleshooting entry
covers this exact failure mode directly.

## 2026-07-29 — Fix: Integration Queue was O(N^2) in queue size (ISSUE-039)

Prompted by a direct question about whether the newly-shipped Milestone
2 pipeline was fast. Measured it rather than guessing, via `TestClient`
against real repo files: `GET /api/integration/queue` took ~145ms at 1
queued item, but ~5.6s at 20 items and **~23s at 50 items** — not
linear, quadratic. Root cause: each queued item's merge-readiness was
computed via a fully independent call that separately re-fetched the
entire active-item set from the database and re-read/re-parsed every
other active item's files from scratch (no caching within or across
calls) — for N items, roughly N x N file re-parses. `generate_integration_review`
had a smaller, 2x (not N^2) instance of the same pattern: it computed
Overlap Engine V2 twice on identical inputs.

**Fixed**, without changing any item's computed score (confirmed via
the existing golden-equivalence test plus 6 new regression tests
directly counting file reads): `overlap_v2.analyze_files`/
`compute_overlap_v2` gained an optional, purely-additive `file_cache`
parameter that memoizes each file's parse per request;
`get_integration_queue` now fetches the active set once and shares one
cache across every item's readiness computation instead of each item
repeating both from scratch; `generate_integration_review` now reuses
the overlap warnings `merge_readiness` already computed (via a new
`OverlapWarningLike` structural-typing `Protocol` in
`conflict_resolution.py`) instead of recomputing them. Re-measured
after the fix: **50 items now takes ~6.3s (was ~23s), and the curve is
roughly linear again, not quadratic.**

**Not fully solved, documented as a known remaining cost:** ~120ms/item
at scale is now dominated by `git` subprocess spawns for branch info
(still one per queued item, inherent since each item can reference a
different branch) — this degrades gracefully now instead of exploding,
but isn't eliminated. See `KNOWN_ISSUES.md` ISSUE-039 for full detail
and the before/after numbers.

6 new tests (`tests/test_collaboration_overlap_v2.py::TestAnalyzeFilesCache`,
two file-read-count regression tests in `tests/test_api_collaboration_routes.py`).
Full suite verified green.

## 2026-07-29 — SIL Phase 6 "Integration Coordinator" Milestone 2: Intelligent Review Pipeline

Builds directly on Milestone 1 (below) without redesigning the Worker
Registry or Integration Queue. Full design in `docs/ARCHITECTURE.md`'s
"SIL Phase 6" section (Milestone 2 subsection); this is the summary.

**Added — persistent Integration Reviews.** A new `integration_reviews`
table (`collaboration/store.py`/`pg_store.py`, Alembic `b91531ca7583`),
generated via `POST`/`GET /api/work-items/{id}/reviews`. APPEND-ONLY --
`create_integration_review` is the only writer, no update/delete path
exists -- a deliberate, one-off exception to this package's usual
"compute live, never persist" rule (`merge_readiness`/`overlap_v2`/
`context_bundle`/worker staleness are all still computed fresh, never
cached): the point of a review is a permanent historical record so
confidence can be seen trending over time, not just its current value.
Wires together only existing primitives, no new scoring model:
`merge_readiness.py` (overlap + branch info), a new Conflict Resolution
Assistant (`collaboration/conflict_resolution.py` -- subsystem impact +
a templated suggested resolution/integration order layered on Overlap
Engine V2's existing scoring, never an automated merge action), a
minimal subsystem lookup (`collaboration/architecture_map.py` -- a
hand-curated path-prefix table, explicitly NOT a real architecture/
dependency graph; SIL Phase 5's "Trading Intelligence Layer" was
proposed, never built, and this milestone is honest about that gap
rather than pretending otherwise), and a shared Validation Planning
heuristic (`collaboration/validation_planning.py`, extracted unchanged
from `tools/local_validate.py` so the CLI and this new feature share one
file->test mapping instead of two independently-drifting copies). Every
generated review also logs `review_generated` (and
`integration_recommended` when `level == "ready"`) into the existing
`work_item_activity` log -- reuses the existing timeline, no second
mechanism.

**Added — stale-worker cleanup.** `collaboration/maintenance.py::_mark_stale_workers_offline`
closes the one write path Milestone 1 deliberately deferred (staleness
was read-only, computed live at request time) -- a worker whose
`last_heartbeat_at` has gone stale is now flipped to `status='offline'`
via a new `store.set_worker_status`, which deliberately does NOT touch
`last_heartbeat_at` (marking a worker offline must not itself look like
a fresh heartbeat, or it would immediately un-stale itself on the next
cycle). Fully reversible -- a worker's row is never deleted, and its
very next real heartbeat brings it back online. `WORKER_STALE_AFTER_SECONDS`/
`is_worker_stale` moved from `api/worker_service.py` into
`collaboration/__init__.py` so both the API layer and `maintenance.py`
read one shared definition (`collaboration/` must not import from
`api/`, so the shared home has to live in `collaboration/`).

**Added — Mission Control.** `IntegrationQueuePanel.tsx`'s existing row
expansion (not a new panel -- "avoid duplicate dashboards" was explicit
in this milestone's own spec) gained a "Generate Integration Review"
button, plus, once at least one review exists: its summary,
recommendation, affected subsystems, validation recommendation, each
conflict's suggested resolution, and a bare confidence-trend string
across review history.

**Fixed as part of this work (not a separate bug-hunt finding, an
in-flight refactor):** `tools/local_validate.py`'s file->test mapping
logic (`_candidate_test_globs`/`_matching_tests`/`_plan`) is now
`collaboration/validation_planning.py`'s `candidate_test_globs`/
`matching_tests`/`plan_validation`, imported by `local_validate.py`
rather than duplicated -- behavior is unchanged, this is purely so the
Integration Review's "Validation Planning" phase reuses the same
heuristic instead of a second, independently-drifting copy.

**Reliability pass** (Milestone 2's own required bug hunt): concurrent
review creation (20 threads, one work item, append-only inserts --
verified no crash/lost row), duplicate/concurrent worker registration
(already covered by Milestone 1's existing test, re-verified), stale
worker cleanup (idempotent across repeated runs, never re-triggers for
an already-`offline` worker, never touches `last_heartbeat_at`, fully
recoverable on the next real heartbeat), activity-timeline ordering
(`review_generated` appears correctly in `GET /api/activity/timeline`),
architecture reporting and validation recommendations (unit-tested
directly against the new modules). No new bugs found requiring a
`KNOWN_ISSUES.md` entry -- every piece behaved as designed on first
correct implementation, unlike Milestone 1's mid-implementation
worker-type correction.

**Explicitly deferred, documented in `ROADMAP.md`'s Future section as
Milestone 3+, not built now**: a real, trend-based Confidence Dashboard
(M3 -- Milestone 2's bare confidence-trend string is the seed, not the
full version); a Conflict Heat Map + Pending Approvals rollup (M4);
Coordinator Memory (M5, still not enough accumulated review history to
build from yet); an Integration Branch git workflow (M6, needs separate
explicit human sign-off); Rollback Intelligence (M7, likely depends on
M6); a real Architecture Model/Trading Intelligence Layer replacing
`architecture_map.py`'s static lookup with a genuine dependency graph
(a distinct, larger, not-yet-scoped effort). Folding worker staleness
into `merge_readiness.py`'s own factor list (from Milestone 1's original
M2 sketch) also remains undone -- Milestone 2's actual spec didn't
separately call for it; noted in `ROADMAP.md` as still open.

60 new tests (55 backend -- three new-module unit-test files
(`test_architecture_map.py`, `test_conflict_resolution.py`,
`test_validation_planning.py`), store/API/maintenance additions
including a concurrent-review-creation test and five
stale-worker-cleanup tests; 5 frontend). Full suite verified: 1654
backend tests (1599 -> 1654 passed, 51 skipped, 0 failed), 134 frontend
tests (129 -> 134, 0 failed), `tsc -b`/`oxlint` both clean (no new
warnings).

## 2026-07-29 — SIL Phase 6 "Integration Coordinator" Milestone 1: Worker Registry + Integration Queue

A large, multi-part spec asking to "transform SIL into a centralized
engineering coordination platform." Scoped deliberately (a design-review
subagent read every relevant file directly and pushed back on several
framings before implementation started) into a buildable foundation:
wire together primitives that already exist rather than invent new
intelligence, and defer the spec's much larger, more speculative pieces
to an explicit, documented Milestone 2+ list rather than build them
speculatively. Full design in `docs/ARCHITECTURE.md`'s "SIL Phase 6"
section; this is the summary.

**Added — Worker Registry.** A `workers` table
(`collaboration/store.py`/`pg_store.py`, Alembic `6794ec903e7c`) --
extends the existing dual-backend `CollaborationStore` rather than a new
store module (workers join constantly against `work_items` for the
Integration Queue). Generic by design: `WORKER_TYPES` (human developer,
Claude Code session, other/future AI agent, validation worker, research
worker, background service, future distributed compute worker) is
deliberately NOT `OWNER_TYPES` ("human"/"ai" only) -- this registry must
not hardcode an assumption that only Claude Code will ever connect.
`POST /api/workers/{id}/heartbeat` is an upsert with a caller-supplied
id (not `touch_last_active`'s update-only precedent -- a worker has no
equivalent "signup" moment a user does). JSON `capabilities` tags
(Backend/Frontend/Database/etc., free-form, queryable). Staleness
(`is_stale`/`seconds_since_heartbeat`) is always computed live from
`last_heartbeat_at`, never stored -- matching every other
liveness-adjacent computation in this package.

**Added — Integration Queue.** `GET /api/integration/queue` and
`GET /api/work-items/{id}/review` -- a *view* over `work_items`
(status in `testing`/`ready_for_review`), not a new persisted queue
table. Reuses `merge_readiness.py`'s existing 0-100 explainable score
and `overlap_v2.py` directly; ordering (score descending, tiebreak:
`priority` desc then `updated_at` asc) *is* the queue position,
recomputed fresh every request. `readiness_note` honestly flags when a
work item's self-reported `estimated_files` was used as a proxy for a
real `git diff` (essentially always) -- same honesty
`test_status="unknown"` already models elsewhere, rather than silently
presenting a weaker signal at full confidence.

**Added — Mission Control.** `WorkforcePanel.tsx` (worker list, 15s
polling), `IntegrationQueuePanel.tsx` (ordered rows, expandable
merge-readiness factor breakdown -- this expansion *is* the "Confidence
Dashboard" for Milestone 1, a separate panel isn't justified until
usage history exists). `CollaborationWorkspace.tsx`'s pre-existing
"Merge Queue" tab (confirmed: a plain client-side filter, zero backend
concept) renamed to "Testing / Ready for Review" in the same change --
leaving two different things both called "queue" would have been
exactly the confusion the preceding audit flagged.

**Explicitly deferred, documented in `ROADMAP.md`'s Future section as
Milestones 2-7, not built now**: logging the review pipeline onto
`work_item_activity` + folding worker staleness into `merge_readiness`
(M2); a trend-based Confidence Dashboard (M3, needs M2's history first);
a Conflict Heat Map + Pending Approvals rollup (M4); Coordinator Memory
(M5, no usage data exists yet to build it from); an Integration Branch
git workflow (M6, needs separate explicit human sign-off -- changes
actual day-to-day git usage); Rollback Intelligence (M7, likely depends
on M6).

42 new tests (35 backend -- schema/store, API routes, a concurrency
test mirroring `claim_work_item`'s, a golden-equivalence test guarding
the queue against silently drifting from the primitive it wraps, a
tiebreak test; 7 frontend panel tests). Full suite verified: 1650
backend tests (1599 passed, 51 skipped, 0 failed), 129 frontend tests
(0 failed).

## 2026-07-29 — SIL Phase 6 collaboration audit: 5 findings, 2 fixed

User asked for a complete audit of the collaboration architecture (Team
Mode, Active Work Registry, automation, accounts, git integration, DB/
API sync) before building any new "Integration Coordinator" feature.
Audited via a parallel research fork (accounts/Team Mode/DB, less
firsthand-known this session) plus direct review (collaboration/
automation, built this session). Full detail in `KNOWN_ISSUES.md`
ISSUE-034 through ISSUE-038; this is the summary.

**Fixed, with regression tests:**
- **ISSUE-034 (Medium)**: registration's two-call non-atomicity
  (`POST /api/organizations` then `POST /api/users`) can permanently
  orphan a zero-user organization with no admin visibility. Root cause
  needs a combined API contract (a schema/route decision, out of scope
  for an audit pass) -- fixed the *visibility* gap instead: the
  maintenance scheduler now counts (never deletes -- an org is real
  data) orphaned organizations past a grace period, surfaced in
  `GET /api/automation/status`. 4 new tests.
- **ISSUE-035 (Low)**: `GET /api/activity/timeline`'s global path had no
  usable index (a full table scan + sort) -- the existing composite
  index only helps once `work_item_id` is constrained. Added
  `idx_work_item_activity_created_at` in both backends (SQLite +
  Alembic migration `bb0017abe70b` for Postgres) -- purely additive.

**Documented, deliberately not fixed (see each entry for why):**
- **ISSUE-036 (High as a primitive)**: `GET /api/users` +
  `GET /api/users/{id}/me` together let anyone harvest every user's
  `api_key` -- a direct, faithful consequence of this platform having no
  authentication layer yet, not a coding mistake. No superficial fix is
  safe without breaking the Profile page or providing false security;
  flagged as exactly what "real authentication" (already on
  `ROADMAP.md`) needs to close.
- **ISSUE-037 (Low)**: `git_watcher.py`'s draft-dedup logic has no
  cross-process protection -- safe today (only an in-process lock exists),
  a real but low-severity gap if multiple machines ever run
  `automation.enabled` simultaneously under Team Mode. Worst case is a
  duplicate, clearly-labeled draft a human dismisses in one click.
- **ISSUE-038 (informational)**: measured, not estimated -- 400
  concurrent SQLite writes succeed cleanly; 5,000 across 100 threads
  produces `database is locked` on ~4% of them. Directly relevant to any
  future multi-worker design: that load profile needs Team Mode's
  Postgres backend, not SQLite mode.

**Also verified NOT bugs** (audited, confirmed safe, no action needed):
org/user creation already uses DB-level `UNIQUE` constraints + caught
`IntegrityError` (no TOCTOU race, unlike the earlier `claim_work_item`
fix); `frontend/src/session.tsx`'s stale-user handling.

**Design note carried into the Integration Coordinator scoping
discussion** (not a bug): Mission Control's existing "Merge Queue" tab
is a client-side filter over the work-items list
(`status === 'testing' || 'ready_for_review'`), not a real queue --
naming collision with the actual Integration Queue the next feature
needs; `merge_readiness.py`'s explainable score and
`overlap_v2.compute_all_conflicts` already exist and should be reused
directly, not rebuilt.

Full suite verified: 1564 backend tests (0 failed, 51 skipped).

## 2026-07-29 — Pull-only auto-sync (git-sync scheduler)

User asked whether a teammate on their own machine could edit/commit
this repo the same way this session does (yes -- plain git, nothing
session-specific), then asked for automatic sync. Clarified via
AskUserQuestion that "sync" meant two very different risk profiles:
auto-*pull* (safe, reversible) vs auto-*push* (a "pushing code" action
this project's own operating rules treat as confirm-each-time, not a
background-loop action). User chose pull-only.

**Prerequisite discovered and fixed**: local `main` was 60 commits ahead
of `origin/main` -- nothing from this entire session (Team Mode fixes
through SIL Phase 4) had ever been pushed. Pushed with explicit
confirmation before building anything else.

**Added**: `collaboration/git_sync.py`'s `GitSyncScheduler` -- same
daemon-thread shape as `git_watcher.py`/`maintenance.py`. Every cycle:
skips entirely if the working tree is dirty (never touches uncommitted
work), fetches the configured remote, and applies a merge **only** when
it's a genuine fast-forward (local HEAD is an ancestor of the
remote-tracking ref) -- a diverged history (unpushed local commits) is
reported, never merged/rebased/forced. **Never pushes**, under any code
path -- confirmed by a dedicated test. `automation.git_sync_enabled`
(default `False`, a *separate* toggle from `automation.enabled` since
this touches the real working tree via merge, not just draft metadata)
+ `git_sync_interval_seconds`/`git_sync_remote`. `GET
/api/collaboration/git-sync/status`; `AutomationStatusOut`/Mission
Control's `AutomationPanel.tsx` gained a third scheduler row.

**Fixed while writing this feature's own tests** (test-fixture bug, not
a product bug -- see full detail in the commit): a throwaway bare-repo
test fixture's default branch didn't match `main`, causing a `git
clone` inside the test to silently check out the wrong branch and land
simulated commits somewhere `git_sync.py` was never looking --
corrected in the test fixture, not the scheduler itself.

**Also fixed**: `config.example.yaml` never documented the
`automation` section at all (a pre-existing gap predating this
session's SIL Phase 4 work) -- added now, alongside the new git-sync
fields.

31 new tests (11 `test_git_sync.py`, 6 `test_api_app_automation_startup.py`
covering the independent enabled/git_sync_enabled gating -- written
specifically because that gating logic was restructured and is exactly
the kind of change that can silently invert a condition -- plus API
route/schema/frontend coverage). Full suite verified: 1560 backend
tests (0 failed, 51 skipped), 122 frontend tests (0 failed, confirmed
across 4 consecutive full runs after one non-reproducing flake).

## 2026-07-29 — SIL Phase 4 "Intelligent Automation Layer"

Six milestones, built directly on the existing Collaboration Platform
(Active Work Registry, Overlap V2, git introspection) with no redesign.
Full detail in `docs/ARCHITECTURE.md`'s "SIL Phase 4" section,
`KNOWN_ISSUES.md` ISSUE-029 through ISSUE-033, and `PROJECT_STATE.md`'s
matching "Last Completed Work" entry.

**Added — Milestone A, AI context bundle.** `collaboration/context_bundle.py`'s
`build_context_bundle()`, `POST /api/collaboration/context-bundle`, and
`work_item_cli.py context` -- one call gathering active work items,
similar past work (keyword-matched against every work item ever
created), recent commits, branch info, Overlap V2 warnings, and relevant
`KNOWN_ISSUES.md`/`ROADMAP.md` excerpts (plain substring search, not
semantic). Pure aggregation of systems that already existed and were
already tested -- no new persisted index, no architecture graph.

**Added — Milestone B, draft work items + background git-watcher.**
`work_items.is_draft` (Alembic `7a3f9c2e1b0d`, mirrored in both SQLite
and Postgres stores). `collaboration/git_watcher.py`'s
`GitWatcherScheduler` -- same `threading.Lock`+`Event` daemon-thread
shape `MarketDataScheduler` already established -- periodically checks
`git status --porcelain` for uncommitted changes not covered by any
active (non-draft) work item's `estimated_files`, and drafts exactly one
work item covering them: self-healing (a growing/shrinking change set
discards the stale draft and creates a fresh one) and idempotent (an
unchanged set is a no-op, confirmed by a dedicated regression test).
Never touches a non-draft item; never auto-approves. New endpoints
(`GET /api/work-items/drafts`, `{id}/approve-draft`, `DELETE {id}/draft`)
and CLI subcommands (`drafts`/`approve-draft`/`discard-draft`).

**Added — Milestone C, local validation command.** `tools/local_validate.py`
maps uncommitted changes to their likely test files (most follow
`test_<module>.py` or `test_<package>_<module>.py`, confirmed against
real examples in this repo -- but not all modules have one, e.g.
`strategy/base.py`), runs just those, and falls back to the full backend
suite when the mapping is incomplete rather than silently under-testing.
Also runs `tsc -b`/`oxlint`/`vitest` when `frontend/src` changed.

**Added — Milestone D, documentation draft assistant.**
`tools/draft_changelog.py` drafts a `CHANGELOG.md`-style entry from
every commit and completed/merged work item since `CHANGELOG.md`'s own
last commit, writes it to `.changelog_draft.md` (gitignored) for a human
to review and rewrite into this repo's narrative prose style -- never
edits `CHANGELOG.md` itself; what actually matters and what's a breaking
change stays a human/session judgment call.

**Added — Milestones E+F, automation status panel + maintenance job.**
`collaboration/maintenance.py`'s `MaintenanceScheduler` -- same
daemon-thread shape, longer interval -- discards drafts untouched for
`automation.stale_draft_days` (inclusive at the cutoff) and checks DB
connectivity via `db.health.check_database_health()` (imported lazily,
see ISSUE-032 below). `GET /api/automation/status` combines both
schedulers' status; Mission Control's new `AutomationPanel.tsx` shows
both and lets a human approve/discard drafts inline, 15s poll.

**Changed.** Both schedulers are wired into `api/app.py`'s lifespan
alongside the research server, gated on a new `automation.enabled` in
`config.yaml` (default **`False`** -- same "zero behavior change unless
deliberately opted into" convention `research_server.enabled`/`live_feed`
already establish, and specifically necessary here: this codebase's test
suite calls `create_app()` well over a thousand times, and either
scheduler defaulting to running would have started a real background
thread hitting this repo's own real `git status` and writing real
`is_draft` rows into whatever database each test happened to be pointed
at). `WorkItemOut`/`frontend/src/types.ts`'s `WorkItem` both gained
`is_draft: bool`.

**Fixed — 5 real bugs found during development, all caught before
shipping** (see `KNOWN_ISSUES.md` for full detail on each):
- **ISSUE-029** (High): `git_info._git()`'s whole-string `.strip()` ate
  the leading space off the first line of `git status --porcelain`
  output (a legitimate part of the two-column status code), truncating
  the first changed file's path by one character.
  `changed_files()` no longer goes through that shared helper.
- **ISSUE-030** (Medium): the git-watcher's own drafts counted as
  "covered," self-suppressing their files from the next cycle's
  uncovered-file calculation and breaking the supersede logic for a
  growing change set. Fixed: only real (non-draft) items count as
  covered.
- **ISSUE-031** (Low): the maintenance scheduler's staleness check used
  `>=` where the config docstring's "untouched for N days" implies
  inclusive-at-exactly-N. Fixed to `>`.
- **ISSUE-032** (Medium): `maintenance.py`'s first draft imported
  `db.health` eagerly, which would have made `sqlalchemy` a hard runtime
  dependency for every SQLite-only install (it's imported unconditionally
  by `app.py`). Fixed with the same lazy-import + `ModuleNotFoundError`
  fallback `api/routes/system.py`'s health route already established.
- **ISSUE-033** (Low): adding `WorkItem.is_draft` broke three existing
  frontend test fixtures (`CollaborationWorkspace`/`WorkItemTable`/
  `WorkRegistryPanel`'s `.test.tsx` files) -- caught by
  `tools/local_validate.py`, the tool being built in the very next
  milestone, on its first real use.

**DB changes.** `work_items.is_draft BOOLEAN NOT NULL DEFAULT false`
(additive, backfills to `false`).

**API changes.** New: `POST /api/collaboration/context-bundle`,
`GET/POST /api/work-items/drafts`, `{id}/approve-draft`,
`DELETE {id}/draft`, `GET /api/collaboration/git-watcher/status`,
`GET /api/automation/status`. `WorkItemOut` gained `is_draft`.

**Frontend changes.** New `AutomationPanel.tsx`, registered into Mission
Control. `WorkItem` gained `is_draft: boolean`.

**Breaking changes.** None -- `automation.enabled` defaults `False`,
`is_draft` defaults `false`/backfills on every existing row.

Full suite verified green after every milestone: 1593 backend tests
(1542 passed, 51 skipped, 0 failed), 121 frontend tests (21 files, 0
failed).

## 2026-07-28 — Live incident response: Team Mode boot, registration, Team Members, and Live Session all broken end-to-end

Not a planned pass -- a live "boot Team Mode and use it" session that hit
four separate, real, previously-undiscovered bugs in sequence, each
blocking the next thing tried. Every fix confirmed by temporarily
reverting it and re-running its regression test against the pre-fix
code. Full detail in `KNOWN_ISSUES.md` ISSUE-025 through ISSUE-028; this
is the summary, in the order they were hit.

**ISSUE-025 (Critical) — Team Mode's production build silently kept the
local-dev loopback API URL.** `api.ts`'s `API_BASE` fallback depended on
`start-team.ps1` setting `VITE_API_BASE_URL=""` at build time, which
doesn't reliably survive the PowerShell -> npm.cmd -> cmd.exe -> vite
child-process chain on Windows -- confirmed directly by finding the
literal `http://127.0.0.1:8000` baked into the actual built bundle. Every
teammate's browser tried to reach its own machine instead of the real
server. Fixed by basing the default on `import.meta.env.DEV`/`PROD`
(real compiled-in booleans) instead.

**ISSUE-026 (Critical) — `PgAccountStore` crashed every response
containing a user with no `notification_preferences` set.** A nullable
JSONB column read back as `None`, but `UserOut`/`UserMeOut` require a
`dict` -- `default_factory` doesn't cover an explicitly-passed `None`.
Broke `GET /api/users`, `GET /api/users/{id}/me`, and even
`POST /api/users`'s own response (registration looked like it failed
even though the row had committed). Fixed by normalizing `None` -> `{}`,
matching the SQLite store's existing behavior. **Incident note**:
verifying this fix's regression test against the live database used its
`TRUNCATE`-based cleanup fixture, which wiped the org/account a teammate
had just registered -- limited to those two tables, re-registration
succeeded once both fixes landed. Documented in ISSUE-026 as a lesson for
next time: never run a `TRUNCATE`-cleanup test file against a database
someone's actively using.

**ISSUE-027 (High) — self-role-edit lockout on Team Members.** A sole
owner who demoted themselves lost `manage_members` on the next refresh,
which hid the role editor for *every* row including their own -- no
recovery path through the product (permissions are advisory-only, not
enforced server-side). Fixed: you can no longer edit your own role from
this page, ever -- only a teammate can change it for you.

**ISSUE-028 (High) — Live Session defaulted to an expired contract.**
The start form's hardcoded `'MESH6'` default was months expired by the
time anyone used it; the session started and reported "running" forever
without a single bar (an empty result is a successful response, not a
feed error, so nothing about the session's own status revealed this).
Confirmed directly by querying the Massive API for both tickers -- the
expired one had zero data, the real current contract (`MESU6`) was
streaming live. Fixed: the field now starts empty with guidance on
picking the current quarterly code by hand. Flagged, not built: an
active-contract-resolution API endpoint so this could auto-fill instead
(a new route needs explicit approval per CLAUDE.md section 8's protected
list).

**Also cleaned up while diagnosing ISSUE-028**: `feeds/massive.py` had
several leftover `print()` debug statements (ironically what made the
diagnosis fast) -- converted to proper `log.debug()` calls instead of
either leaving raw prints in production or deleting genuinely useful
diagnostic signal.

**Operationally**: the local docker-compose TimescaleDB was also found 3
Alembic migrations behind (`owner_type`, `api_key`/profile fields,
`org_id`) and brought current (`alembic upgrade head`) -- not a code bug,
just a database nobody had migrated yet.

**Testing**: 4 new regression tests (API_BASE resolution, `notification_
preferences` normalization against a live Postgres, self-role-lockout
prevention, Live Session symbol handling), each confirmed to fail against
its bug's pre-fix code before the fix landed. Full backend and frontend
suites verified passing after every change.

## 2026-07-28 — Stabilization Mode: concurrency and edge-case sweep of the Collaboration/Registration features

A ~1-hour, skeptical bug hunt across everything added in the same day's
two preceding entries (SIL Phase 2, User Registration & Organization
Management) — assume hidden bugs exist, try to break every new feature,
fix every legitimate issue found. Four real bugs found, fixed, and each
confirmed against a regression test that fails on the pre-fix code
(verified directly by temporarily reverting each fix and re-running its
test) before restoring the fix. Full detail in `KNOWN_ISSUES.md`
ISSUE-022 through ISSUE-024 and each commit's own message; this is the
summary.

**ISSUE-022 (High) — a real concurrent-claim race.** `claim_work_item`
read the item, decided in Python whether the claim was allowed, then ran
an unconditional `UPDATE` — two concurrent callers (two humans, or two AI
sessions, racing to claim the same item) could both pass the check before
either committed, and whichever `UPDATE` committed last silently won:
*both* callers got back a 200 claiming ownership, no error either way.
Fixed in both `CollaborationStore` and `PgCollaborationStore` by
re-checking ownership in the `UPDATE`'s own `WHERE` clause and inspecting
the affected-row count — the loser now gets the same "already claimed"
error a sequential double-claim already raised.

**ISSUE-023 (Medium) — silent action failures.** Every action handler in
`WorkItemTable.tsx` (claim/release/complete/advance) awaited its API call
with no `try`/`catch`, and this app has no error boundary — a rejected
call (newly reachable in normal use once ISSUE-022 made losing a claim
race a real outcome) vanished with zero feedback. Fixed via a shared
`runAction()` wrapper that catches and displays the error, then always
refetches so the UI stays honest.

**ISSUE-024 (Medium) — Register retry created a duplicate, orphaned org.**
If `createOrganization` succeeded but the following `createUser` call
failed (e.g. a taken username), the org already existed with no owner;
retrying re-ran `createOrganization` with the same name and hit "already
exists" — a dead end. Fixed by caching the created org id across retries
within the same form session.

**Also found and fixed (not a numbered KNOWN_ISSUES entry, a scoping gap
rather than a bug with a failure mode): `TeamPanel.tsx`** (Mission
Control's "Team" widget) was still calling `getUsers()` with no
organization filter — missed when `session.tsx`/org-scoping landed
elsewhere in the same effort, so a multi-org instance's Team widget
showed every registered user globally. Scoped to the signed-in user's
own organization; added an online/offline badge (`format.ts::isOnline`,
shared with `TeamMembers.tsx` rather than duplicated); refreshed a stale
module docstring that still claimed no session concept existed.

**Swept and found clean** (no bug, or a pre-existing/documented design
choice, not something this pass needed to change): `release_work_item`/
`complete_work_item`/`reassign_work_item`/`update_status` (none has
`claim`'s ownership-exclusivity invariant to protect, so the same
check-then-set shape isn't a race there); `accounts/store.py`'s
username-uniqueness enforcement (DB-level `UNIQUE` constraint, already
atomic); every `setInterval` in the frontend (all have a matching
`clearInterval` cleanup — no leaks); the new `fetch_work_items` dynamic
`WHERE`-clause builder (parameterized throughout, no injection risk);
Postgres's `notification_preferences` JSONB round-trip (already a dict,
not double-decoded). Two named items from the stabilization brief —
"Architecture Intelligence Engine" and a persisted "dependency graph" —
don't exist as literal systems in this codebase (Overlap Engine V1/V2 are
what exists today, and are real but deliberately shallower; see
`ROADMAP.md`'s "Future" section) — noted rather than fabricating findings
against something not actually built.

**Testing**: 4 new backend/frontend regression tests, each independently
confirmed to fail against its bug's pre-fix code. Full backend suite
(1468 passed, 50 skipped) and full frontend suite (110 passed, `tsc -b`/
`oxlint` clean) verified passing after every fix.

## 2026-07-28 — User Registration & Organization Management

Continuation of the same day's SIL Phase 2 entry (below) — expands the
lightweight accounts MVP into a full onboarding experience while staying
explicitly no-auth (no passwords, no OAuth -- still an internal
development platform). Full detail in each milestone's own commit
message (three commits); this is the summary.

**Backend**: `users` gains an auto-generated personal `api_key`
(`fbot_`-prefixed, unique -- placeholder for future auth, not enforced
anywhere yet) and profile fields (`timezone`, `preferred_ai_model`,
`default_branch_prefix`, `notification_preferences` JSON) via a new
Alembic revision (`1be0295e6dbe`). New `UserMeOut` (includes `api_key`)
vs. the existing `UserOut` (never does) -- registration and
`GET /api/users/{id}/me` return the former, every roster listing the
latter, so one member's key never leaks into another's view of the team.
New `accounts/permissions.py`: a flat, explainable role->capability table
(`manage_organization`/`manage_members`/`manage_work`/`view`) -- not
enforced server-side (no request-level identity exists to check it
against), consulted by the frontend only. New `PATCH /api/organizations/{id}`
(rename) and `POST /api/users/{id}/regenerate-api-key`.

**Multi-org data isolation**: `work_items` gains a nullable `org_id`
(Alembic `9f68d758839f`) so "each organization contains Active Work, AI
Workers, Collaboration data" is actually true -- threaded through every
overlap/conflict/merge-readiness/pre-work-check call so two unrelated
organizations sharing one instance never warn each other about
coincidental file-path matches. `org_id=None` means "no filter,"
preserving every existing session-less caller's behavior (e.g. the CLI).

**Frontend**: a new `session.tsx` -- a frontend-only "current user"
concept (a user id in `localStorage`, resolved via `GET /api/users/{id}/me`)
since there's still no backend session to derive "who is making this
request" from. Explicitly documented as a UX convenience, never a
security boundary. `RequireSession` gates every in-app route behind
having registered/picked an account at least once. New pages: Welcome,
Register (create-or-join an org; role is never a form field -- the org
creator becomes `owner`, a joiner becomes `member` automatically, a
human owner/admin promotes people afterward), Profile (preferences,
API key display/regenerate), Organization Settings (rename, gated behind
`manage_organization`), Team Members (roster, online status derived from
`last_active_at`, role changes gated behind `manage_members`). Wired the
real session into what already existed -- `StatusBar` shows who's signed
in, `WorkItemTable`'s claim prompt defaults to the signed-in user,
`CollaborationWorkspace`/`WorkRegistryPanel` scope work items to the
session's organization.

**Bug found via testing, fixed**: `Profile.tsx`'s `useApi` fetcher called
`getUserMe(currentUser!.id)` unconditionally on mount, which would crash
if `currentUser` was ever `null` when that effect first ran (a
stale/cleared session while the page is already mounted, e.g. via
"Switch user" in another tab) -- fixed to skip the fetch entirely without
a current user; regression test added.

**Testing**: ~20 new backend tests, 22 new frontend tests (107 total, up
from 85) -- `tsc -b` and `oxlint` both clean. Full backend suite (1467
passed, 50 skipped -- live-Postgres only) and full frontend suite
verified passing.

## 2026-07-28 — SIL Phase 2 "Workflow Integration": the Collaboration Platform becomes the actual workflow

Continuation of the same day's Team Collaboration Platform MVP entry
(below) — a follow-on brief explicitly building on that foundation with
no redesign: mandatory work registration, automatic branch tracking, a
full Mission Control workspace, a deeper overlap engine, a work lifecycle,
AI awareness, a merge-readiness dashboard, a searchable activity timeline,
a reliability pass, and documentation. Full detail in each milestone's own
commit message (six commits); this is the summary.

**Lifecycle + owner_type.** `collaboration.STATUSES` grows from
open/claimed/completed to a full pipeline (`planned→claimed→in_progress→
testing→ready_for_review→merged→completed`) via a new `update_status()`/
`POST /api/work-items/{id}/status` — advisory, not enforced (a review can
send work backward; claim/release/complete keep their exact Phase 1
behavior for open/claimed/completed). `owner_type` ('human'/'ai') added to
`work_items` (Alembic revision `4ec561b51e64` for Postgres, `ALTER TABLE`
for SQLite, defaults to `'human'`).

**Live git branch info** (`collaboration/git_info.py`). Branch age,
ahead/behind vs a base branch, last commit — via `git` subprocess calls,
nothing persisted, best-effort (detached HEAD/missing base branch/not a
repo degrade to notes, never an exception). `GET /api/git/branch-info`,
`GET /api/work-items/{id}/branch-info`.

**Overlap Engine V2** (`collaboration/overlap_v2.py`). Shared Python
imports (`ast`-parsed)/TS imports/API route paths/DB table names/frontend
component names/config files/title-description keywords — one explainable
0-100 confidence score with a factor-by-factor breakdown, additive
alongside the original file-path-only V1 (untouched — still what
`create_work_item`/`check_overlap`/`merge_summary` use). New
`GET /api/work-items/{id}/overlap-v2`, `GET /api/work-items/conflicts`
(bulk pairwise scan across all active work in one pass), and
`POST /api/work-items/pre-work-check` — the AI-awareness contract,
callable before a work item even exists, returning a `suggested_action`
(proceed/coordinate/choose_different_task) and a recommendation naming
the conflicting item's owner.

**Merge readiness dashboard** (`collaboration/merge_readiness.py`). One
explainable 0-100 score from Overlap V2 risk, branch age, behind-base
count, and change size. `test_status` is always the literal `"unknown"`
— no CI integration exists, and guessing would be worse than admitting
that gap (documented in the module's own docstring). New
`POST /api/work-items/merge-readiness`.

**Searchable activity timeline** (`collaboration/timeline.py`). Merges
`work_item_activity` with real git commits into one filterable feed
(`event_type`/`q`/`since`/`until`). New `GET /api/activity/timeline`.
While building it: found and fixed a real ordering bug —
`CollaborationStore.fetch_activity` ordered only by `created_at`, which
SQLite's `datetime('now')` resolves to whole seconds, so events logged
within the same second (realistic for an automated/AI-driven
claim-then-complete) tied and sorted unpredictably. Fixed with a `rowid`
tiebreaker (SQLite's own implicit, monotonically-increasing insert-order
column); regression test added. `PgCollaborationStore` didn't need the
same fix — `func.now()` there resolves per-transaction with real
microsecond precision.

**`tools/work_item_cli.py`.** create/list/claim/release/complete/status/
check subcommands talking to `collaboration_service` in-process — no API
server needed. `check` runs the pre-work-check and exits nonzero on a
critical overlap. `CLAUDE.md`'s Session Protocol gained a new step 7:
for non-trivial work, run `check` and register a work item before
starting — turns the "AI awareness" requirement into something sessions
actually do, not just a documented ask.

**Mission Control's `CollaborationWorkspace`.** Seven tabs — My Active
Work, Team Active Work, AI Workers, Recent Activity, Merge Queue,
Conflict Warnings, Ready For Review — every one real data
(`getWorkItems`/`getTimeline`/`getWorkItemConflicts`), polling every 15s
like `InfrastructurePanel` already does. Extracted the list/action
rendering (claim/release/complete/advance-to-next-stage, status badges,
a compact per-item lifecycle-stage dot indicator) out of
`WorkRegistryPanel` into a shared `WorkItemTable`, used by both.

**Reliability pass.** AST-based unused-import scan found and removed two
(`merge_readiness.py`'s unused `Optional`, `collaboration_service.py`'s
unused `OverlapWarningV2` dataclass import); confirmed the Alembic
migration chain stays linear with a single head; the `rowid` tiebreaker
above was the one behavioral bug found and fixed.

**Testing.** ~70 new backend tests (1433 passed, 47 skipped — live-Postgres
only, up from 1279/1250/29), 13 new frontend tests (85 total, up from 72)
— `tsc -b` and `oxlint` both clean.

**Documentation.** `docs/ARCHITECTURE.md` gained a "Team Collaboration
Platform" section covering the full layer (accounts + collaboration,
V1/V2 overlap, git info, merge readiness, timeline) and what's still out
of scope. `ROADMAP.md`'s Future section updated to reflect what SIL Phase
2 actually closed vs. what's still ahead (real auth, a true dependency
graph, semantic merge assistance, real CI integration, persistent AI
workers, a distributed worker network).

## 2026-07-28 — Team Collaboration Platform: lightweight accounts, live Mission Control data, and an Active Work Registry

Continuation of the same day's earlier Stabilization Mode entry (below).
A six-phase brief: (1) finish Team Mode end-to-end, (2) lightweight user
accounts, (3) real Mission Control data, (4) a stability pass, (5) team
collaboration groundwork, (6) code quality. Full detail in each area's own
commit message; this is the summary.

**Phase 1 — Team Mode, continued.** Bounded the firewall-rule elevation
wait: `Start-Process -Verb RunAs` can block synchronously on the UAC
decision regardless of `-Wait`, confirmed empirically across three
attempted fixes (`Process.WaitForExit`, a plain polling loop, a
`Start-Job`-wrapped launch) before landing on a marker-file-based design
that correctly bounds the wait without relying on any process-handle
relationship across the elevation boundary. Verified connection-failure
handling (stopped the live TimescaleDB container, confirmed
`/api/system/health` degrades gracefully rather than hanging/crashing)
and 30-concurrent-request shared-DB access still clean with every fix
from this session applied.

**Phase 2 — Lightweight user accounts.** New `accounts/` package: `users`/
`organizations` tables (SQLite + Postgres, Alembic-migrated), four fixed
roles (owner/admin/member/viewer), full CRUD API. Deliberately not an
authentication system — no password, no session, no login route — built
so stronger auth can be added later without a redesign.

**Phase 3 — Mission Control, real data.** New `GET /api/system/infrastructure`
(CPU/memory/disk via a new `psutil` dependency, job-queue depth from the
real `jobs` table). Two new panels replacing mock data: `InfrastructurePanel`,
`TeamPanel` (real registered users + the connected-users estimate).
Deliberately not wired: a "restart services" button — this API has no
auth in front of it yet, so exposing a process-restart action to anyone
who can reach the port needs auth first, not a UI button.

**Phase 4 — Stability pass.** Found and fixed a genuine timestamp-parsing
correctness issue while building `TeamPanel` (SQLite's space-separated,
timezone-less format needs explicit ISO-8601 normalization before
parsing); documented but deliberately deferred the same underlying issue
in the widely-shared `format.ts::dateTime()` (touches every page that
renders a local-mode timestamp — a focused fix of its own, not a drive-by
edit).

**Phase 5 — Active Work Registry.** New `collaboration/` package: claimable
work items (title/description/owner/branch/status/estimated files/
priority) with claim/release/complete/reassign, every transition logged
to an append-only activity table. New `collaboration/overlap.py`: warn-only
file-path overlap detection against every other active work item
(no_risk/low/medium/high/critical, small documented heuristic, never
blocks). New Mission Control panel (list/create/claim/release/complete,
inline overlap warnings). New `POST /api/work-items/merge-summary` --
"before merging, generate a summary showing overlap with active work" --
reusing the same overlap detection against a real diff's changed files.

**Phase 6 — Code quality.** Confirmed no unused imports across every new
module; the existing per-store-class duplication (`accounts/store.py`
next to `collaboration/store.py`, mirroring `TradeStore`/`MarketDataStore`'s
own existing precedent of small, independent store classes rather than a
shared base class) was left as-is rather than introducing a new
abstraction layer for two call sites.

**Testing:** ~95 new backend tests (accounts + collaboration: store unit
tests, HTTP route tests, live-Postgres suites following the project's
established skip-without-a-server convention) and 6 new frontend tests,
on top of everything from the same day's earlier Stabilization Mode
entry. Full frontend suite (72 tests, typecheck, lint) and every touched
backend test area verified passing throughout, not just at the end.

**Deliberately out of scope**, per each area's own docstring: real
authentication (passwords/sessions/API keys), a dependency/architecture
graph, semantic merge analysis, persistent AI worker processes, and a
distributed compute cluster. Each is a substantially larger effort of its
own to build on top of this session's foundation, not a missing piece of
it — see `ROADMAP.md` for how they're tracked.

## 2026-07-28 — Real production data migration completed; Stabilization Mode pass (10 real bugs found and fixed)

Two parts: finishing the real `market_data.db`/`research.db` → TimescaleDB
migration PROJECT_STATE.md flagged as the operator's own next step, then a
full Stabilization Mode pass (crash protection, thread safety, resource
leaks) across the whole platform. Every fix below has its own KNOWN_ISSUES.md
entry (ISSUE-014 through ISSUE-020) with full reproduction/verification
detail; this is the summary.

**Real production data migration — completed and verified.**
Backed up both `.db` files first (integrity-checked). Dry-run confirmed
real counts (3,519,756 `bars` rows, 14,807 `trades`, etc.) against the
live `deploy/docker-compose.yml` `timescaledb` instance. The real
`--yes` run hit a genuine blocker (ISSUE-014 below) partway through;
after fixing it, the full migration completed and was verified
source-count ≤ destination-count for every table in both databases.

**Fixed, this session:**
- **ISSUE-014 (High):** `bars.created_at NOT NULL` rejected 32.5% of
  real `bars` rows (every source, not an edge case) that genuinely have
  no `created_at` — a live consequence of ISSUE-004's schema drift, only
  surfaced now that real data hit a NOT-NULL-enforcing destination.
  Fixed by making the column nullable in Postgres (new Alembic revision)
  to match the source's true state, rather than fabricating or dropping
  real rows.
- **Team Mode networking (Priority #1, High):** root-caused why another
  device on the tailnet couldn't reach the backend even though it worked
  locally — Windows Firewall's Private-profile default is BlockInbound
  with zero rule for this app/port; same-machine tests succeed because
  local delivery to an owned IP never crosses the filtered path. Fixed:
  `scripts/start-team.ps1` now auto-creates a rule scoped to Tailscale's
  own CGNAT range, self-elevating via one UAC prompt if needed.
- **ISSUE-015 (Low):** a genuinely flaky test
  (`test_stale_ip_outside_window_is_purged`, ~30-40% failure rate even in
  isolation) traced to `time.monotonic()`'s ~31ms clock granularity
  beating a 10ms sleep. Fixed by increasing the sleep to 50ms.
- **ISSUE-016 (Medium):** a real, reproducible check-then-set race in
  `ResearchServer.start()`, `AutonomousPaperTrader.start()`, and
  `LiveSessionManager.start()` — all three checked a running-flag under
  lock, released it, did slow setup work, then set the flag, so two
  concurrent calls could both proceed. Confirmed with a new regression
  test that failed 3/3 pre-fix, passed 3/3 post-fix, in all three cases.
  Fixed by claiming the flag atomically with the check.
- **ISSUE-017 (High):** `GET /api/logs` read the entire `decisions.jsonl`
  into memory on every call — found at 9.2 GB / 34.2M lines on this
  machine (unbounded, never-rotated, from long-running autonomous paper
  trading). Fixed with a bounded backward-seeking tail reader: 2000 lines
  in 0.022s against the real file, down from a multi-GB allocation.
- **ISSUE-018 (Low):** `Live.tsx` showed a blank panel while a session
  was `starting` — a gap made more visible by the ISSUE-016 fix above
  lengthening that window. Added a loading indicator for that state.
- **ISSUE-019 (Medium):** unhandled exceptions in any API route were
  never logged anywhere this project's own logging system controls (a
  real silent-failure gap, though the response itself was already safe).
  Added a catch-all handler that logs via the `futures_bot` logger
  without shadowing the existing `ApiError`/`KeyError`/`HTTPException`
  handling.
- **ISSUE-020 (Low):** `api/jobs.py`'s executor lazy-init had no lock,
  unlike every other singleton accessor in this codebase. Added one for
  consistency.

**Verified:** full local-mode boot (backend + frontend, clean), team-mode
boot up through the point requiring human UAC interaction (the one step
that genuinely can't be automated further), every `test_api_*.py` file
(16 files, ~160 tests) individually, the full research-server test suite,
and the full frontend suite (65 tests, typecheck, lint) all green.

**Not done:** creating the actual Windows Firewall rule end-to-end
(needs a human to click the UAC prompt or run the one-time elevated
command this session provided) and a genuine second-machine Tailscale
connection (a registered peer exists, `rafaelballer`, but was offline
this session).

## 2026-07-27 — Team deployment (Tailscale + TimescaleDB): completed and verified against a live server

Continuation and completion of the entry directly below (`market_data.db`
vertical slice, `[IN PROGRESS]`) — Docker/WSL2 is now installed, unblocking
everything that entry's "Not started"/"Not yet verified" lists deferred.
Approved plan: `C:\Users\sstae\.claude\plans\polymorphic-nibbling-lamport.md`.
Full detail (bugs found, exact test counts, per-table verification) is in
`PROJECT_STATE.md`'s "Team deployment — completed" write-up and
`KNOWN_ISSUES.md` ISSUE-010/011/012 (all found and fixed this session,
logged Resolved immediately); this entry is the summary.

**Added**
- `deploy/docker-compose.yml`'s `timescaledb` service (official
  `timescale/timescaledb:latest-pg16` image, named volume, health check,
  loopback-only port binding).
- `alembic/` + `alembic.ini` — Alembic now manages both databases'
  Postgres schema (`db/schema.py` + new `db/research_schema.py`, 5 + 14
  tables), chained as two revisions. `alembic upgrade head` verified
  against the live compose instance: every table created, `bars`
  genuinely converted into a TimescaleDB hypertable
  (`timescaledb_information.hypertables` confirmed).
- `src/futures_bot/research/pg_trade_store.py::PgTradeStore` — full
  Postgres port of `TradeStore`, all ~60 methods, verified against the
  live server (`tests/test_pg_trade_store_live.py`, 12 tests covering
  every subsystem including the client-import FIFO-lot pipeline).
  `api/store.py::get_store()` now branches on `FUTURES_BOT_DATABASE_URL`
  exactly like `market_data/store.py::get_market_data_store()` already
  did; unset (the default) is byte-identical to before.
- `tools/migrate_to_timescaledb.py` — real, verified data migration
  (`--dry-run`/`--yes`, batched `ON CONFLICT DO NOTHING`, source-vs-
  destination row-count verification). Verified end-to-end against
  synthetic fixtures covering all 19 tables, including a same-data re-run
  proving idempotency (`tests/test_migrate_to_timescaledb.py`, 5 tests) —
  **not yet run against the real production `market_data.db`/`research.db`**,
  deliberately (927 MB/26 MB of real data; the approved plan's own
  Verification section calls that a separate, operator-run step against
  real data later, not something to do automatically). See
  "Recommended Next Task" in `PROJECT_STATE.md`.
- `tools/backup_timescaledb.py` — `pg_dump`-based backup + JSON marker
  (`db_backups/last_backup.json`) `/api/system/health` reads. Unit-tested
  (`tests/test_backup_timescaledb.py`, 8 tests); the actual `pg_dump`
  invocation itself needs an operator with Postgres client tools
  installed to exercise for real (confirmed graceful, correct failure
  when `pg_dump` isn't on PATH — this dev sandbox has none).
- `scripts/start-team.ps1` — team-mode boot (builds the frontend once,
  starts the backend bound to this machine's Tailscale address via
  `--allow-network-exposure`, serving the built dashboard from the same
  process). Syntax-verified and confirmed to fail safely (clear message,
  exit 1, no side effects) before any network-exposing action when
  `FUTURES_BOT_DATABASE_URL`/Tailscale aren't ready — a full run needs a
  real Tailscale network to actually exercise end-to-end, not done this
  session.
- `GET /api/system/health` (`api/routes/system.py`, `api/schemas.py::SystemHealthOut`/
  `DatabaseHealthOut`) — backend "ok", database configured/ok/latency/error
  (`db/health.py`), process uptime, last-backup timestamp, and an honest
  "connected users" estimate (`api/connected_users.py`'s process-local
  sliding-window IP tracker, explicitly documented as approximate given
  there's still no auth system). 23 new tests across
  `tests/test_api_system_health.py`/`test_connected_users.py`/`test_db_health.py`.
- `db/engine.py::prime_engine` + `api/app.py::_maybe_prime_db_engine` —
  `config.py::DeploymentSettings.pool_size`/`max_overflow`/
  `pool_recycle_seconds` were defined last session but never actually
  reached `get_engine()` (every real call site used its bare defaults) —
  closed that gap at API startup.
- Mission Control's `StatusBar`/`HealthGrid` now consume real data from
  `/api/system/health` (`frontend/src/api.ts::getSystemHealth`,
  `frontend/src/types.ts::SystemHealth`) for exactly the fields the
  team-deployment plan scoped for them — version/environment/uptime in
  `StatusBar`, a conditionally-rendered "Team Database" card in
  `HealthGrid` (only shown when `database.configured`). Every other
  Mission Control section is unchanged, still mock, per that plan's own
  scope.
- `docs/ARCHITECTURE.md`'s PERSISTENCE section, `CLAUDE.md`'s doc table
  and File Ownership table (`TEAM_DEPLOYMENT.md`, `src/futures_bot/db/`,
  `alembic/`, `scripts/start-team.ps1`, `tools/migrate_to_timescaledb.py`/
  `backup_timescaledb.py`), `TEAM_DEPLOYMENT.md` itself (server setup,
  Tailscale setup, onboarding, updating the server, troubleshooting) all
  updated/finalized to match what's actually built and verified, not what
  was planned.

**Fixed (found during this session's own live-server verification, not
via a pre-existing test)** — see `KNOWN_ISSUES.md` for full detail on each:
- **ISSUE-010**: `db/schema.py`'s `bars.id` had no way to auto-generate a
  value on Postgres (`autoincrement=True` only applies to a single-column
  primary key, and `id` deliberately isn't one) — would have raised `NOT
  NULL` on the first real insert. Fixed with `Identity(always=False)`.
- **ISSUE-011**: `PgMarketDataStore.fetch_sync_runs`/`fetch_gaps`/
  `contract_rolls` returned native `datetime` objects (Postgres
  `TIMESTAMPTZ`) where `MarketDataStore`'s SQLite equivalents always
  returned a plain string — caused a real 500 (`pydantic.ValidationError`)
  hitting `GET /api/market-data/overview` against the live server.
  Fixed with `pg_store.py::_isoformat_datetimes`.
- **ISSUE-012**: `tools/migrate_to_timescaledb.py` always reported "0
  newly inserted" for every table, even on the first real run — trusted
  `result.rowcount` for a multi-row `INSERT ... ON CONFLICT DO NOTHING`,
  the exact pitfall `pg_store.py::upsert_bars`'s own docstring already
  documents and avoids one file away. Data landed correctly either way;
  only the reporting was wrong. Fixed with `.returning()` + `fetchall()`.
- **ISSUE-013**: the general test suite was not hermetic w.r.t.
  `FUTURES_BOT_DATABASE_URL` — with it exported (exactly what a developer
  doing this session's own work would have in their shell), 41 tests with
  no connection to Postgres failed, because none of their fixtures
  guarded against that variable and `get_store()`/`get_market_data_store()`
  silently routed them through the live shared instance instead of each
  test's own isolated SQLite tmp file. New `tests/conftest.py`: an
  autouse fixture clears it for every test by default; the handful of
  test modules that genuinely need a live database opt back in
  explicitly via a `live_database_url` fixture.

**Also fixed:** a doc gap found during this session's boot-checklist
reconciliation, not a code bug — the Mission Control frontend scaffold
(`frontend/src/pages/MissionControl.tsx` + `components/mission-control/`)
landed in the prior (paused) session but was never mentioned in that
session's `PROJECT_STATE.md`/`CHANGELOG.md` write-up. Backfilled into both
under today's date.

**Verified**
- Full test suite with `FUTURES_BOT_DATABASE_URL` unset: 1250 passed, 29
  skipped (every live-server test, by design), 0 failed — confirms every
  existing single-developer setup is still byte-identical. Re-confirmed
  after the ISSUE-013 fix (`tests/conftest.py`) to prove that fix didn't
  change this baseline.
- Full test suite with `FUTURES_BOT_DATABASE_URL` set: first attempt
  showed 41-46 failures across two separate causes — a forgotten
  concurrent second `pytest` process racing on shared state (a testing-
  process mistake, not a product bug), and ISSUE-013 itself (a real,
  independent finding). After the ISSUE-013 fix, in genuine isolation:
  see the exact re-confirmed count directly below this bullet once
  available in `PROJECT_STATE.md`.
- Concurrency (plan's own Verification item #4): 30 concurrent requests
  (mixed `/api/system/health`, `/api/market-data/overview`,
  `POST /api/backtest/run`, `/api/backtests`) against the shared
  TimescaleDB instance via a thread pool — 0 errors, 0 pool exhaustion,
  every backtest run got a distinct id (no cross-request corruption).
- `market_data.db`'s tables (verified last session) confirmed untouched
  by this session's `research.db` work (`bars` still 0 rows after this
  session's test data was truncated).

No commit hash yet — not committed this session.

## 2026-07-27 — Team deployment (Tailscale + TimescaleDB): market_data.db vertical slice [IN PROGRESS]

Approved plan: `C:\Users\sstae\.claude\plans\polymorphic-nibbling-lamport.md`.
Paused mid-implementation, blocked on Docker/WSL2 not yet available in
the dev environment — see `PROJECT_STATE.md`'s "Recommended Next Task"
for the exact resume point. This entry covers what actually landed.

**Added**
- `src/futures_bot/db/` (new package): `engine.py` (pooled SQLAlchemy
  `Engine`, `pool_pre_ping` reconnect, `FUTURES_BOT_DATABASE_URL`),
  `health.py` (`check_database_health()`), `schema.py` (Postgres/
  TimescaleDB `Table`/`MetaData` for `market_data.db`'s 5 tables +
  hypertable conversion statements).
- `src/futures_bot/market_data/pg_store.py::PgMarketDataStore` — full
  Postgres port of `MarketDataStore`, all 23 methods, verified identical
  public method surface via `tests/test_market_data_store_parity.py`
  (5 new tests).
- `market_data/store.py::get_market_data_store()` — the one seam every
  caller now goes through instead of constructing `MarketDataStore(default_db_path())`
  directly; branches on `FUTURES_BOT_DATABASE_URL`. `api/market_data_store.py`
  re-exports it as a thin wrapper for `api/`-layer imports.
- `config.py::DeploymentSettings` (`deployment.environment`, pool
  tuning) on `Settings`, defaulting to today's behavior; documented
  (commented out) in `config.example.yaml`.
- `db` optional dependency group in `pyproject.toml` (`sqlalchemy`,
  `psycopg[binary]`, `alembic`).
- **Mission Control frontend scaffold** (plan item #7's frontend half):
  `frontend/src/pages/MissionControl.tsx` + 7 components under
  `frontend/src/components/mission-control/` (`StatusBar`, `HealthGrid`,
  `AlertCenter`, `ActivityFeed`, `QuickActions`, `RoadmapPanel`,
  `SummaryCards`), wired in as the app's new index route (`App.tsx`/
  `Layout.tsx` gained a "Home" nav entry; the prior index `Dashboard`
  route moved to `/dashboard`). Layout/component structure is real;
  every value is placeholder data in `missionControlData.ts` (documented
  in that file's own header) pending the real `/api/system/health` route
  below. This bullet was missing from the original write-up of this
  entry — added 2026-07-27 during the next session's boot-checklist
  reconciliation (CLAUDE.md §6).

**Changed**
- `cli.py`, `backtest/data.py`, `market_data/scheduler.py`,
  `research_server/paper_trader.py`, `api/market_data_service.py`,
  `api/live_session.py`, `api/services.py::list_datasets` — all 16 real
  call sites repointed at `get_market_data_store()`. `list_datasets`
  additionally gained graceful-degradation handling for a configured-
  but-unreachable Postgres database (catches and logs, returns what it
  has, never 500s).
- `MarketDataStore`/`PgMarketDataStore` both gained a `.location`
  property (fixes a real bug this refactor would otherwise have shipped:
  `api/market_data_service.py` read `.path`, which doesn't exist on the
  connection-based Postgres store — `.location` is credential-safe on
  both, via SQLAlchemy's own `render_as_string(hide_password=True)` for
  Postgres).

**Verified**
- Full test suite: 1226 passed (1221 + 5 new), confirming the SQLite
  path — every existing single-developer setup — is byte-identical to
  before this session.
- `db/health.py` tested against both "unconfigured" and "configured but
  genuinely unreachable" (bogus host, confirmed graceful ~5s timeout
  failure via `connect_timeout`, not a hang or crash).
- Postgres DDL (`db/schema.py`) and the `ON CONFLICT` statements in
  `pg_store.py` compile correctly against SQLAlchemy's real `postgresql`
  dialect.
- **Not yet verified against an actual running Postgres/TimescaleDB
  server** — no Docker available in the dev sandbox this session; this
  is the very next step once Docker/WSL2 finishes installing.

**Not started:** `research.db`/`TradeStore`'s Postgres port (untouched,
SQLite-only, unaffected by anything above), Alembic setup, the data
migration script, `deploy/docker-compose.yml`'s new service, team-mode
deployment scripts, `/api/system/health`, Mission Control's real-data
wiring (the scaffold itself landed — see Added above), `TEAM_DEPLOYMENT.md`.

No commit hash yet — not committed this session.

## 2026-07-27 — Platform Verification Phase 2: Context Engine dedup + stale-context fix

Resolves the two findings Platform Verification Phase 1's audit
surfaced (measurement-only at the time, deliberately not fixed there).
No new functionality, no classification/scoring/API changes — goal was
"a cleaner and faster implementation with zero behavioral changes."
Full report: `docs/PLATFORM_VERIFICATION_PHASE2.md`.

**Changed**
- `src/futures_bot/context/regime.py`: `classify_regime` gained optional
  `precomputed_volatility`/`precomputed_adx` parameters, defaulted to a
  private `_UNSET` sentinel (not `None`) so "not supplied" and "supplied
  as `None`" are distinguishable. Every existing caller (nothing passes
  these yet outside `context_engine.py`) is on the exact same
  recompute-it-yourself code path as before.
- `src/futures_bot/context/trend.py`: `analyze_trend` gained an optional
  `precomputed_adx` parameter, same sentinel pattern (its own private
  `_UNSET`, not shared with `regime.py`'s).
- `src/futures_bot/context/context_engine.py`: `ContextEngine.build_context`
  now computes `analyze_volatility()` and a new `_compute_adx()` exactly
  once per bar and passes both into `_classify_regime`/`_classify_trend`,
  which forward them to `regime.py`/`trend.py` as the new precomputed
  parameters. `DEFAULT_ADX_PERIOD` imported from `regime.py` (single
  source of truth, unchanged).
- `src/futures_bot/engine.py`: `TradingEngine.__init__` now resets
  `self.strategy.context = None` immediately at construction; `on_bar`
  now sets `self.strategy.context` unconditionally every bar (the real
  `MarketContext` when `ContextMode.ENABLED` + `strategy.uses_context`,
  `None` otherwise) instead of only ever writing it in the `ENABLED`
  branch and leaving every other mode's prior value untouched.

**Added**
- `tests/test_platform_verification_phase2.py` (6 tests): precomputed-vs-
  fresh-computation equivalence for both `classify_regime` (including the
  early-return-on-`UNKNOWN`-volatility edge case, where `None` must be
  used as-is, not retried) and `analyze_trend`; `ContextEngine.build_context`
  actually using the shared computation (`regime_context.adx` ==
  `trend_context.adx` == a single direct `adx()` call); full
  `MarketContext` equivalence against calling every dimension
  independently the old way; a construction-time-only stale-context
  reset test.
- `docs/PLATFORM_VERIFICATION_PHASE2.md`: architecture summary, files
  changed, before/after performance (wall-clock, marginal cost,
  `cProfile` call counts, memory), risks eliminated, remaining risks,
  final recommendation.
- `KNOWN_ISSUES.md` ISSUE-008 (duplicate ADX/volatility computation) and
  ISSUE-009 (stale `Strategy.context`), both logged and marked Resolved.

**Changed (tests)**
- `tests/test_platform_verification_phase1.py`:
  `TestKnownLimitationStaleStrategyContextAcrossReusedInstances` renamed
  to `TestStaleStrategyContextAcrossReusedInstancesIsResolved`; its
  assertion inverted from "the stale value persists" (the bug) to "the
  stale value is `None`" (the fix).

**Performance (before vs. after, same 400/800/1,600-bar methodology as
Phase 1's own benchmark)**
- `cProfile` call counts (800-bar `OBSERVE` backtest, 800 `build_context`
  calls): `adx()` 1,585 → **800**; `analyze_volatility()` 1,600 → **800**
  — duplication completely eliminated, not merely reduced.
- Marginal context-generation cost (`OBSERVE − OFF`, environment-noise-
  normalized): 400 bars 528.3ms → 340.2ms (−35.6%); 800 bars 2,165.0ms →
  1,210.3ms (−44.1%); 1,600 bars 10,705.0ms → 5,820.7ms (−45.6%) —
  converging toward the ~45% Phase 1's own `cProfile` breakdown
  predicted (ADX+volatility were ~90% of context-gen CPU, each halved).
- Peak memory delta (`tracemalloc`, `OBSERVE` minus `OFF`): modest ~7-10%
  reduction across all three bar counts (fewer intermediate allocations,
  not the primary target).
- Methodology note: a live `futures_bot.api` background process was
  found consuming significant CPU mid-benchmark (confirmed via
  `Get-CimInstance Win32_Process` — the user's own process, not a
  leftover diagnostic; stopped by the user, not by this session).
  Numbers above are from the subsequent clean-environment measurement.

**Regression verification**
- Full suite: **1,221 passed, 0 failed** (1,215 + 6 new).
- No difference found in trades, metrics, P&L, or reports — the
  existing 8-metric exact-equality backward-compatibility checks
  (`tests/test_platform_verification_phase1.py::TestBackwardCompatibilityRegression`)
  all still pass unchanged.

**Confidence:** both verified Phase 1 findings are fixed and
independently proven correct (not just "tests still pass"). Safe to
proceed to the first context-aware strategy. Remaining, non-blocking
item: the O(n²) full-replay cost is unchanged in kind (only reduced in
constant factor) — a known, by-design characteristic per
`docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`, not a defect.

No commit hash yet — not committed this session.

## 2026-07-27 — Platform Verification Phase 1: Market Context Integration Audit

Independent, read-only audit of the immediately preceding session's
work (Phase 9's `TradingEngine` integration) — no new features, no
optimizations, one genuine finding surfaced and documented, not fixed,
per this phase's explicit scope. Full report:
`docs/PLATFORM_VERIFICATION_PHASE1.md`.

**Added**
- `tests/test_platform_verification_phase1.py` (25 tests): exact-equality
  regression checks for all eight requested backward-compatibility
  metrics (entry/exit timestamps, entry/exit prices, exit reasons, net
  P&L, win rate, profit factor) across a pre-integration-style call,
  explicit `OFF`, and explicit `OBSERVE`; `MarketContext` completeness
  (all nine fields present on every trade) and internal-consistency
  checks (bare enum fields always agree with their nested rich object);
  a same-bar close-then-reenter "flip" stress test (28+ rapid trades,
  zero cross-contamination between a closing trade's context and a new
  entry's); an explicit no-duplicate-generation call-count check; a
  check that `RiskManager.record_trade` never reads the new
  `entry_context` field; and a reproduction test documenting the
  stale-`Strategy.context`-across-reused-instances finding (see below).
- `docs/PLATFORM_VERIFICATION_PHASE1.md`: the full audit report --
  PASS/WARN/FAIL for every verification item, both findings with exact
  reproductions, performance measurements, and a confidence level.

**Findings (neither fixed, per this phase's "measure and report only"
scope)**
1. **Duplicate ADX/volatility computation.** `cProfile` against a real
   800-bar `OBSERVE` backtest attributes ~71% of all context-generation
   CPU time to `strategy.indicators.adx` and ~19% to
   `volatility.analyze_volatility`/`atr_series` -- ~90% combined.
   `context/regime.py`'s `classify_regime` and `context/trend.py`'s
   `analyze_trend` both call `adx()` directly with identical
   `bars`/`period`; `context/context_engine.py`'s `_classify_volatility`
   and `regime.classify_regime` (internally) both call
   `analyze_volatility()`. Both are correct, both are wasteful -- call
   counts confirm exactly ~2x the necessary invocations (1,585 `adx()`
   calls / 1,600 `analyze_volatility()` calls for 800 `build_context`
   invocations). Recommended as a high-value future optimization
   (thread one computed result through `build_context` instead of each
   classifier re-deriving it); not implemented here.
2. **Stale `Strategy.context` across a reused instance.** Reproduced
   directly: if one `Strategy` instance is passed to two separate
   `TradingEngine`/`run_backtest` calls -- first `ContextMode.ENABLED`
   (sets `self.context`), then `ContextMode.OFF` or a non-opted-in path
   -- the first run's context is still there, since neither `OFF` nor a
   non-opted-in path ever resets it. Confirmed harmless for every
   *current* caller (`cli.py`, `api/services.py`, `api/live_session.py`,
   `research_server/paper_trader.py` all construct a fresh strategy
   instance per run -- verified by direct inspection of all four call
   sites). Recommended defensive fix (unconditionally reset
   `Strategy.context = None` at the start of every bar, before the
   `ENABLED`+`uses_context` re-population check) for before any future
   tooling reuses instances across runs; not implemented here.

**Verified, all PASS unless noted:**
- `ContextMode.OFF` never calls `ContextEngine.build_context` (spy-
  verified across a full backtest).
- `ContextMode.OBSERVE` generates exactly one `MarketContext` per
  processed bar (call count == bar count, no duplicate timestamps) and
  cannot influence decisions -- structurally, not just empirically: it
  never executes the line that sets `Strategy.context` at all.
- `ContextMode.ENABLED` matches `OFF` even for a strategy that opts in
  (`uses_context=True`) but never reads `self.context` -- the weakest,
  most permissive case.
- Execution flow traced statement-by-statement in `engine.py`'s
  `on_bar`; `list(self.bars)` always includes the bar that just closed
  (no off-by-one).
- All 8 backward-compatibility metrics identical, exact equality, not
  tolerance-based.
- Every completed trade's `entry_context` carries all 9 required
  fields; bare enum fields always agree with their nested object
  (structural guarantee from `context_engine.py`'s construction,
  verified directly).
- No circular imports (subprocess-verified for every affected module);
  no memory leaks (`ContextEngine` holds no accumulating state,
  `_pending_entry_context` always cleared after use); no new thread-
  safety concerns (same unsynchronized-instance-attribute pattern the
  engine already used).

**Database changes**
- None.

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None. This phase changed no production code -- audit and tests only.

**Verified:** full suite green (1215 passed, 0 failed -- 1190 + 25
new). Initial wall-clock performance measurements showed severe
variance (2.0s-28.5s for the identical 800-bar `OBSERVE` backtest
across attempts) traced to a runaway diagnostic process from an
over-ambitious first benchmark attempt (10,000 bars) contending for
CPU in the background -- identified and killed; final numbers are from
clean, uncontended runs cross-checked against `cProfile`'s CPU-
attributed call graph (immune to wall-clock contention) for the
relative breakdown.

**Confidence level: High.** Zero correctness defects found. Two
findings, both documented with exact reproductions and clear,
un-implemented recommendations.

**Commit hashes**
- Not yet committed as of this entry.

## 2026-07-27 — Market Context Engine Phase 9: Integration into TradingEngine + A/B Comparison

Wires the (already complete, Phase 8) Context Engine into the actual
trading path -- backtesting, paper trading, and live trading, all
through the same `TradingEngine`/`run_backtest`/`build_engine` -- without
changing any existing trading behavior.

**Added**
- `engine.ContextMode` (`OFF`/`OBSERVE`/`ENABLED`): a three-way switch so
  "context is generated and recorded" and "context can influence a
  decision" are two separately verifiable guarantees, not one on/off
  flag. `OFF` is the default for `TradingEngine.__init__`,
  `engine.build_engine`, and `backtest.runner.run_backtest` -- every
  existing caller gets exactly the pre-integration engine, and
  `ContextEngine.build_context` is never called at all in this mode.
  `OBSERVE` generates exactly one `MarketContext` per processed bar
  (built in a new `TradingEngine._build_market_context`, step 0 of
  `on_bar`, before anything else touches the bar) and attaches it to
  every completed trade -- but never sets `Strategy.context`, so no
  strategy can read it; decisions are therefore provably identical to
  `OFF`. `ENABLED` additionally sets `Strategy.context`, but only for a
  strategy whose own `uses_context` is `True`.
- `models.Trade.entry_context: Optional[MarketContext] = None` (new,
  purely-additive field; `TYPE_CHECKING`-guarded forward reference to
  `context.models.MarketContext` to avoid a real import cycle with
  `models.py` -- the same pattern `context/models.py` already uses for
  its own forward references). Attached by
  `TradingEngine._record_trade` -- the single shared closing path for
  every trade regardless of *why* it closed (a resting stop/target
  resolving, a risk-forced flatten, a strategy exit) -- via
  `dataclasses.replace`, since `Trade` is frozen. Neither
  `PaperBroker` nor `TradovateBroker` ever sets it or references
  `context/` at all.
- `strategy.base.Strategy.context: Optional[MarketContext] = None` and
  `Strategy.uses_context: bool = False` (new, optional attributes;
  `TYPE_CHECKING`-guarded reference, never a real import --
  `Strategy` is a protected interface, and this stays that way
  regardless of whether a real import would technically work today).
  `Strategy.on_bar`'s call signature is completely unchanged -- no
  existing strategy needed a single line of modification.
- `backtest/context_comparison.py`: `compare_context_impact(settings,
  strategy_factory, bars, **run_backtest_kwargs)` runs the same
  strategy/parameters/dataset/date-range twice through the *same*
  `run_backtest` -- once `OBSERVE` (the baseline: decision-identical to
  `OFF`, but every trade carries its `entry_context`, which the
  comparison needs to explain differences), once `ENABLED` (may
  differ) -- and diffs the two trade lists. Each changed trade is
  classified `UNCHANGED`/`REMOVED_BY_CONTEXT`/`ADDED_BY_CONTEXT`/
  `ENTERED_DIFFERENTLY`/`EXITED_DIFFERENTLY` and carries the
  `MarketContext`/`EnvironmentScore` that explains it.
  `MetricsSummary.from_metrics` reads Net Profit/Win Rate/Profit
  Factor/Expectancy/Max Drawdown/Total-Winning-Losing Trades/Average
  Trade/Average Winner/Average Loser/Largest Winner/Largest Loser
  straight off the existing `BacktestMetrics` -- nothing recomputed.
  Documented path-dependence caveat: only the *first* divergence
  between the two runs is guaranteed to be directly explained by the
  strategy's own context rule -- once one run skips a trade the other
  took, the two runs' open-position timelines can drift apart, so later
  changes may be downstream consequences rather than independently
  explained.
- `tests/test_engine_context_integration.py` (18 tests): one
  `MarketContext` per processed bar (and zero calls in `OFF`), correct
  (entry-time, not exit-time) context attached to every trade including
  a risk-forced flatten, no circular imports (subprocess-verified),
  no duplicate `ContextEngine` construction, and -- the most important
  guarantee -- `OFF` byte-identical to a pre-integration-style backtest,
  `OBSERVE` decision-identical to `OFF`, `ENABLED` decision-identical to
  `OFF`/`OBSERVE` for a strategy that hasn't opted in, plus explicit
  proof that `Strategy.context` stays `None` in every case that isn't
  `ENABLED` + `uses_context=True`.
- `tests/test_backtest_context_comparison.py` (8 tests): no duplicate
  pipeline (both runs verified to go through `run_backtest`), metrics
  match the underlying `BacktestMetrics` exactly, a non-context-aware
  existing-style strategy reports zero changed trades, a test-only
  context-aware strategy (skips entries below a fixed environment-score
  threshold) reports real, correctly-classified changes each carrying
  context/score, and a fresh strategy instance is used per run (no
  state leaking between the baseline and enabled runs).

**Changed**
- `src/futures_bot/engine.py`: `TradingEngine.__init__` gained
  `context_mode`/`context_engine` parameters (defaults `ContextMode.OFF`/
  auto-constructed); `on_bar` gained step 0 (`_build_market_context`,
  wrapped in a broad `except` so a `context/` defect can never crash a
  live/paper/backtest run -- the same defensive posture `_safe_signal`
  already takes toward strategy code); `_handle_signal` gained a
  `market_context` parameter, capturing it into
  `self._pending_entry_context` on a successful entry; `_record_trade`
  attaches and clears it. `build_engine` gained matching
  `context_mode`/`context_engine` parameters, passed straight through.
- `src/futures_bot/backtest/runner.py`: `run_backtest` gained matching
  `context_mode`/`context_engine` parameters, passed straight through
  to `TradingEngine`.
- `tests/test_context.py`/`tests/test_context_engine_validation.py`:
  three tests whose premise -- "`context/` has zero reference from the
  trading side" -- this integration deliberately supersedes were
  rewritten to check the *actual* current invariant instead (risk/
  brokers still have zero reference; `engine.py`'s reference is real,
  by design, but gated by `ContextMode.OFF`'s default;
  `strategy/base.py`'s reference is `TYPE_CHECKING`-only).
- `docs/ARCHITECTURE.md`: "Market Context Engine" section heading and
  several paragraphs updated from "not wired in yet" to describe the
  actual integration; new "Integration into `TradingEngine`" subsection
  covering the execution flow, the two bugs found and fixed, and the
  A/B comparison framework.

**Database changes**
- None.

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None. `context_mode` defaults to `OFF` everywhere; `Trade.entry_context`
  and `Strategy.context`/`uses_context` are purely additive with safe
  defaults; every existing call site (`cli.py`, `research_server/`,
  `api/services.py`, every existing test) is unaffected.

**Risks discovered and fixed during this integration's own manual
verification** (not just via the tests written alongside it):
1. `TradingEngine.bars` is a bounded `collections.deque`
  (`_MIN_BARS_RETAINED`), which does not support the slice indexing
  `context/liquidity.py`/`volatility.py` rely on for their trailing
  windows (`TypeError: sequence index must be integer, not 'slice'`).
  Fixed by converting to `list(self.bars)` once per bar before calling
  `ContextEngine.build_context` -- every classifier already only reads
  a trailing slice of whatever it's given, so this changes nothing
  about correctness, only compatibility with the container type.
2. `dataclasses.replace` (needed to attach `entry_context` to a frozen
  `Trade`) returns a *new* object. The first draft only used it inside
  `_record_trade`'s local scope, discarding the enriched copy the
  moment the method returned -- `PaperBroker.trades` (what
  `run_backtest` actually reads via `list(broker.trades)` to build
  `BacktestMetrics.trades`) still held the original, un-enriched trade.
  Caught by inspecting `broker.trades` directly after a manual test
  run, before trusting the formal test suite; fixed by writing the
  enriched trade back into `self.broker.trades[-1]`.

**Verified:** full suite green (1190 passed, 0 failed -- 1163 + 27
new). `git status`/`git diff` confirm the only files outside
`tests/`/`docs/` touched are `src/futures_bot/models.py`,
`src/futures_bot/strategy/base.py`, `src/futures_bot/engine.py`,
`src/futures_bot/backtest/runner.py`, and the new
`src/futures_bot/backtest/context_comparison.py` -- no changes to
`brokers/`, `risk/`, `research/`, `api/`, `research_server/`, or any
bundled strategy file.

**Commit hashes**
- Not yet committed as of this entry.

## 2026-07-27 — Market Context Engine Phase 8: Completion and Validation

An 11-part phase making the Market Context Engine production-ready as
an **independent, unintegrated subsystem**. Explicitly did not touch
`TradingEngine`/`Strategy`/`RiskEngine`/backtesting/live trading/broker
code — confirmed by `git diff` and dedicated tests (see Part 8 below).

**Added**
- `src/futures_bot/context/trend.py`: `analyze_trend`/`TrendContext` —
  standalone `TrendState` (BULLISH/BEARISH/NEUTRAL/UNKNOWN), simpler
  and available with far less history than `regime.py`'s
  volatility-coupled `MarketRegime` composite. Reuses
  `research.regime.classify_trend` (direction) and
  `strategy.indicators.adx` + `regime.py`'s own
  `ADX_TRENDING_THRESHOLD`/`ADX_CONFIDENCE_SCALE` (confidence, same
  scale as `regime.py`'s). 14 new tests.
- `src/futures_bot/context/liquidity.py`: `analyze_liquidity`/
  `LiquidityContext` — `LiquidityState` (THIN/NORMAL/DEEP/UNKNOWN) from
  relative volume (current bar vs. trailing average), reusing
  `strategy.indicators.sma`. Genuinely new classification logic (no
  existing general-purpose liquidity classifier anywhere in this
  codebase — `strategy/trend_pullback/strategy.py`'s own `volume_ratio`
  is strategy-local analytics, not reusable without an inappropriate
  `context/` → `strategy/` dependency), following the same
  trailing-window-ratio shape `volatility.py` already established. 19
  new tests.
- `src/futures_bot/context/risk.py`: `assess_risk`/`RiskContext` —
  `RiskState` (LOW/ELEVATED/HIGH/UNKNOWN) as a **pure composite** of
  already-real `volatility_state`/`market_regime` — no new market-data
  analysis, exactly what this method's own Phase-1 stub docstring
  anticipated. Unrelated to, and never consulted by,
  `risk.manager.RiskManager` (naming collision only, verified by an
  import-inspection test). 17 new tests.
- `src/futures_bot/context/scoring.py`: `ScoringConfig` dataclass
  centralizing all six scoring weights (previously hardcoded module
  constants) — `trend_weight`/`volatility_weight`/`session_weight`/
  `structure_weight`/`liquidity_weight`/`risk_weight`.
  `DEFAULT_SCORING_CONFIG` reproduces every pre-Phase-8 scoring
  behavior exactly (verified: all 21 pre-existing scoring tests pass
  unchanged). `score_environment`/`with_environment_score` gained an
  optional `config` parameter; `ContextEngine.__init__` gained an
  optional `scoring_config` parameter. 9 new tests
  (`TestConfigurableScoring`).
- `src/futures_bot/context/analytics.py`: `analyze_context_batch`/
  `ContextAnalyticsReport` — developer/research distribution analytics
  over a batch of already-built `MarketContext` snapshots: session/
  regime/volatility/trend/liquidity/risk distributions, environment-
  score and confidence numeric summaries (min/max/mean/median/stdev),
  and UNKNOWN-frequency per dimension, plus a human-readable `.render()`
  text report. No UI, not wired into `ContextEngine` (a separate,
  post-hoc analysis layer, the same relationship
  `market_data/validation.py` has to the sync pipeline). 12 new tests.
- `tests/test_context_engine_validation.py` (16 tests): no-circular-
  imports (every `context/` submodule imports standalone in a fresh
  subprocess), no-duplicated-logic (ATR/ADX/SMA/`classify_trend` each
  defined exactly once, everywhere else only imports them), no-
  duplicated-calendars/session/regime/volatility-state enum
  definitions, module independence from `risk.manager`/`brokers`/
  `engine.py` (checked in both directions), determinism (identical
  inputs produce an identical `MarketContext`; no wall-clock or
  randomness anywhere in `context/`), missing-data safety, `UNKNOWN`
  correctness, and confidence validity across many scenarios.
- `tools/benchmark_context_engine.py`: measures
  `ContextEngine.build_context`'s average/worst-case timing and peak
  memory across 50–50,000-bar histories. Results in
  `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`.
- Four new documentation reports:
  `docs/CONTEXT_ENGINE_LOOKAHEAD_AUDIT.md` (module-by-module look-ahead
  reasoning for all 8 dimensions plus the combined score — no issues
  found), `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`,
  `docs/CONTEXT_ENGINE_COVERAGE.md` (status/tests/confidence-model/
  dependencies/integration-readiness table), and
  `docs/CONTEXT_ENGINE_ARCHITECTURE_REVIEW.md` (final confirmation the
  engine remains a pure, unintegrated information layer).

**Changed**
- `src/futures_bot/context/models.py`: added `trend_context`/
  `liquidity_context`/`risk_context` fields to `MarketContext`
  (`TYPE_CHECKING`-guarded imports, same pattern as every other nested
  `*Context` field); `to_dict`/`from_dict` updated.
- `src/futures_bot/context/context_engine.py`: `_classify_trend`/
  `_classify_liquidity`/`_classify_risk` are now real (were `UNKNOWN`
  stubs); `ContextEngine.__init__` gained an optional `scoring_config`
  parameter (default `None` → `scoring.DEFAULT_SCORING_CONFIG`).
- `src/futures_bot/context/liquidity.py`: performance fix found during
  Part 5's benchmark — `analyze_liquidity` now converts only the
  trailing `lookback` bars to `Decimal` instead of the entire history
  passed in, since `sma()` only ever used the trailing slice anyway.
  Verified output-identical: all pre-existing liquidity tests pass
  unchanged, plus a new dedicated large-history equivalence test.
- `tests/test_context.py`: one assertion updated (a "few bars" fixture
  now correctly classifies `trend_state` as `NEUTRAL` rather than
  `UNKNOWN`, since `TrendState` is real now and only needs 2 closes);
  one new regression test for backward-compatible `from_dict`
  deserialization of a pre-Phase-8-shaped dict (missing the newer
  context keys entirely, not just null).
- `docs/ARCHITECTURE.md`: "Market Context Engine" section substantially
  rewritten — new Trend/Liquidity/Risk subsections, the configurable
  scoring system, a consolidated "Validation guarantees" summary, a
  "Known limitations" summary, and a "Future integration plan" section,
  per this phase's own documentation requirements.

**Database changes**
- None.

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None. Every new field defaults to `None`/`UNKNOWN`; `ScoringConfig`
  parameters all default to the pre-Phase-8 values; the liquidity
  optimization is output-identical.

**Verified:** full suite green (1163 passed, 0 failed — 1074 + 89 new).
`git status`/`git diff` confirm zero changes outside `context/`,
`tests/test_context*.py`, `tools/benchmark_context_engine.py`, and the
five persistent documentation files plus `docs/` — verified in both
directions (nothing outside `context/` references it either). Found
and fixed one real bug during this phase's own audit work: an early
draft of the circular-import check used `importlib.reload()`, which
mints duplicate Enum class objects and silently broke `is`-identity for
every test running afterward in the same process — caught by a test
failure, root-caused, and replaced with genuine subprocess isolation
before being trusted.

**Commit hashes**
- `280d52b`.

## 2026-07-27 — Market Context Engine: Context Scoring System (Phase 2f)

**Added**
- `src/futures_bot/context/scoring.py`: `score_environment(context:
  MarketContext) -> EnvironmentScore` and `EnvironmentScore` (`score`
  0-100, `confidence`, `reasons`, `breakdown`). Combines every existing
  `MarketContext` dimension into one reading of how favorable current
  conditions look for a systematic strategy to operate in *generally* --
  **not a directional (bullish/bearish) signal**. Six dimensions each
  contribute a signed value scaled by a documented maximum weight:
  Trend 20, Volatility 15, Session 10, Structure 20, Liquidity 15, Risk
  -10 -- chosen to reproduce the task's own worked example exactly
  (`20+15+10+20+15-10 == 70`, manually verified against the live module
  with a hand-built `MarketContext` before trusting it as a test
  assertion). Trend combines whichever of `regime_context.confidence`
  (only when the regime is directional) and
  `timeframe_alignment.alignment_score` are available, rather than
  picking one. Volatility/session/structure/liquidity/risk each read one
  existing field (`volatility_state`, `session_context.liquidity_expectation`,
  `structure_context`, `liquidity_state`, `risk_state`) through a small
  lookup table. The total is clamped to `[0, 100]`. A dimension with no
  data (`UNKNOWN`, or its sub-context missing) contributes exactly `0.0`
  and is excluded from both `reasons` and the `confidence` fraction --
  never a fabricated guess. `confidence` is the fraction of the six
  dimensions that actually had data, independent of whether the score
  itself is high or low (a dedicated test constructs a "full data,
  worst possible readings" scenario proving this explicitly).
  `reasons` matches the task's own second worked example verbatim for
  the scenario it describes ("Strong trend alignment", "Normal
  volatility", "Good liquidity"). `to_dict`/`from_dict`.
- **Real "missing data" case, not hypothetical:** `liquidity_state`/
  `risk_state` are still `UNKNOWN` stubs everywhere else in this
  codebase (`ContextEngine._classify_liquidity`/`_classify_risk`), so
  they *always* contribute `0.0` through the real engine today. A
  dedicated test asserts today's practical score ceiling (65, not 100)
  as a result -- an honest reflection of incomplete data, not a bug,
  and something this module needs zero changes to pick up correctly
  once those two phases land with real classification.
- `tests/test_context_scoring.py` (20 tests): the exact worked example
  (`70/100` with the exact per-dimension breakdown), the reason-phrasing
  example, confidence aggregation (full data, zero data, partial data,
  and the "full data but all-bad readings still full confidence" case),
  clamping at both the 0 and 100 ends, missing-data handling (including
  today's real liquidity/risk stub ceiling), full `MarketContext`
  integration (`to_dict`/`from_dict` round-trip through
  `ContextEngine.build_context`), `EnvironmentScore` serialization, and
  an explicit "information only, never decides trades" check that
  inspects both `scoring.py`'s own imports and confirms `engine.py`/
  `strategy/base.py` don't import it either.

**Changed**
- `src/futures_bot/context/models.py`: added
  `MarketContext.environment_score: Optional[EnvironmentScore]`
  (`TYPE_CHECKING`-guarded import, same pattern as the other nested
  `*Context` fields); `to_dict`/`from_dict` updated to serialize it.
- `src/futures_bot/context/context_engine.py`: `build_context` now
  constructs the `MarketContext` in two steps -- the base
  `MarketContext(...)` call with every other field, then
  `scoring.with_environment_score` (a `dataclasses.replace`) to fill in
  `environment_score`, since the score necessarily depends on every
  other field already being set and cannot be computed inside the same
  constructor call that produces those fields.
- `docs/ARCHITECTURE.md`: "Market Context Engine" section updated — new
  "Context Scoring System" subsection covering the weight design, the
  information-only guarantee, and today's real liquidity/risk-stub
  ceiling.

**Database changes**
- None.

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None. `environment_score` is purely additive (defaults to `None`).

**Verified:** full suite green (1074 passed, 0 failed -- 1054 + 20 new).
`git status`/`git diff` confirm zero changes to `strategy/`,
`engine.py`, `risk/`, `brokers/`, `backtest/`, `research/regime.py` --
only `context/`, its new test file, and docs changed. Manually verified
the task's own worked example (hand-built `MarketContext` reproducing
`70/100` exactly) and the second reasons-only example before trusting
them as passing tests.

**Commit hashes**
- Not yet committed as of this entry.

## 2026-07-27 — Market Context Engine: Market Structure Context (Phase 2e)

**Added**
- `src/futures_bot/context/structure.py`: `analyze_structure(timestamp,
  symbol, bars, swing_window=3, structure_lookback=3)` and
  `StructureContext` (`trend`, `support`, `resistance`,
  `distance_to_support`, `distance_to_resistance`, `structure_confidence`).
  Detects confirmed swing highs/lows via a standard fractal definition
  (a bar's high/low strictly beats every high/low within `swing_window`
  bars on both sides), then classifies structure by comparing the most
  recent confirmed swings pairwise: higher-highs/higher-lows votes
  bullish, lower-highs/lower-lows votes bearish. `structure_confidence`
  is the winning side's share of all pairwise comparisons (unanimous
  agreement is 1.0; a tied/mixed read is `NEUTRAL` at 0.0; too few
  confirmed swings is `UNKNOWN` at 0.0). `support`/`resistance` are the
  nearest confirmed swing low/high bracketing the current price
  (falling back to the most recent swing if price has broken through
  every level); `distance_to_support`/`distance_to_resistance` are the
  plain price differences. Reuses `context/models.py`'s existing
  `TrendState` enum rather than inventing a fourth bullish/bearish/
  neutral vocabulary (no existing swing/support-resistance equivalent
  to reuse elsewhere in this codebase — genuinely new work, same
  disclosure `regime.py` gives for liquidity/risk). `to_dict`/`from_dict`
  (Decimal price fields serialize as plain floats, parsed back via
  `Decimal(str(...))` -- matching this codebase's established
  Decimal-JSON convention, e.g. `feeds/massive.py`). **Strictly
  descriptive** -- `StructureContext` carries no broker/risk-manager/
  engine reference of any kind; this module never generates a trade and
  never overrides a strategy's own signal, verified directly by a test
  that inspects the module's own import statements.
- **Confirmation lag is explicitly documented as distinct from a
  look-ahead violation:** confirming a swing point requires looking at
  bars chronologically *after* the candidate bar -- but since every bar
  this module ever sees is already-completed history (the same "bars up
  to and including the bar that just closed" convention every classifier
  in this package holds callers to), those later bars are themselves
  already in the past relative to `timestamp`. The practical effect is
  that the most recent `swing_window` bars simply have no confirmed
  swing near them yet -- the honest behavior of a real-time swing
  detector, not a bug. Verified directly by a dedicated test.
- `tests/test_context_structure.py` (17 tests): higher-highs/
  higher-lows and lower-highs/lower-lows detection (via a deterministic
  zigzag fixture), support below and resistance above current price,
  distance-to-level correctness, the task's own worked example shape, a
  flat/no-structure case, missing data (too few bars, zero bars),
  confirmation-lag-is-not-leakage (including that a shorter prefix is
  unaffected by bars appended after it), serialization, integration
  into `MarketContext`, and an explicit "does not generate trades / does
  not override strategies" import-boundary check. Caught and fixed a
  real test-fixture bug during manual verification (not just via the
  tests written alongside it): an early zigzag-generator draft produced
  *rising* cycle lows regardless of the intended drift direction, which
  would have silently mislabeled a "downtrend" fixture as bullish.

**Changed**
- `src/futures_bot/context/models.py`: added
  `MarketContext.structure_context: Optional[StructureContext]`
  (`TYPE_CHECKING`-guarded import, same pattern as the other nested
  `*Context` fields); `to_dict`/`from_dict` updated to serialize it.
- `src/futures_bot/context/context_engine.py`: `_classify_structure` is
  now real (delegates to `structure.analyze_structure`); `build_context`
  adds `confidence_scores["structure"]` only when `trend` is not
  `UNKNOWN`. The remaining three `_classify_*` methods (trend,
  liquidity, risk) are unchanged stubs.
- `docs/ARCHITECTURE.md`: "Market Context Engine" section updated — new
  "Market Structure Context" subsection covering the fractal swing
  definition, the confirmation-lag-vs-leakage distinction, and the
  support/resistance fallback rule.

**Database changes**
- None.

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None. `structure_context` is purely additive (defaults to `None`).

**Verified:** full suite green (1054 passed, 0 failed -- 1037 + 17 new).
`git status`/`git diff` confirm zero changes to `strategy/`,
`engine.py`, `risk/`, `brokers/`, `backtest/`, `research/regime.py` --
only `context/`, its new test file, and docs changed. Manually verified
uptrend/downtrend zigzag fixtures and the confirmation-lag scenario
against the live module before trusting them as passing tests.

**Commit hashes**
- `a95b4df`.

## 2026-07-27 — Market Context Engine: Multi-Timeframe Context (Phase 2d)

**Added**
- `src/futures_bot/context/timeframe.py`: `classify_timeframe_alignment(
  timestamp, symbol, bars_by_timeframe)` and `TimeframeAlignment`
  (`alignment`, `alignment_score`). Combines trend direction across five
  canonical timeframes (`TIMEFRAME_ORDER`: `1m`/`5m`/`15m`/`1h`/`1d`) into
  one reading. Reuses `research.regime.classify_trend` per timeframe --
  the same function `regime.py` already uses for its own trend signal --
  mapping its "bullish"/"bearish"/"sideways" onto `context/models.py`'s
  existing `TrendState` enum (`BULLISH`/`BEARISH`/`NEUTRAL`). A timeframe
  absent, empty, or with fewer than 2 completed bars is simply left out
  of `alignment` -- "missing timeframe data" handled safely, matching
  this package's established "absence means not recorded" convention.
  `alignment_score` is the magnitude (`[0.0, 1.0]`) of a rank-weighted
  average direction across whichever timeframes are present (weights
  1 through 5, ascending with `TIMEFRAME_ORDER`, so a longer horizon
  counts for more without the wildly mismatched ratios raw minute-
  durations would imply). `to_dict`/`from_dict`.
- **Look-ahead safety, stricter than any prior single-stream classifier
  in this package:** it's realistic for a caller to hand over a coarser
  timeframe's series where the *last* bar is still forming (e.g. at
  09:05, a 1-hour series' 09:00 bar has opened but not closed) even
  though its timestamp alone looks "at or before now" -- a naive
  `timestamp <= now` filter would wrongly accept it. `timeframe.py`
  instead tracks each timeframe's actual bar duration and only keeps a
  bar once `bar.timestamp + duration <= timestamp` -- its close time has
  genuinely passed -- before handing anything to `classify_trend`.
- `tests/test_context_timeframe.py` (14 tests): the task's own worked
  example shape (1m neutral, 5m/15m/1h bullish, daily absent), full
  agreement across all five timeframes, an even bullish/bearish split,
  missing data in several forms (no mapping, empty mapping, empty/short
  per-timeframe series), a dedicated in-progress-bar leakage scenario
  (constructing exactly the 09:00-1h-bar-not-yet-closed-at-09:05 case),
  a no-future-leakage test for a single stream, serialization, and
  integration into `MarketContext`.

**Changed**
- `src/futures_bot/context/models.py`: added
  `MarketContext.timeframe_alignment: Optional[TimeframeAlignment]`
  (`TYPE_CHECKING`-guarded import, same pattern as
  `session_context`/`volatility_context`/`regime_context`);
  `to_dict`/`from_dict` updated to serialize it.
- `src/futures_bot/context/context_engine.py`: `_classify_timeframe_alignment`
  is now real (delegates to `timeframe.classify_timeframe_alignment`);
  `build_context` gained an optional `bars_by_timeframe` parameter
  (independent of the existing `bars`/`self.timeframe`; a caller
  wanting its own timeframe counted includes it under the matching
  key), and adds `confidence_scores["timeframe_alignment"]` only when
  at least one timeframe actually produced a reading. The remaining
  three `_classify_*` methods (trend, liquidity, risk) are unchanged
  stubs.
- `docs/ARCHITECTURE.md`: "Market Context Engine" section updated — new
  "Multi-Timeframe Context" subsection covering the reuse, the
  alignment-score formula, and the in-progress-bar leakage risk this
  module specifically guards against.

**Database changes**
- None.

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None. `timeframe_alignment` is purely additive (defaults to `None`);
  `build_context`'s new `bars_by_timeframe` parameter is optional and
  defaults to `None`, so every existing call site is unaffected.

**Verified:** full suite green (1037 passed, 0 failed -- 1023 + 14 new).
`git status`/`git diff` confirm zero changes to `strategy/`,
`engine.py`, `risk/`, `brokers/`, `backtest/`, `research/regime.py` --
only `context/`, its new test file, and docs changed. Manually verified
the task's own worked example shape and the in-progress-bar leakage
scenario against the live module before trusting them as passing tests.

**Commit hashes**
- `d0522e7`.

## 2026-07-27 — Market Context Engine: Market Regime Detection (Phase 2c)

**Added**
- `src/futures_bot/context/regime.py`: `classify_regime(timestamp,
  symbol, timeframe, bars, adx_period=14)` and `RegimeContext` (`regime`,
  `confidence`, `adx`, `trend_direction`, `volatility_ratio`). Classifies
  overall market behavior into one of five mutually exclusive
  `MarketRegime` values: `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`,
  `HIGH_VOLATILITY`, `LOW_VOLATILITY`. Combines three reused signals,
  nothing re-derived: `strategy.indicators.adx` (trend strength —
  conventional ADX >= 25 "actually trending" threshold, Wilder's own
  convention), `research.regime.classify_trend` (trend direction —
  bullish/bearish/sideways, already look-ahead-safe and already used
  for this purpose elsewhere), and `context.volatility.analyze_volatility`
  (volatility signal, inheriting its look-ahead safety for free).
  Priority when signals disagree, documented explicitly: extreme
  volatility dominates trend/range labeling; otherwise a strong,
  directional ADX reading wins; otherwise low volatility is its own
  label; otherwise the default is `RANGING`. `confidence` always in
  `[0.0, 1.0]` via a small formula per branch (trending: `min(1.0,
  adx/50.0)` — matches the task's own worked example, ADX 39 -> 0.78
  exactly). No parameter optimization this phase — every threshold is
  either reused from elsewhere in this codebase or an unmodified
  textbook default (ADX 25). `to_dict`/`from_dict`.
- `tests/test_context_regime.py` (21 tests): trending up/down, ranging,
  high/low volatility, extreme volatility taking priority over a
  concurrent trend (per the documented priority order), a confidence
  formula check against the task's own worked example, missing data (no
  bars, and a partial-data case where volatility classifies but ADX
  still can't), a dedicated no-future-leakage test, serialization, and
  integration into `MarketContext`.

**Changed**
- `src/futures_bot/context/models.py`: `MarketRegime` enum redefined
  from Phase 1's placeholder set (`TRENDING`/`RANGING`/`VOLATILE`) to
  the exact 5-value taxonomy above — confirmed zero usages outside
  `context/`'s own tests before changing it (same discipline as
  `SessionPhase`'s Phase 2a rename). Added
  `MarketContext.regime_context: Optional[RegimeContext]`
  (`TYPE_CHECKING`-guarded import, same pattern as
  `session_context`/`volatility_context`); `to_dict`/`from_dict`
  updated to serialize it.
- `src/futures_bot/context/context_engine.py`: `_classify_regime` is now
  real (delegates to `regime.classify_regime`); `build_context` adds
  `confidence_scores["regime"]` only once `classify_regime` actually
  produced a non-`UNKNOWN` reading. The remaining three `_classify_*`
  methods (trend, liquidity, risk) are unchanged stubs.
- `tests/test_context.py`: updated 6 assertions that referenced the old
  `MarketRegime` member names (`TRENDING`/`RANGING` literals still valid
  where unchanged, `TRENDING` -> `TRENDING_UP` elsewhere); renamed and
  re-commented 2 tests whose docstrings claimed `market_regime` was
  still an unconditional stub (it's real now, just still `UNKNOWN` on
  insufficient data, which is a different reason).
- `docs/ARCHITECTURE.md`: "Market Context Engine" section updated — new
  "Market Regime Detection" subsection covering the three reused
  signals, the priority order, and the confidence formulas; the
  "reuse, don't duplicate" paragraph narrowed to what's actually still
  a stub (standalone `trend_state`, `liquidity_state`, `risk_state`).

**Database changes**
- None.

**API changes**
- None.

**Frontend changes**
- None.

**Breaking changes**
- None to any existing caller (nothing outside `context/` references
  `MarketRegime`'s renamed members — confirmed via `grep` before
  renaming). `regime_context` is purely additive (defaults to `None`).

**Verified:** full suite green (1023 passed, 0 failed — 1002 + 21 new).
`git status`/`git diff` confirm zero changes to `strategy/`,
`engine.py`, `risk/`, `brokers/`, `backtest/`, `research/regime.py` —
only `context/`, its test files, and docs changed. Manually verified all
four regime scenarios (trending up/down, ranging, high/low volatility)
and the extreme-volatility-takes-priority case against the live module
before trusting them as passing tests -- caught and fixed a test-data
bug this way (concatenated bar segments without carrying the price
`base` forward, producing an artificial discontinuity that looked like
a real price move).

**Commit hashes**
- `8c2d2a0`.

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
- `4236392`.

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
