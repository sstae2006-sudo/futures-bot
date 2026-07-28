# KNOWN_ISSUES.md

Every discovered bug is listed here. Don't delete an entry unless
verified fixed — instead mark it Resolved with a date and commit.

---

### ISSUE-001 — Turtle-data import: contract placeholder + century-pivot timestamp shift (RESOLVED)

- **Severity:** Critical (data integrity)
- **Description:** See
  [docs/DATABASE_CORRUPTION_REPORT.md](../docs/DATABASE_CORRUPTION_REPORT.md)
  for full detail, including a documented near-miss during the repair
  itself. Two real, confirmed bugs, both confined to `bars` rows with
  `source = 'turtletrader'`:
  1. **`contract` hardcoded to `"CONTINUOUS"` (342,494 rows, 100% of
     turtle data):** discarded the real per-file ticker instead of
     recording it. `product_code` was already correctly the full
     ticker (e.g. `CL00F`) and stays that way — it must, since the
     schema's uniqueness index is `(product_code, resolution,
     timestamp)` and this archive is hundreds of individual, heavily
     date-overlapping contract histories, not a single rolled series.
     Treating `product_code` as a "bogus" field and rewriting it to a
     generic root during the first repair attempt collided every
     overlapping trading day across contracts and silently dropped
     ~90% of the data (342,494 → 34,331 rows) — caught immediately,
     rolled back from the verified backup before anything was lost for
     good, and fixed properly. See the report's Resolution section.
  2. **Century-pivot timestamp shift (17,668 rows, Copper/`HG` only):**
     bars from 1959–1968 were stored with `timestamp` shifted exactly
     +100 years (e.g. 1964 stored as 2064), because the date parser
     relied on Python's default `%y` two-digit-year pivot.
- **Files involved:** `market_data.db` (table `bars`, columns
  `contract` for bug 1, `timestamp` for bug 2). Root-caused and fixed
  in `tools/import_turtle_data.py` (bug 1) and
  `tools/convert_turtle_data.py` (bug 2, `parse_date`, now a fixed
  50-year pivot).
- **Possible cause:** N/A — resolved.
- **Current status:** **Resolved 2026-07-26.** Backup taken and
  verified (integrity check + row-count match) before any write.
  342,494 rows deleted and re-imported from source
  (`turtle_raw/`/`turtle_converted/`, regenerated with the fixed date
  parser); final count matches the pre-repair total exactly, zero
  future-shifted timestamps, zero `CONTINUOUS` placeholders remain.
  Full test suite: 896 passed (881 pre-existing + 15 new regression
  tests in `tests/test_tools_turtle_import.py`), 0 failures. One file,
  `turtle_raw/GC001F.txt`, uses a different schema entirely (headered,
  `MM/DD/YYYY`) and has a malformed filename — correctly rejected by
  the new import-time validation rather than silently imported;
  flagged, not fixed (see report).

---

### ISSUE-002 — Test-order-dependent flake in research-server test

- **Severity:** Low (test reliability, not a product bug)
- **Description:**
  `tests/test_api_research_server.py::TestNightlyAndFindings::test_run_nightly_now_updates_the_status_the_dashboard_reads`
  fails when the full test suite runs, but passes when run in
  isolation.
- **Files involved:** `tests/test_api_research_server.py`,
  `src/futures_bot/research/trade_store.py` (the failure observed was
  `sqlite3.OperationalError: duplicate column name: dataset_version`
  in `TradeStore.ensure_schema`).
- **Possible cause:** Shared mutable state across tests in the same
  process (a singleton, or a shared db file/connection) — the schema
  migration's "add column if missing" check appears to race or see
  stale state when other tests run first. Not root-caused.
- **Current status:** Unresolved. Confirmed reproducible: fails in
  full-suite runs (2026-07-26), passes standalone every time tried.
  Not currently blocking — treat a full-suite failure at this specific
  test as this known flake, not a new regression, until it's actually
  root-caused.

---

### ISSUE-003 — `pyproject.toml` missing runtime dependencies (RESOLVED)

- **Severity:** Critical (blocked `python -m futures_bot.api` entirely
  on a bare install)
- **Description:** `fastapi`/`uvicorn`/`python-multipart`/`openpyxl`
  were gated behind an optional `api` extra instead of base
  `dependencies`, so `pip install -e .` alone couldn't run the API.
  Separately, `tzdata` wasn't declared as a dependency at all —
  `contracts.py` resolves `ZoneInfo("America/Chicago")` at import time,
  which raises `ZoneInfoNotFoundError` on Windows and on slim Linux
  images with no system IANA timezone database (both Dockerfiles had a
  bolted-on `RUN pip install tzdata` workaround instead of a real
  dependency).
- **Files involved:** `pyproject.toml`, `deploy/Dockerfile`,
  `deploy/Dockerfile.api`.
- **Possible cause:** N/A (root cause identified and fixed).
- **Current status:** **Resolved 2026-07-26**, commit `8946de8`
  (dependency fix) and `647c3ea`/Dockerfile edits in the same session.
  Verified in a clean venv: `pip install -e .` + `python -m
  futures_bot.api` boots and serves real requests with zero manual
  installs.

---

### ISSUE-004 — `bars` table schema has drifted from `store.py`'s current `_SCHEMA`

- **Severity:** Medium (structural drift, not (yet) a data-correctness
  problem — every row observed so far still holds valid data)
- **Description:** Discovered 2026-07-27 by the new permanent validator
  (`docs/DATABASE_VALIDATION.md`, `schema_mismatch:bars` check). The
  live `bars` table's actual column definitions no longer match
  `store.py`'s `_SCHEMA` string: every column is missing the `NOT
  NULL` constraint the current schema declares, `id`'s type is `INT`
  (not `INTEGER`) and it never became the `PRIMARY KEY AUTOINCREMENT`
  the schema now specifies (matching the long-standing observation in
  `docs/DATABASE_CORRUPTION_REPORT.md`'s "Out of Scope" section that
  `id` is `NULL` for every row, regardless of source), and
  `created_at` has no `DEFAULT` clause. `CREATE TABLE IF NOT EXISTS`
  never retroactively alters an already-existing table, so this table
  was created under an older revision of `_SCHEMA` and was never
  migrated when the schema string changed.
- **Files involved:** `market_data.db` (table `bars`),
  `src/futures_bot/market_data/store.py` (`_SCHEMA`, `ensure_schema`).
- **Possible cause:** `ensure_schema()` only ever runs `CREATE TABLE IF
  NOT EXISTS`/`CREATE INDEX IF NOT EXISTS` — by design, idempotent and
  non-destructive, but with no migration path for a column-level schema
  change on an existing table.
- **Current status:** Diagnosed, not fixed — out of scope for the
  validator-building task that found it ("do not modify existing
  data"). A real fix needs an explicit migration (e.g. `ALTER TABLE`
  where SQLite allows it, or a rebuild-and-copy where it doesn't) and,
  per CLAUDE.md section 8, is a database-schema change requiring
  explicit approval before it's attempted.

---

### ISSUE-005 — Genuine OHLC invariant violations in raw `US80Z` source data

- **Severity:** Low (isolated to one 1980 Treasury Bond contract; not a
  systemic import/parsing defect)
- **Description:** Discovered 2026-07-27 by the new permanent validator
  (`high_lt_open`/`high_lt_close`/`low_gt_open`/`low_gt_close`
  checks). 13 bars total for `US80Z` violate basic OHLC ordering (e.g.
  `high < open`). Confirmed by inspecting `turtle_raw/US80Z.txt`
  directly: the bad values are already present in the raw historical
  source file (e.g. row `801027,69.9375,69.59464,68.59464,68.59464,...`
  — high 69.59464 < open 69.9375) — not introduced by
  `convert_turtle_data.py` or `import_turtle_data.py`.
- **Files involved:** `market_data.db` (table `bars`, `product_code =
  'US80Z'`), `turtle_raw/US80Z.txt` (source).
- **Possible cause:** A data-entry or vendor error in the original
  1980s-era historical archive this project imported from. Not a bug
  in this project's own code.
- **Current status:** Diagnosed, not fixed — out of scope for the
  validator-building task that found it ("do not modify existing
  data"). If ever repaired, the safest approach is almost certainly
  deleting and re-importing just `US80Z` from `turtle_raw/` after
  manually correcting the specific bad rows in the source file (there
  is no "correct" value to derive from within the data itself).

---

### ISSUE-006 — `npm run dev` can't start Vite on Windows (kill-vite.js kills itself)

- **Severity:** High (blocks the frontend's documented entry point,
  though `scripts\start.ps1` now works around it)
- **Description:** Discovered 2026-07-27 while building and verifying
  `scripts\start.ps1`. `frontend/package.json`'s `dev` script is
  `node scripts/kill-vite.js && vite`. `kill-vite.js` runs `taskkill
  /F /IM node.exe` on Windows to clear any stale Vite process — but
  image-name matching doesn't exclude the calling process, so it kills
  its own node.exe process too. The script therefore always exits
  nonzero, `&&` never reaches `vite`, and `vite` never starts.
  Confirmed empirically, twice, in isolation (`node
  scripts/kill-vite.js` alone exits 1; the full chain produces npm's
  script-start banner and then nothing — no Vite banner, no error,
  empty stderr, because the process was force-killed mid-script rather
  than exiting gracefully).
- **Files involved:** `frontend/scripts/kill-vite.js`,
  `frontend/package.json` (`dev` script).
- **Possible cause:** `taskkill /F /IM node.exe` was presumably written
  assuming it only matches *other* node.exe processes, not realizing
  Windows image-name matching includes the caller.
- **Current status:** Not fixed — this was discovered while building
  the startup-scripts task, which was explicitly told not to modify
  existing project files. `scripts\start.ps1` works around it by
  launching `frontend\node_modules\.bin\vite.cmd` directly (with
  `--host 127.0.0.1`) instead of `npm run dev`, which also sidesteps
  the redundant kill-vite.js step entirely (start.ps1 already frees
  port 5173 by-port before launching). Manual frontend development
  should use `npx vite --host 127.0.0.1` in `frontend/` until this is
  fixed directly. A real fix would be narrowing kill-vite.js to only
  kill node processes actually holding port 5173 (e.g. via the same
  by-port lookup `scripts/_common.ps1` uses) instead of every node.exe
  on the machine — flagged for a future session/explicit approval
  rather than done here.

---

### ISSUE-007 — Vite binds IPv6 loopback (`[::1]`) by default, not `127.0.0.1`

- **Severity:** Low (only matters for tooling that hardcodes
  `127.0.0.1`; browsers resolve `localhost` fine either way)
- **Description:** Discovered 2026-07-27 alongside ISSUE-006.
  `frontend/vite.config.ts` sets `server.port: 5173` but no `host`, so
  Vite binds `localhost` — which Node resolves to the IPv6 loopback
  (`[::1]`) first on this machine, not `127.0.0.1`. Confirmed
  empirically: `Invoke-WebRequest http://127.0.0.1:5173` failed to
  connect against a Vite instance that was simultaneously serving
  `http://localhost:5173` (200 OK) correctly.
- **Files involved:** `frontend/vite.config.ts`.
- **Possible cause:** No explicit `host` configured; platform/Node
  version-dependent `getaddrinfo` ordering decides which loopback
  address `localhost` resolves to first.
- **Current status:** Not fixed in `vite.config.ts` (not modified, per
  this task's constraints). `scripts\start.ps1` works around it by
  passing `--host 127.0.0.1` explicitly when it launches `vite.cmd`
  directly. Doesn't affect the browser (which resolves `localhost`
  correctly) or anyone running `npx vite` and browsing to whatever URL
  it prints — only matters for scripts/tools that hardcode
  `127.0.0.1`.

---

### ISSUE-008 — Context Engine computed ADX and volatility twice per bar (RESOLVED)

- **Severity:** Medium (performance only — no incorrect output; every
  duplicate call produced the identical, correct result)
- **Description:** Found by Platform Verification Phase 1's `cProfile`
  audit (2026-07-27): `context/regime.py`'s `classify_regime` and
  `context/trend.py`'s `analyze_trend` each independently called
  `strategy.indicators.adx(bars, period=14)` with identical arguments;
  `context/context_engine.py`'s `_classify_volatility` and
  `regime.classify_regime` (internally) each independently called
  `context.volatility.analyze_volatility(...)`. Measured at ~90% of all
  context-generation CPU time for an 800-bar `OBSERVE` backtest (1,585
  `adx()` calls, 1,600 `analyze_volatility()` calls, both ≈2x the 800
  `build_context` invocations that triggered them).
- **Files involved:** `src/futures_bot/context/regime.py`,
  `src/futures_bot/context/trend.py`,
  `src/futures_bot/context/context_engine.py`.
- **Possible cause:** Each classifier module was built independently
  (Phase 8) and reused `adx()`/`analyze_volatility()` directly rather
  than coordinating with sibling modules that needed the same value.
- **Current status:** **Resolved 2026-07-27** (Platform Verification
  Phase 2, `docs/PLATFORM_VERIFICATION_PHASE2.md`). `classify_regime`/
  `analyze_trend` gained optional `precomputed_volatility`/
  `precomputed_adx` parameters (sentinel-defaulted, so every existing
  caller is unaffected); `ContextEngine.build_context` now computes
  both once per bar and passes them through. Verified byte-identical
  output (`tests/test_platform_verification_phase2.py`) and confirmed
  by `cProfile`: 800/800 calls for 800 `build_context` invocations, down
  from 1,585/1,600. Per-run context-generation cost reduced ~35-46%
  (converging toward ~45% as bar count grows).

---

### ISSUE-009 — Stale `Strategy.context` could leak across a reused Strategy instance (RESOLVED)

- **Severity:** Low (never affected any current caller — every existing
  call site constructs a fresh `Strategy` instance per run — but a
  real, reproducible latent risk for any future caller that reused one)
- **Description:** Found by Platform Verification Phase 1 (2026-07-27):
  `TradingEngine.on_bar` only ever set `self.strategy.context` inside
  the `ContextMode.ENABLED` + `strategy.uses_context` branch, never
  resetting it in any other mode. Reusing the same `Strategy` instance
  across two separate engine runs — first `ENABLED` (sets `.context`),
  then `OFF` or a non-opted-in `ENABLED` run — left the first run's
  value visible during the second. Reproduced directly via
  `tests/test_platform_verification_phase1.py`.
- **Files involved:** `src/futures_bot/engine.py` (`TradingEngine.__init__`,
  `TradingEngine.on_bar`).
- **Possible cause:** The original integration only wrote to
  `Strategy.context` when there was something real to write, without
  considering that a reused instance needs the *absence* of context
  written explicitly too.
- **Current status:** **Resolved 2026-07-27** (Platform Verification
  Phase 2). `TradingEngine.__init__` now resets `self.strategy.context =
  None` at construction; `on_bar` now sets it unconditionally every bar
  (the real value or `None`), closing the gap automatically with no
  caller-side discipline required. Verified by the inverted
  `TestStaleStrategyContextAcrossReusedInstancesIsResolved` and a new,
  narrower construction-time-only test.

---

### ISSUE-010 — `bars.id` had no way to auto-generate a value on Postgres (RESOLVED)

- **Severity:** High (would have raised `NOT NULL` violation on the very
  first `PgMarketDataStore.upsert_bars` insert against a real server)
- **Description:** Found 2026-07-27, the first time `db/schema.py`'s
  `bars` table was actually created against a live TimescaleDB instance
  (`alembic upgrade head`) rather than only compiled against SQLAlchemy's
  dialect. `id` was declared `autoincrement=True` but is deliberately
  *not* the table's primary key (the real uniqueness constraint is
  `uq_bars_identity` on `(product_code, resolution, timestamp)` — see that
  column's own comment). Postgres/SQLAlchemy only auto-generates a
  sequence default for `autoincrement=True` on a single-column primary
  key; without one, `id` had no default at all, and `pg_store.py::upsert_bars`
  never supplies it explicitly (matching the SQLite store's own
  `rowid`-equivalent behavior).
- **Files involved:** `src/futures_bot/db/schema.py` (`bars` table).
- **Possible cause:** The schema was written and dialect-verified before
  any real server existed to catch this — a plausible-looking
  `autoincrement=True` that only fails at actual insert time, not at
  `CREATE TABLE` time.
- **Current status:** **Resolved 2026-07-27.** Changed to
  `Identity(always=False)`, which Postgres supports independently of
  primary-key status (`GENERATED BY DEFAULT AS IDENTITY`). Verified
  against the live `deploy/docker-compose.yml` `timescaledb` service:
  `\d bars` shows the identity default; `tests/test_pg_market_data_store_live.py`
  exercises real inserts end-to-end.

---

### ISSUE-011 — `PgMarketDataStore` returned native `datetime` where `MarketDataStore` always returned a string (RESOLVED)

- **Severity:** High (a real `pydantic.ValidationError` 500 on
  `GET /api/market-data/overview`, the very first time that route was hit
  against a live server)
- **Description:** Found 2026-07-27 via a real HTTP 500, not a unit test:
  `fetch_sync_runs`/`fetch_gaps`/`contract_rolls` read Postgres's native
  `TIMESTAMPTZ` columns, which psycopg returns as real `datetime` objects.
  `MarketDataStore`'s identically-shaped SQLite methods return the raw
  TEXT string unconverted (no `datetime.fromisoformat()` call) — every
  caller up the stack (`market_data_service.py`, `api/schemas.py::SyncRunOut`/
  `GapOut`/`MarketDataOverviewOut.last_sync_at`) already assumed a plain
  string, because that's all SQLite had ever produced. `tests/test_market_data_store_parity.py`
  (signature-only) and the first pass of `tests/test_pg_market_data_store_live.py`
  (which asserted `PgMarketDataStore`'s own values in isolation, never
  cross-checking against what the SQLite side actually returns for the
  same method) both missed this — it only surfaced by exercising the real
  HTTP route end to end.
- **Files involved:** `src/futures_bot/market_data/pg_store.py`
  (`contract_rolls`, `fetch_sync_runs`, `fetch_gaps`).
- **Possible cause:** `coverage()`'s `Coverage.earliest`/`.latest` are
  real `datetime` objects on *both* backends (SQLite explicitly parses
  them; Postgres returns them natively) — that method's parity was never
  in question. The three dict-returning methods above were the only ones
  where SQLite's "TEXT, so always already a string" and Postgres's
  "TIMESTAMPTZ, so always already a `datetime`" diverge.
- **Current status:** **Resolved 2026-07-27.** Added
  `pg_store.py::_isoformat_datetimes`, applied to all three methods'
  return dicts, restoring the "identical dict shape on both backends"
  guarantee `test_market_data_store_parity.py` already promises callers.
  Regression tests added directly to `tests/test_pg_market_data_store_live.py`
  (asserting `isinstance(value, str)` for every affected field) and a new
  `tests/test_api_market_data_live.py` exercising the actual route against
  a live server end to end.

---

### ISSUE-012 — `tools/migrate_to_timescaledb.py` always reported 0 rows newly inserted (RESOLVED)

- **Severity:** Medium (cosmetic/reporting only — the migration itself
  wrote every row correctly, confirmed by the script's own source-vs-
  destination row-count verification; only the per-table "N newly
  inserted" figure printed during the run was wrong)
- **Description:** Found 2026-07-27 during this script's first real run
  against a live server: every table reported "0 newly inserted, N
  already present" even on the very first run against an empty
  destination. `migrate_table()` trusted the driver-reported
  `result.rowcount` from a multi-row `INSERT ... ON CONFLICT DO NOTHING`
  — exactly the pitfall `pg_store.py::upsert_bars`'s own docstring already
  documents and avoids elsewhere in this same codebase: `rowcount` is not
  reliably "rows actually inserted" for that statement shape under every
  driver.
- **Files involved:** `tools/migrate_to_timescaledb.py` (`migrate_table`).
- **Possible cause:** Written by analogy to a plain `INSERT` (where
  `rowcount` is reliable) without re-deriving that `ON CONFLICT DO
  NOTHING` changes the semantics — despite the correct pattern already
  existing one file away.
- **Current status:** **Resolved 2026-07-27.** Switched to
  `.returning(*dest_columns)` + `len(result.fetchall())`, the same fix
  already applied to `upsert_bars`. Verified directly: a first run against
  an empty destination now reports the real per-table counts, and a
  second (idempotent) run against the same data reports 0 — covered by
  `tests/test_migrate_to_timescaledb.py`.

---

### ISSUE-013 — Full test suite is not hermetic w.r.t. `FUTURES_BOT_DATABASE_URL` (RESOLVED)

- **Severity:** High (silently corrupts dozens of unrelated test results
  for any developer who has `FUTURES_BOT_DATABASE_URL` set in their shell
  for legitimate team-deployment work, then runs the general suite)
- **Description:** Found 2026-07-27 while re-verifying the full suite
  against a live server as part of this session's own team-deployment
  work: with `FUTURES_BOT_DATABASE_URL` exported in the shell, 41 tests
  failed that have no connection to Postgres at all —
  `test_api_services.py`, `test_cli_market_data.py`,
  `test_research_server_nightly_jobs.py`,
  `test_research_server_paper_trader.py`, etc. Root cause: none of those
  tests' fixtures guard against `FUTURES_BOT_DATABASE_URL` — they isolate
  via `FUTURES_BOT_RESEARCH_DB`/`FUTURES_BOT_MARKET_DATA_DB` (per-test
  tmp-file SQLite paths), a pattern that predates this variable mattering
  at all. Once it's set, `get_store()`/`get_market_data_store()`
  transparently route *every* one of those tests through the same live
  shared Postgres instance instead of each getting its own isolated
  SQLite file — cross-test state leakage (a `TestRollDetection` test
  expecting "no active contract yet" instead saw one left over from an
  earlier test/session), not a Postgres-specific bug. Confirmed
  reproducible in a genuinely isolated single pytest process (no
  concurrent second process — that was ruled out first, see the false
  lead below).
- **Files involved:** Every test file using `get_store()`/
  `get_market_data_store()` indirectly (via `TradeStore`/`MarketDataStore`
  API routes/CLI commands) without controlling `FUTURES_BOT_DATABASE_URL`
  itself — i.e., nearly the whole suite. Fixed centrally in
  `tests/conftest.py` rather than touching every affected file.
- **Possible cause:** `FUTURES_BOT_DATABASE_URL` didn't exist as a
  meaningful switch before this session's team-deployment work — no test
  ever needed to guard against it, because no developer's shell would
  plausibly have it set for an unrelated reason before now.
- **False lead ruled out first:** an earlier full-suite run (with this
  variable set) showed a similar-looking but different failure set while
  a second, forgotten background `pytest` invocation was still running
  concurrently against the same live database — that one really was just
  two processes racing on shared state, confirmed by re-running in
  genuine isolation (`ps aux` showing exactly one pytest process) and
  still seeing 41 failures. Both are real; they're independent findings.
- **Current status:** **Resolved 2026-07-27.** New `tests/conftest.py`:
  a session-wide autouse fixture clears `FUTURES_BOT_DATABASE_URL` for
  every test by default (with a guarded `dispose_engine()` call, since
  `db.engine.get_engine()`'s cached singleton doesn't re-check the env
  var once built — a stale cached Engine from an earlier live test could
  otherwise leak into a later, supposedly-hermetic one even after the env
  var itself is cleared). The handful of test modules that deliberately
  need a live database (`test_pg_market_data_store_live.py`,
  `test_pg_trade_store_live.py`, `test_db_health.py`,
  `test_api_market_data_live.py`, `test_migrate_to_timescaledb.py`) opt
  back in explicitly via a `live_database_url` fixture, depended on by
  each file's central `store`/`client`/cleanup fixture — pytest guarantees
  the autouse clear runs before any fixture that explicitly requests
  something it affects, so this ordering is deterministic, not a race.
  Verified: full suite with `FUTURES_BOT_DATABASE_URL` unset still shows
  the same 1250 passed/29 skipped baseline; with it set to the live
  compose instance, all 1279 run for real with 0 failures (see
  `PROJECT_STATE.md`/`CHANGELOG.md` for the exact confirmation run).

---

### ISSUE-014 — Real production data migration hit `bars.created_at NOT NULL` violation: 32.5% of real rows have NULL `created_at` (RESOLVED)

- **Severity:** High (blocked the real `market_data.db` → TimescaleDB
  migration outright, partway through the `bars` table)
- **Description:** Found 2026-07-28 during the first real (non-synthetic)
  run of `tools/migrate_to_timescaledb.py --yes` against the actual
  production `market_data.db` (927 MB, ~3.5M `bars` rows). The migration
  raised `psycopg.errors.NotNullViolation: null value in column
  "created_at"` partway through the `bars` table (2,377,220 of 3,519,754
  rows already committed via `ON CONFLICT DO NOTHING`, safely resumable).
  Root cause is ISSUE-004 (`bars` schema drift): the live SQLite table
  predates the `created_at NOT NULL DEFAULT` revision and was never
  migrated, so a genuinely large fraction of real rows have no
  `created_at` at all — not a fringe case. Confirmed by direct query:
  1,142,785 of 3,519,756 rows (32.5%), spread across every source
  (`turtletrader` 342,494; `massive_flatfiles` 707,156; `massive` 91,942;
  `autonomous_paper` 1,193). `db/schema.py`'s `bars.created_at` declared
  `nullable=False`, which is correct for the schema *as designed* but not
  for the data actually on disk — the migration's own synthetic-fixture
  tests (`tests/test_migrate_to_timescaledb.py`) never caught this because
  fixtures built through the real `MarketDataStore` always populate
  `created_at` on insert.
- **Files involved:** `src/futures_bot/db/schema.py` (`bars.created_at`),
  `alembic/versions/66fd84a1c6cb_*.py` (new), `market_data.db` (source,
  unchanged — this is a Postgres-destination-only fix).
- **Possible cause:** N/A — resolved.
- **Current status:** **Resolved 2026-07-28.** `bars.created_at` changed
  to `nullable=True` (new inserts still get one via `server_default`),
  matching the source data's true, already-diagnosed state rather than
  fabricating or dropping real rows. New Alembic revision applied to the
  live instance (`alembic upgrade head`); the autogenerated diff also
  proposed `drop_index('bars_timestamp_idx')`, which was deliberately
  removed from the migration before applying it — that index is
  TimescaleDB's own, created automatically by `create_hypertable()` on
  the time dimension, never declared in `schema.py`'s metadata, and
  dropping it for real would have hurt every time-range query's chunk
  exclusion. Migration resumed and completed after the fix — see
  PROJECT_STATE.md's migration write-up for final row counts.

---

### ISSUE-015 — `test_stale_ip_outside_window_is_purged` flaky: `time.monotonic()` clock granularity can beat a 10ms sleep (RESOLVED)

- **Severity:** Low (test reliability only — `ConnectedUsersTracker` itself
  is correct; only reachable in production with an unrealistic
  `window_seconds=0`, never the real 900s default)
- **Description:** Found 2026-07-28 (Stabilization Mode sweep) as a real,
  reproducible failure — not a one-off — in
  `tests/test_connected_users.py::TestConnectedUsersTracker::test_stale_ip_outside_window_is_purged`:
  roughly 30-40% of runs failed, reproducible even in complete isolation
  (no other tests, no system load). Instrumented directly
  (`t_record`/`t_before_count` printed around the `time.sleep(0.01)`):
  `time.monotonic()` on this machine has ~31.25ms granularity (successive
  reads step in exact 0.03125s increments), coarser than the test's 10ms
  sleep. On a run where the sleep didn't cross a tick boundary, both
  `time.monotonic()` calls returned the *exact same value*
  (`delta=0.000000` exactly) — `ConnectedUsersTracker.count()`'s purge
  condition (`last_seen < cutoff`) is then comparing a value to itself,
  correctly evaluating `False`, so the entry is never purged. The
  production code (`connected_users.py`) is not wrong: a strict `<` is the
  right comparison, and this granularity is completely invisible against
  the real 900-second default window — only a test deliberately using
  `window_seconds=0` with a sub-tick sleep can hit it.
- **Files involved:** `tests/test_connected_users.py`.
- **Possible cause:** N/A — resolved.
- **Current status:** **Resolved 2026-07-28.** Sleep increased from
  `0.01`s to `0.05`s — safely above the observed ~31ms granularity.
  Verified: 0 failures in 30 instrumented repro iterations (vs. 10/30
  failing at 10ms), and 8/8 clean `pytest` runs of the full test file
  afterward.

---

### ISSUE-016 — `ResearchServer.start()`/`AutonomousPaperTrader.start()` had a check-then-set race letting two concurrent calls both proceed (RESOLVED)

- **Severity:** Medium (thread-safety correctness gap; not reachable from
  normal single-user dashboard clicks — the race window is far narrower
  than a human double-click — but reachable from any two genuinely
  concurrent callers, e.g. scripted/automated API calls)
- **Description:** Found 2026-07-28 (Stabilization Mode sweep) by reading
  `orchestrator.py`/`paper_trader.py`'s existing lock discipline closely.
  Both `start()` methods checked `self._running` under a lock, released
  the lock, then did substantial slow work (network calls, a blocking
  websocket handshake, building strategy engines) before finally setting
  `self._running = True` under the lock again. Two concurrent `start()`
  calls could both pass the initial check before either actually marked
  itself running, both proceeding into the expensive setup path — for
  `AutonomousPaperTrader`, that means two feeds/thread sets built
  concurrently against the same instance. Confirmed as a *real*,
  reliably-reproducible race, not theoretical: a new regression test
  (`test_concurrent_start_calls_never_both_win`, using an artificially
  slowed fake Contracts session to widen the window) failed 3/3 runs
  against the pre-fix code (both callers won, `FakeMassiveBarFeed`
  produced 2 instances) and passed 3/3 after the fix.
- **Files involved:** `src/futures_bot/research_server/paper_trader.py`
  (`AutonomousPaperTrader.start()`), `src/futures_bot/research_server/orchestrator.py`
  (`ResearchServer.start()`).
- **Possible cause:** Both methods were written to delay the "running"
  flag until every subsystem had actually, successfully started (itself a
  deliberate, documented design choice — see orchestrator.py's own comment
  on the *other* bug this was originally avoiding: claiming "running" too
  early when a later subsystem could still fail). Neither considered a
  second concurrent caller arriving during that window.
- **Current status:** **Resolved 2026-07-28.** `_running` is now claimed
  atomically with the initial check (before any slow work starts) in both
  methods; a wrapping `try/except` releases the claim (`_running = False`)
  if anything downstream fails, including the pre-existing "empty
  strategies" early-return path in `AutonomousPaperTrader.start()` — so a
  failed or no-op start never leaves the tracker permanently stuck
  reporting `running: True`. `ResearchServer.start()`'s existing rollback
  path (stopping whichever subsystems already started) is unchanged; the
  claim is just released before its final `raise`. Verified: the new
  regression test plus the full existing `test_research_server_paper_trader.py`/
  `test_research_server_orchestrator.py`/`test_research_server_nightly_jobs.py`/
  `test_api_research_server.py` suites (31 tests) all pass.
- **Update 2026-07-28, same sweep:** the identical shape existed in a third
  place, `api/live_session.py::LiveSessionManager.start()` (checks
  `self._snapshot.status` under lock, releases it, then does settings load/
  a DB insert/strategy+engine construction before finally changing the
  status) — found by inspection once the pattern was known, then confirmed
  the same way: a new `test_concurrent_start_calls_never_both_win` (widening
  the window via a monkeypatched, artificially slowed `_build_strategy`)
  failed 3/3 against the pre-fix code and passed 3/3 after applying the
  identical fix (status claimed atomically with the check; restored to its
  pre-call value in an `except` wrapping the rest of `start()`). Full
  `test_api_live_session.py` suite (19 tests) passes.

---

### ISSUE-017 — `GET /api/logs` read the entire (unbounded, 9.2 GB) `decisions.jsonl` into memory on every call (RESOLVED)

- **Severity:** High (real crash/latency risk — a multi-GB memory
  allocation and multi-second-to-multi-minute read on every single request
  to this route, not a hypothetical edge case)
- **Description:** Found 2026-07-28 during a Stabilization Mode sweep that
  hit every simple `GET` route through a `TestClient` looking for broken
  endpoints. `GET /api/logs` appeared to hang; instrumented directly and
  found the real cause: `api/services.py::read_logs` called
  `path.read_text(encoding="utf-8").splitlines()` on `logs/decisions.jsonl`
  — an append-only, never-rotated file (see `journal.py::DecisionJournal`)
  — just to keep the last 2000 lines. On this machine, that file had
  reached **9.2 GB / 34,230,897 lines** from long-running autonomous paper
  trading with `log_every_decision: true`. Every call read the whole thing
  into memory before discarding all but the tail.
- **Files involved:** `src/futures_bot/api/services.py` (`read_logs`).
- **Possible cause:** Written when `decisions.jsonl` was small (a single
  backtest/session's worth of decisions); never revisited once autonomous,
  long-running paper trading made continuous unbounded growth the norm.
- **Current status:** **Resolved 2026-07-28.** New `_read_tail_lines`
  helper seeks backward from the end of the file in a bounded, 4 MB
  initial window (geometrically growing only if a pathologically long line
  requires it) instead of reading the whole file. Verified against the
  real 9.2 GB file directly: 2000 lines in **0.022 seconds** (previously
  unmeasured because it never got a chance to finish quickly enough to
  matter). Six new tests (`tests/test_api_services.py::TestReadTailLines`)
  cover small files, `max_lines` exceeding the file's total, empty files,
  a multi-megabyte file forced through a chunk-growth retry (checked
  against a naive full read for correctness), a byte-read-count assertion
  proving it doesn't scale with file size, and an end-to-end check that
  `read_logs()` actually goes through the new helper. Full
  `test_api_services.py` suite (58 tests) passes. Not addressed here (a
  separate, larger question): whether `decisions.jsonl` should ever be
  rotated/archived — this fix makes reading it cheap regardless of size,
  but the file will keep growing forever and could eventually threaten
  disk space on a very long-running deployment.
