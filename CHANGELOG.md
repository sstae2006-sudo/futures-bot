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
