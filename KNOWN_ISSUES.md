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

---

### ISSUE-018 — `frontend/src/pages/Live.tsx` rendered nothing but the status badge while a session was `starting` (RESOLVED)

- **Severity:** Low (UX gap, no data loss or incorrect state — just a
  blank-looking page during a real, if normally brief, transition)
- **Description:** Found 2026-07-28 (Stabilization Mode frontend review).
  `Live.tsx` only rendered the "Session" panel for `running`/`stopping` and
  the "Start a session" form for `stopped`/`error` (`canStart`) -- neither
  branch covered `starting`, so a session mid-startup showed only the
  top status badge with no form and no session details. This window got
  meaningfully longer as a direct consequence of the same day's
  ISSUE-016 fix: `LiveSessionManager.start()` now claims `status =
  "starting"` immediately (before contract detection, feed connection,
  etc.), rather than just before the background thread actually starts --
  correct for closing the race, but it also means "starting" is now
  visible for the session's entire slow setup, not just a brief tail
  moment.
- **Files involved:** `frontend/src/pages/Live.tsx`.
- **Possible cause:** Written when "starting" was a near-instantaneous
  transition; never revisited once ISSUE-016 lengthened it.
- **Current status:** **Resolved 2026-07-28.** Added a `starting` branch
  showing a `LoadingState` panel. New regression test in
  `frontend/src/__tests__/Live.test.tsx` (`shows a starting indicator
  while the session is still coming up`) asserts the message appears and
  neither the Stop nor Start button renders during this state. Full
  frontend suite (65 tests), typecheck, and lint all pass.

---

### ISSUE-019 — Unhandled exceptions in any API route were never logged anywhere (RESOLVED)

- **Severity:** Medium (a real "silent failure" gap, not a crash or
  information-disclosure risk — Starlette's own default already returned
  a safe generic 500 with no leaked traceback; the exception was just
  invisible to this project's own logging/observability)
- **Description:** Found 2026-07-28 (Stabilization Mode backend review).
  `api/app.py` only registered exception handlers for `ApiError` (400) and
  `KeyError` (404) — any other unhandled exception fell through to
  Starlette's own default handling, confirmed directly: a genuine
  `ValueError` raised inside a route returned `500 Internal Server Error`
  (plain text, not even this API's usual JSON `{"detail": ...}` shape) with
  no traceback or message leaked to the client (the safe part already
  worked) — but nothing was ever logged through the `futures_bot` logger
  this project's own `bot.log`/`journal.py` setup controls. Confirmed by
  capturing the root logger during a real request: nothing but the
  HTTP-level access log line appeared. Any bug that isn't one of the two
  specifically-handled types would previously vanish without a trace
  anywhere the dashboard's own Logs page or `bot.log` could ever surface
  it.
- **Files involved:** `src/futures_bot/api/app.py`.
- **Possible cause:** `ApiError`/`KeyError` were added for the two
  well-understood, expected error shapes (validation failures, missing
  lookups); a true catch-all for the *unexpected* case was never added
  alongside them.
- **Current status:** **Resolved 2026-07-28.** New
  `@app.exception_handler(Exception)` logs the full exception (with
  traceback, via `log.error(..., exc_info=exc)`) through the same
  `futures_bot` logger every other log line in this process already uses,
  then returns the same JSON error shape every other handler in this file
  returns (`{"detail": "Internal server error"}`) — deliberately still no
  exception text/traceback in the response body, matching this API's own
  "no authentication in front of it" caution elsewhere. Verified it does
  not shadow the more specific existing handlers: `ApiError` still 400s,
  a genuine `HTTPException` (e.g. an unknown route) still 404s normally,
  only a truly unhandled exception type reaches the new handler (FastAPI/
  Starlette resolve the most specific registered handler by the raised
  exception's own type, not registration order). Three new tests in
  `tests/test_api_app.py::TestUnhandledExceptionHandler` cover: the crash
  is logged and returns the safe generic body without leaking the real
  message, `ApiError` is unaffected, and `HTTPException`-based 404s are
  unaffected. Every `test_api_*.py` file (16 files, ~160 tests) verified
  individually, all passing.

---

### ISSUE-020 — `api/jobs.py`'s `_get_executor()` lazy-init had no lock, unlike every other singleton accessor in this codebase (RESOLVED)

- **Severity:** Low (an extremely narrow startup-only race; worst case is
  a leaked, never-shut-down spare `ThreadPoolExecutor` sitting idle, not
  a correctness bug — no job would ever be lost or misrouted either way)
- **Description:** Found 2026-07-28 (Stabilization Mode backend review),
  by inspection while auditing thread-safety across every module-level
  singleton accessor in this codebase. `get_paper_trader()`,
  `get_research_server()`, `get_live_session_manager()`, and
  `market_data.scheduler.get_scheduler()` all guard their `if _x is None:
  _x = Constructor()` lazy-init with a dedicated lock; `api/jobs.py`'s
  `_get_executor()` was the one exception. Two nearly-simultaneous first
  calls to `submit()` (e.g. two job-submission requests arriving at
  almost the same instant right after process startup) could both
  observe `_executor is None` and each construct their own
  `ThreadPoolExecutor`, with the later assignment silently discarding the
  earlier one.
- **Files involved:** `src/futures_bot/api/jobs.py`.
- **Possible cause:** Written before the lock-guarded pattern was
  established elsewhere, or simply not revisited once it was.
- **Current status:** **Resolved 2026-07-28.** Added `_executor_lock`,
  matching the exact pattern already used successfully by every other
  singleton accessor in this codebase. `tests/test_api_jobs.py` +
  `tests/test_api_jobs_routes.py` (22 tests) pass.

---

### ISSUE-021 — `frontend/src/format.ts::dateTime()` parses SQLite (local-mode) timestamps as local time instead of UTC (OPEN, not fixed)

- **Severity:** Medium (a real display bug for every local-mode/SQLite
  timestamp shown through this shared helper -- Team-mode/Postgres
  timestamps are unaffected, they already carry an explicit `+00:00`)
- **Description:** Found 2026-07-28 (Stabilization Mode pass on newly
  added code) while writing `TeamPanel.tsx`'s own `timeAgo` helper and
  confirming its date parsing directly, node-by-node. `format.ts::dateTime()`
  calls `new Date(value)` directly with no normalization. SQLite's
  `datetime('now')` (every local-mode timestamp column in this codebase)
  produces `"YYYY-MM-DD HH:MM:SS"` -- no `T`, no timezone marker -- but the
  *value* is UTC. Every JS engine parses a timezone-less string as
  **local** time, not UTC (confirmed directly: `new Date("2026-07-28
  19:06:08")` produced a UTC-equivalent `time` shifted by this machine's
  own UTC offset, not the original instant). Every existing call site
  (`dateTime(status.last_bar_time)` in `Live.tsx`, and others) silently
  displays local-mode timestamps offset by the browser's UTC offset from
  the actual event time.
- **Files involved:** `frontend/src/format.ts` (`dateTime`), and by
  extension every page that calls it with a SQLite-sourced timestamp.
- **Possible cause:** Written and tested against Team-mode/Postgres
  timestamps (which already carry a timezone and parse correctly) without
  separately testing the SQLite local-mode case, which silently degrades
  instead of erroring -- there is no exception or `NaN` to notice, just a
  wrong-by-a-fixed-offset displayed time.
- **Current status:** **Not fixed.** `TeamPanel.tsx`'s own new `timeAgo`
  helper was written correctly from the start (explicit "T" + "Z"
  normalization before parsing, verified against both timestamp shapes --
  see its own inline comment), so this specific finding didn't need a
  matching fix to ship Phase 3. Fixing `format.ts::dateTime()` itself
  touches every page that renders a local-mode timestamp -- deliberately
  deferred rather than rushed through in the same pass that found it,
  since a shared, widely-used formatter deserves its own focused fix +
  verification across each call site rather than a drive-by edit.
  Recommended fix: apply the same normalization `timeAgo` already uses.

---

### ISSUE-022 — `claim_work_item` had a check-then-set race letting two concurrent claims on the same item both "succeed" (RESOLVED)

- **Severity:** High (silent data-integrity issue, not just an error path
  gap -- both callers get a 200, only one is actually true, and neither
  learns a conflict happened)
- **Description:** Found 2026-07-28 (Stabilization Mode sweep, concurrency
  review) by inspecting `collaboration/store.py`/`pg_store.py` for the
  same check-then-set shape already found and fixed three times in
  ISSUE-016. `claim_work_item` read the item, decided in Python whether
  the claim was allowed (item open, or already owned by the same caller),
  then ran an unconditional `UPDATE ... WHERE id = ?` with no re-check of
  ownership at write time. Two concurrent callers (two humans, or two AI
  sessions, racing to claim the same work item -- exactly the scenario
  Mission Control's Collaboration Workspace exists to support) could both
  pass the Python-level check before either committed; whichever `UPDATE`
  committed last silently won. Confirmed as a *real*, reliably-reproducible
  race, not theoretical: a new regression test
  (`test_concurrent_claims_never_both_win`, two real threads each on their
  own `CollaborationStore` connection, with an artificial delay between
  the read and the write to widen the window) failed against the pre-fix
  code (both threads reported success, one silently overwriting the
  other) and was confirmed to fail by temporarily reverting the fix and
  re-running it, then passed cleanly once restored.
- **Files involved:** `src/futures_bot/collaboration/store.py`
  (`CollaborationStore.claim_work_item`), `src/futures_bot/collaboration/pg_store.py`
  (`PgCollaborationStore.claim_work_item`).
- **Possible cause:** Written and tested against sequential double-claim
  behavior only (`test_claiming_already_claimed_by_another_user_raises`),
  which the original unconditional `UPDATE` already handled correctly --
  a second caller reading *after* the first one's commit sees the new
  `status`/`owner_user_id` and is rejected by the Python-level check
  before ever reaching the `UPDATE`. True concurrency (both callers
  reading *before* either commits) was never exercised.
- **Current status:** **Resolved 2026-07-28.** The `UPDATE`'s own `WHERE`
  clause now re-checks `owner_user_id IS NULL OR owner_user_id = ?`, and
  the affected-row count (`cursor.rowcount` / SQLAlchemy `result.rowcount`)
  determines whether the claim actually applied -- zero rows means someone
  else won the race, and the loser now gets the same "already claimed"
  `CollaborationError` a sequential double-claim already raised, instead
  of a false success. Full `test_collaboration_store.py` (35 tests) and
  `test_api_collaboration_routes.py` (43 tests) suites pass.

---

### ISSUE-023 — Every work-item action handler in `WorkItemTable.tsx` silently swallowed a failed API call (RESOLVED)

- **Severity:** Medium (a real UX gap, not a crash -- a rejected
  claim/release/complete/advance previously left the UI looking
  unchanged with zero feedback, no error boundary exists anywhere in this
  app to catch it)
- **Description:** Found 2026-07-28 (Stabilization Mode sweep) while
  fixing ISSUE-022, which made a previously-unreachable failure path
  (losing a claim race) newly reachable in normal use. `handleClaim`/
  `handleRelease`/`handleComplete`/`handleAdvance` each `await`ed their
  API call directly inside an `onClick` handler with no `try`/`catch`;
  a rejected promise became an unhandled rejection (logged to the
  console, nothing shown to the user) and `onRefetch()` never ran, so the
  displayed row didn't even refresh to show the item's real current
  state.
- **Files involved:** `frontend/src/components/mission-control/WorkItemTable.tsx`.
- **Possible cause:** Every action was written and tested against its
  success path only; no test exercised a rejected call, so the gap wasn't
  visible until ISSUE-022 made rejection a realistic outcome of ordinary
  concurrent use rather than only reachable via a stale/deleted item.
- **Current status:** **Resolved 2026-07-28.** Every handler now runs
  through a shared `runAction()` that catches `ApiRequestError`, displays
  its message in an inline `role="alert"` banner, and always calls
  `onRefetch()` in a `finally` block so the row reflects reality either
  way. Regression test added (mocks a rejected `updateWorkItemStatus`
  call, asserts the alert renders with the server's message).

---

### ISSUE-024 — `Register.tsx` retrying after a failed account creation re-created a duplicate, now-orphaned organization (RESOLVED)

- **Severity:** Medium (a real dead-end in the registration flow, not a
  crash -- reachable any time `createUser` fails after `createOrganization`
  already succeeded, e.g. a taken username)
- **Description:** Found 2026-07-28 (Stabilization Mode sweep, edge-case
  review of the newly-added registration flow). `handleAccountSubmit`
  always called `createOrganization` when `orgMode === 'create'`, with no
  memory of a previous attempt. If that call succeeded but the following
  `createUser` call then failed (most plausibly: the chosen username was
  already taken), the organization the user picked already existed in the
  database with no owner. Retrying the exact same form re-ran
  `createOrganization` with the same name, which now failed with
  "already exists" (the org name is unique) -- a dead end with no way
  forward from that screen, and a stray ownerless organization left
  behind from the first attempt.
- **Files involved:** `frontend/src/pages/Register.tsx`.
- **Possible cause:** The two-call sequence (create org, then create
  user) was written and tested against its success path and against
  `createUser` failing on the *first* attempt only; a *second* attempt
  after a partial first success wasn't exercised.
- **Current status:** **Resolved 2026-07-28.** The created organization's
  id is now cached in component state once that call succeeds, and a
  retry reuses it instead of calling `createOrganization` again;
  changing the organization name field (a genuine change of intent, not
  a retry) clears the cached id so a different name still creates a new
  org. Regression test added (`createUser` rejects once then succeeds,
  asserts `createOrganization` was called exactly once across both
  attempts) -- confirmed failing against the pre-fix code by temporarily
  reverting the fix and re-running it.

---

### ISSUE-025 — Team Mode's production build silently kept the local-dev loopback API URL, breaking every teammate's requests (RESOLVED)

- **Severity:** Critical (broke the entire dashboard for every user in
  Team Mode, not a degraded feature -- every API call from a freshly
  registered browser failed with "Could not reach the research API at
  http://127.0.0.1:8000")
- **Description:** Found 2026-07-28 (live, while a teammate was actually
  blocked registering). `frontend/src/api.ts::API_BASE` was
  `import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'`.
  `scripts/start-team.ps1` tried to make its production build use
  relative (same-origin) paths by setting `$env:VITE_API_BASE_URL = ""`
  immediately before `npm run build`, matching the same convention
  `Dockerfile.api`/`deploy/DEPLOYMENT.md` already use on Linux. An
  empty-string environment variable doesn't reliably survive the
  PowerShell -> npm.cmd -> cmd.exe -> vite child-process chain on
  Windows, though -- confirmed directly by inspecting the actual built
  `dist/assets/*.js` bundle, which had the literal string
  `http://127.0.0.1:8000` baked in as `API_BASE` despite the env var
  having been set. Every browser loading the built dashboard tried to
  reach its own machine's loopback address instead of the real Tailscale
  server.
- **Files involved:** `frontend/src/api.ts` (`API_BASE`),
  `scripts/start-team.ps1` (the frontend build step).
- **Possible cause:** The empty-string round trip was never actually
  verified against a real Windows build before this session -- it read
  as correct (matches the Linux/Docker convention) without confirming
  Windows' env-var-passing behavior across that specific process chain
  matched.
- **Current status:** **Resolved 2026-07-28.** `API_BASE`'s default is
  now derived from `import.meta.env.DEV`/`PROD` (real booleans Vite
  compiles in directly, not a value round-tripped through a child-process
  environment) via a new pure `resolveApiBase()` function -- dev mode
  (Local Mode's separate Vite dev server + API origin) still defaults to
  the loopback address, a production build (Team Mode, one origin serves
  both) now always defaults to a relative path regardless of whether any
  env var was set at build time. `start-team.ps1` simplified to a plain
  `npm run build`, no env var dance. Regression test added
  (`resolveApiBase`'s dev/prod/explicit-override/explicit-empty-string
  cases) -- confirmed failing against the pre-fix logic by temporarily
  reverting it and re-running; confirmed the rebuilt bundle no longer
  contains the loopback literal by inspecting it directly a second time.

---

### ISSUE-026 — `PgAccountStore` returned `None` for `notification_preferences`, crashing every response containing a user (RESOLVED)

- **Severity:** Critical (broke registration, the roster, and profile
  lookups for every Team Mode user with no notification preferences set
  -- i.e. every user, since nothing sets this field yet)
- **Description:** Found 2026-07-28 (live, immediately after fixing
  ISSUE-025 unblocked registration -- the very next registration attempt
  hit this). `notification_preferences` is a nullable JSONB column with
  no `server_default`, so a freshly created user reads back as SQL
  `NULL`/Python `None`. `UserOut`/`UserMeOut` (`api/schemas.py`) declare
  the field as a non-nullable `dict`
  (`Field(default_factory=dict)`) -- `default_factory` only applies when
  a field is *omitted* from the constructor call, not when it's
  explicitly passed as `None`, so `UserOut(**user)` raised a Pydantic
  `ValidationError` -> 500 for `GET /api/users`, `GET /api/users/{id}/me`,
  and even `POST /api/users`'s own response (the row itself had already
  committed successfully -- the crash happened serializing the response
  afterward, so registration *looked* like it failed even though the
  account existed). `AccountStore`'s SQLite side already normalizes this
  exact case (`_row_with_notification_preferences`); `PgAccountStore`
  never got the matching fix.
- **Files involved:** `src/futures_bot/accounts/pg_store.py` (`_row_dict`).
- **Possible cause:** The live-Postgres test coverage added alongside
  this field (`test_pg_account_store_live.py::test_profile_fields_round_trip`)
  explicitly set `notification_preferences` via `update_user` before
  reading it back, so it never exercised the NULL-default case a freshly
  created user actually hits.
- **Current status:** **Resolved 2026-07-28.** `_row_dict` now normalizes
  `None` -> `{}` for this field, matching the SQLite store. Regression
  test added directly against a live Postgres instance
  (`test_notification_preferences_defaults_to_empty_dict_not_none`) --
  confirmed failing against the pre-fix code by temporarily reverting it
  and re-running against that same live database.
- **Incident note:** Verifying this fix's regression test involved
  running `test_pg_account_store_live.py` against the same live database
  a teammate's real, just-created account lived in -- its cleanup fixture
  runs `TRUNCATE users, organizations` after every test, which wiped that
  account. The data loss was limited to those two tables (work items,
  trades, and market data were untouched); the affected user re-registered
  successfully once ISSUE-025 and this issue were both fixed. Lesson:
  never run a test file with a `TRUNCATE`-based cleanup fixture against a
  database someone is actively using for real work, even to verify a fix
  live -- use a disposable database, or at minimum confirm with whoever
  owns that data first.

---

### ISSUE-027 — A user who changed their own role via Team Members could permanently lock themselves out of managing anyone's role, including their own (RESOLVED)

- **Severity:** High (a real, reproducible self-lockout with no recovery
  path through the product itself -- happened live, to a sole
  organization owner)
- **Description:** Found 2026-07-28 (live -- a user demoted themselves
  from Owner to Member via the Team Members role dropdown). The role
  editor for *every* row (including your own) was gated only by
  `can('manage_members')`, evaluated against the *current* session role.
  After a self-demotion, the session refresh picked up the new, lower
  role, `manage_members` became `false`, and the dropdown disappeared
  for every row on the page -- including the user's own, with no other
  Owner/Admin in the org to fix it for them. Permissions are advisory-
  only, not enforced server-side (by design, documented in
  `accounts/permissions.py`), so the only actual recovery was a direct
  `PATCH /api/users/{id}` call bypassing the UI entirely.
- **Files involved:** `frontend/src/pages/TeamMembers.tsx`.
- **Possible cause:** The capability check only ever considered "can this
  session manage members at all," never "is this row the session's own"
  -- a self-service role change was never explicitly designed for or
  against, just not excluded.
- **Current status:** **Resolved 2026-07-28.** You can no longer edit
  your own role from this page under any circumstances, even with
  `manage_members` -- role changes always have to come from a teammate
  now (the same restriction most real systems apply, for exactly this
  reason). Regression test added -- confirmed failing against the pre-fix
  code by temporarily reverting it and re-running.

---

### ISSUE-028 — The Live Session start form defaulted to a hardcoded, already-expired futures contract (RESOLVED)

- **Severity:** High (a session silently reports "running" forever
  without a single bar of real data -- no error, no obvious symptom
  besides an empty dashboard, easy to miss)
- **Description:** Found 2026-07-28 (live -- "the live session tab never
  actually shows live data"). `frontend/src/pages/Live.tsx`'s start form
  defaulted `liveSymbol` to the hardcoded literal `'MESH6'` (March 2026
  expiry). Futures contract tickers roll every quarter; by the time
  anyone actually used this default, it was months expired. Starting a
  session with it succeeds (the API doesn't validate that a ticker has
  any live data before marking the session "running"), but
  `MassiveBarFeed.poll_new_bars()` then legitimately finds nothing --
  confirmed directly by querying the Massive API for both tickers: the
  expired one returned zero results, the actual current front-month
  contract (`MESU6`) had bars streaming in through the current moment.
  No exception is raised in this case (an empty result is a normal,
  successful response, not a feed error), so `last_feed_error` stayed
  `null` right alongside `last_bar_time` -- nothing about the session's
  own status distinguished this from "briefly quiet market."
- **Files involved:** `frontend/src/pages/Live.tsx`.
- **Possible cause:** A plausible-looking placeholder ticker was used as
  a form default during development and never revisited -- it was
  correct once, silently wrong forever after, with no mechanism (there is
  no active-contract-resolution endpoint exposed to the frontend yet,
  only used internally by the CLI/research server) to catch the drift.
- **Current status:** **Resolved 2026-07-28.** The field now starts
  empty with a placeholder/help text explaining it must be the *current*
  front-month contract, not a remembered one, plus the quarterly month-
  code reference (H/M/U/Z = Mar/Jun/Sep/Dec) needed to pick the right one
  by hand. Regression test updated to fill the field explicitly rather
  than relying on a default value. **Not done, a real follow-on
  improvement**: exposing the CLI/research-server's existing
  `active_contract()` resolution as an API endpoint so this field could
  auto-fill/validate instead of relying on the user to get a quarterly
  code right by hand -- flagged rather than built now (a new API route
  needs the explicit approval CLAUDE.md section 8 requires for that
  protected category, not something to add on a live-debugging pass).

---

### ISSUE-029 — `git_info._git()`'s whole-string `.strip()` truncated the first line of `git status --porcelain` output (RESOLVED)

- **Severity:** High (would have silently mis-mapped the first changed
  file on every SIL Phase 4 git-watcher cycle and every
  `tools/local_validate.py` run -- caught in development, never shipped)
- **Description:** Found 2026-07-29 while building
  `collaboration.git_info.changed_files()` (SIL Phase 4 Milestone B).
  `git status --porcelain`'s two-column status code can legitimately
  start with a literal space (e.g. `" M"` = "modified, not staged") --
  `_git()`'s shared `result.stdout.strip()` (used by every other
  function in this module for single/multi-line output where leading
  whitespace isn't meaningful) strips that leading space off the
  *first* line only, since `.strip()` operates on the whole string, not
  per-line. `" M src/foo.py\n M src/bar.py"` became
  `"M src/foo.py\n M src/bar.py"` after stripping, and `changed_files()`'s
  fixed-offset `line[3:]` parsing then read `"rc/foo.py"` instead of
  `"src/foo.py"` for the first file only -- confirmed directly against
  this repo's own real uncommitted changes.
- **Files involved:** `src/futures_bot/collaboration/git_info.py`.
- **Possible cause:** `_git()`'s `.strip()` is correct for every
  existing caller (branch names, commit metadata) where leading
  whitespace is never meaningful; `changed_files()` was the first
  caller whose output format uses a meaningful leading space.
- **Current status:** **Resolved 2026-07-29**, same commit it was
  introduced in (caught before shipping). `changed_files()` no longer
  goes through `_git()` -- it runs its own `subprocess.run` and only
  strips trailing newlines, never leading whitespace. Regression tests
  added: `tests/test_collaboration_git_info.py::TestChangedFiles`,
  including one asserting the first file's path is never truncated.

---

### ISSUE-030 — SIL Phase 4 git-watcher's own drafts suppressed their own files from the next cycle's "uncovered" calculation (RESOLVED)

- **Severity:** Medium (broke the git-watcher's self-healing/supersede
  behavior for a growing change set -- caught by its own regression
  test suite before shipping, never observed live)
- **Description:** Found 2026-07-29 while writing
  `tests/test_git_watcher.py`. `GitWatcherScheduler._reconcile()`
  computed "uncovered files" as `changed - covered`, where `covered`
  was built from every active work item's `estimated_files` --
  including the watcher's *own* previously-created draft. On the next
  cycle, a file already listed in that draft was wrongly excluded from
  `uncovered`, so a growing change set (e.g. `["a.py"]` -> `["a.py",
  "b.py"]`) produced a new draft covering only `["b.py"]` instead of
  both files once the old draft was discarded and replaced --
  `test_growing_change_set_supersedes_the_old_draft` failed against the
  pre-fix code with exactly this symptom.
- **Files involved:** `src/futures_bot/collaboration/git_watcher.py`.
- **Possible cause:** `covered` was built from every active item without
  distinguishing "a real work item genuinely resolves this file" from
  "this is the watcher's own pending, not-yet-reviewed draft" -- the
  two look identical in `fetch_active_work_items()`'s result.
- **Current status:** **Resolved 2026-07-29**, same commit it was
  introduced in. `_reconcile()` now skips `is_draft=True` items when
  building `covered`. Regression tests:
  `test_growing_change_set_supersedes_the_old_draft`,
  `test_shrinking_change_set_creates_a_new_draft_alongside_discarding_the_old`.

---

### ISSUE-031 — SIL Phase 4 maintenance scheduler's staleness check was off-by-one at the exact cutoff (RESOLVED)

- **Severity:** Low (a draft exactly `stale_draft_days` old would have
  survived one extra maintenance cycle instead of being discarded --
  caught by its own regression test before shipping)
- **Description:** Found 2026-07-29 while writing
  `tests/test_maintenance.py::test_boundary_is_inclusive_of_stale_draft_days`.
  `MaintenanceScheduler._discard_stale_drafts` used `if updated_at >=
  cutoff: continue` (skip, not stale), which excludes a draft exactly at
  the cutoff -- `config.py::AutomationSettings.stale_draft_days`'s own
  docstring says "untouched for this many days," which reads as
  inclusive at exactly N days.
- **Files involved:** `src/futures_bot/collaboration/maintenance.py`.
- **Possible cause:** An unexamined `>=` vs `>` choice when translating
  "stale after N days" into a comparison.
- **Current status:** **Resolved 2026-07-29**, same commit it was
  introduced in. Changed to `if updated_at > cutoff: continue`, making
  the cutoff itself count as stale.

---

### ISSUE-032 — SIL Phase 4's `collaboration/maintenance.py` imported `db.health` eagerly, breaking the "SQLite-only never needs the `db` extra" guarantee (RESOLVED)

- **Severity:** Medium (would have made `sqlalchemy` a hard runtime
  dependency for every single-developer/local-SQLite install, since
  `maintenance.py` is imported unconditionally by `api/app.py` -- caught
  before shipping, running the test suite under the system Python that
  lacks the `db` extra)
- **Description:** Found 2026-07-29. `db/engine.py`'s own module
  docstring and `api/routes/system.py`'s existing lazy-import pattern
  both establish that anything importing from `futures_bot.db` must do
  so lazily (inside a function, with a `try/except ModuleNotFoundError`
  fallback) specifically so a SQLite-only setup is never required to
  install `sqlalchemy`. `maintenance.py`'s first draft imported
  `check_database_health` at module level -- harmless when `sqlalchemy`
  happens to be installed, but a hard `ModuleNotFoundError` on
  `import futures_bot.api.app` otherwise, since `app.py` imports
  `collaboration.maintenance` unconditionally at startup (not gated
  behind team-deployment mode the way `pg_store.py` modules are, which
  are only ever imported from inside `get_*_store()`'s conditional
  branch).
- **Files involved:** `src/futures_bot/collaboration/maintenance.py`.
- **Possible cause:** `pg_store.py` files across this codebase import
  `db.engine` at module level safely, because *they* are only ever
  imported lazily by their own package's factory function -- easy to
  miss that `maintenance.py` doesn't have that same protection, since it
  itself is imported unconditionally.
- **Current status:** **Resolved 2026-07-29**, same commit it was
  introduced in. `check_database_health` is now imported inside a new
  `_check_database_health` method, with the same
  `try/except ModuleNotFoundError` fallback `system.py`'s health route
  already established.

---

### ISSUE-033 — `frontend/src/types.ts`'s new `WorkItem.is_draft` field broke three existing test fixtures (RESOLVED)

- **Severity:** Low (a `tsc -b` type error, not a runtime bug -- caught
  by `tools/local_validate.py`, the very tool being built in the same
  milestone sequence, on its first real use)
- **Description:** Found 2026-07-29. Adding `is_draft: boolean` to the
  `WorkItem` interface (SIL Phase 4 Milestone B) correctly requires
  every `WorkItem` object literal to supply it -- three existing test
  fixture builders
  (`CollaborationWorkspace.test.tsx`/`WorkItemTable.test.tsx`/
  `WorkRegistryPanel.test.tsx`'s `makeItem()` helpers) predated the
  field and didn't. Running `tools/local_validate.py` (Milestone C,
  built immediately after) against its own preceding commit's
  `frontend/src/types.ts` change caught this via `tsc -b` before it was
  ever an issue in CI.
- **Files involved:**
  `frontend/src/__tests__/CollaborationWorkspace.test.tsx`,
  `frontend/src/__tests__/WorkItemTable.test.tsx`,
  `frontend/src/__tests__/WorkRegistryPanel.test.tsx`.
- **Possible cause:** Adding a required field to a widely-used shared
  type without a repo-wide type-check pass immediately after -- exactly
  the gap `tools/local_validate.py` exists to close going forward.
- **Current status:** **Resolved 2026-07-29**, same commit it was
  introduced in. Added `is_draft: false` to each fixture's default
  object.
