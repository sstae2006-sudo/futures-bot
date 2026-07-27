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

## 4. Backend startup

```bash
pip install -e .          # or ".[dev]" / ".[dev,ml]" depending on task
python -m futures_bot.api
```
Expect: boots on `127.0.0.1:8000` with no traceback other than the
research-server auto-start's own caught/logged errors (e.g. a stale
`MASSIVE_API_KEY` causing a 401 — that's an external-API/config issue,
not a startup failure; see `docs/ARCHITECTURE.md` and
KNOWN_ISSUES.md before assuming it's new).

## 5. Frontend startup

```bash
cd frontend && npm install && npm run dev
```
Expect: Vite dev server starts, dashboard loads and can reach the API
(check a page that calls a real endpoint, not just the shell).

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

## 7. Reconcile

If any of the above doesn't match PROJECT_STATE.md: stop, explain the
discrepancy to the user, and update PROJECT_STATE.md once the cause is
understood — don't proceed on a guess about which is stale (the file or
the running project).
