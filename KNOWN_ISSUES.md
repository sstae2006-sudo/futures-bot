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
