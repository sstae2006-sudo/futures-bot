# KNOWN_ISSUES.md

Every discovered bug is listed here. Don't delete an entry unless
verified fixed — instead mark it Resolved with a date and commit.

---

### ISSUE-001 — Contract/ticker column corrupted for 1980s–90s import

- **Severity:** Critical (data integrity)
- **Description:** Historical futures data imported from the 1980s–90s
  has the contract/ticker column populated with date values instead of
  actual contract symbols (e.g. a date string where `MESH6` or `6CH5`
  should be), producing a large number of bogus distinct "contracts"
  in the database.
- **Files involved:** `market_data.db` (affected table/column not yet
  identified). Suspected root cause in `tools/convert_data.py`,
  `tools/convert_turtle_data.py`, `tools/import_turtle_data.py`, or one
  of the flat-file import scripts (`tools/pull_massive_flatfiles.py`) —
  not yet confirmed.
- **Possible cause:** Column mapping bug in one of the above scripts —
  off-by-one, wrong header assumption, or a source file with a
  different schema than the others assume.
- **Current status:** Reported, not yet diagnosed. No fix attempted.
  Any repair work must back up `market_data.db` and verify the backup
  (open + integrity check) before any write — the file is ~1 GB+ and
  no fresh backup exists as of this writing (`market_data_backup.db`
  is stale, do not assume it's current).

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
