# CHANGELOG.md

Every session appends an entry here. Don't edit past entries except to
mark something resolved with a date/commit — this is a history, not a
scratchpad.

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
