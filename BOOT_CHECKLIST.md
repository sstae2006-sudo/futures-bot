# BOOT_CHECKLIST.md

Run this at the start of every session, before making any change. If
what you find doesn't match PROJECT_STATE.md, stop and explain the
discrepancy — don't silently continue on a stale assumption.

## 1. Git state

```bash
git branch --show-current
git log --oneline -10
git status --short
```
Expect: `main`, `HEAD` matching the last commit hash in CHANGELOG.md,
and a clean or expected-dirty status (compare against what the last
session said it left uncommitted).

## 2. Project structure sanity

```bash
ls
```
Expect: matches File Ownership in `CLAUDE.md` section 8. If a
top-level dir/file appears that isn't documented there, don't assume
it's fine — figure out what it is before touching anything nearby.

## 3. Python environment

```bash
python --version
.venv/Scripts/python.exe --version   # project venv, if present
.venv/Scripts/python.exe -m pip freeze
```
Expect: Python >= 3.11 (see `pyproject.toml` `requires-python`).
Compare key package versions against PROJECT_STATE.md if something
seems off (e.g. an import error that looks version-related).

## 4. Backend + frontend startup (recommended: scripts\start.ps1)

```powershell
scripts\start.ps1
```
(or double-click `start.cmd` at the repo root). This is the official
startup method as of 2026-07-27 — see `CLAUDE.md` section 9. One
command verifies the repo, activates the venv, runs `pip install
-e .`/`npm install` unconditionally (so dependencies are always
current, not just "present"), checks `market_data.db`, frees ports
8000/5173 of any stale processes, starts backend + frontend, waits for
both to actually respond, opens the browser, and prints a green
summary (Backend URL, Frontend URL, database status, API status). Any
failure stops immediately with exactly what failed and how to fix it —
never a silent continue or a raw stack trace. Companions:
`scripts\status.ps1` (read-only check — backend/frontend running?,
reachable?, venv?, ports?, database?), `scripts\stop.ps1` (clean
shutdown), `scripts\restart.ps1` (stop, wait for ports to free, start
again).

Expect: the green summary block, ending "futures-bot is running" and
both URLs.

## 5. Manual startup (fallback — isolated backend-only/frontend-only debugging)

```bash
pip install -e .          # or ".[dev]" / ".[dev,ml]" depending on task
python -m futures_bot.api
```
```bash
cd frontend && npm install && npx vite --host 127.0.0.1
```
**Not** `npm run dev` — that script's `kill-vite.js` pre-step kills its
own node.exe process (`taskkill /F /IM node.exe` matches the caller by
image name), so `&& vite` never runs. Confirmed empirically 2026-07-27;
see `KNOWN_ISSUES.md`. `--host 127.0.0.1` is explicit because Vite
otherwise binds `localhost`, which resolves to the IPv6 loopback
(`[::1]`) on this machine, not `127.0.0.1` — also confirmed
empirically.

Expect: backend boots on `127.0.0.1:8000` with no traceback other than
the research-server auto-start's own caught/logged errors (e.g. a
stale `MASSIVE_API_KEY` causing a 401 — that's an external-API/config
issue, not a startup failure; see `docs/ARCHITECTURE.md` and
KNOWN_ISSUES.md before assuming it's new). Frontend: Vite dev server
starts, dashboard loads and can reach the API (check a page that calls
a real endpoint, not just the shell).

## 6. Test suite

```bash
pytest -q                              # or with ml extra installed for full coverage
```
Expect: matches PROJECT_STATE.md's "Backend Status" test count. A
failure at
`tests/test_api_research_server.py::TestNightlyAndFindings::test_run_nightly_now_updates_the_status_the_dashboard_reads`
in a full-suite run only is KNOWN_ISSUES.md ISSUE-002, not new — verify
by re-running that one test in isolation before treating it as a
regression.

## 7. Database validation

```bash
python -m futures_bot.cli --validate-db
```

Read-only, no API key needed. Expect exit code 0 ("VALIDATION
PASSED"). As of 2026-07-27 this exits **1** ("VALIDATION FAILED") on
the live `market_data.db` — two known, not-yet-fixed findings (a
schema drift in `bars`, and genuine OHLC violations in the raw
`US80Z` historical source data) — see `KNOWN_ISSUES.md` ISSUE-004 and
ISSUE-005, and `docs/DATABASE_VALIDATION.md`. Treat *exactly those two*
findings as known; a new or different FAIL is a real regression worth
stopping for.

## 8. Reconcile

If any of the above doesn't match PROJECT_STATE.md: stop, explain the
discrepancy to the user, and update PROJECT_STATE.md once the cause is
understood — don't proceed on a guess about which is stale (the file or
the running project).
