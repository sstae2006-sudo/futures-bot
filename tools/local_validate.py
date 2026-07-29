"""SIL Phase 4's "Automatic Local Validation" -- maps your currently
uncommitted changes to the tests most likely to catch a regression, runs
them, and prints a clear pass/fail summary, without waiting for the full
suite (currently ~1500 tests, several minutes) on every small change.

Usage:

    python tools/local_validate.py            # fast: only the mapped tests
    python tools/local_validate.py --full      # always run the whole backend suite
    python tools/local_validate.py --no-frontend  # skip lint/typecheck/vitest even if frontend/ changed

The file->test mapping is a heuristic, not a guarantee -- most test files
in this repo follow `test_<package>_<module>.py` or `test_<module>.py`
(confirmed against `collaboration/git_watcher.py` -> `test_git_watcher.py`,
`market_data/scheduler.py` -> `test_market_data_scheduler.py`), but not
every module has a same-named test file (e.g. `strategy/base.py`,
`api/collaboration_service.py` are exercised indirectly through other
test files, not a `test_base.py`/`test_collaboration_service.py` of
their own). Rather than silently under-testing when the heuristic comes
up empty, this falls back to running the FULL backend suite for that
invocation -- correctness over speed, same as everywhere else in this
codebase (CLAUDE.md section 2). Pass `--fast` to skip that fallback and
just run whatever direct matches were found (or nothing, printed
clearly) when you deliberately want the quick path.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from futures_bot.collaboration import git_info

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_PREFIX = "src/futures_bot/"
_TESTS_DIR = "tests"


def _candidate_test_globs(changed_file: str) -> list[str]:
    """`src/futures_bot/collaboration/git_watcher.py` ->
    `["test_git_watcher*.py", "test_collaboration_git_watcher*.py"]` --
    tried in this order (module-stem alone, then package-qualified) since
    real examples in this repo use both conventions inconsistently (see
    this module's own docstring)."""
    if not changed_file.startswith(_SRC_PREFIX) or not changed_file.endswith(".py"):
        return []
    rel = changed_file[len(_SRC_PREFIX):]
    parts = rel[:-3].split("/")  # strip ".py", split package path
    stem = parts[-1]
    if stem in ("__init__",):
        return []
    globs = [f"test_{stem}*.py"]
    if len(parts) > 1:
        qualified = "_".join(parts)
        globs.append(f"test_{qualified}*.py")
    return globs


def _matching_tests(changed_file: str) -> list[str]:
    tests_dir = _REPO_ROOT / _TESTS_DIR
    matches: set[str] = set()
    for pattern in _candidate_test_globs(changed_file):
        for path in tests_dir.glob(pattern):
            matches.add(f"{_TESTS_DIR}/{path.name}")
    return sorted(matches)


def _plan(changed_files: list[str]) -> tuple[list[str], list[str], bool]:
    """Returns (matched_test_files, unmapped_source_files, frontend_changed)."""
    matched: set[str] = set()
    unmapped: list[str] = []
    frontend_changed = False

    for f in changed_files:
        if f.startswith(f"{_TESTS_DIR}/") and f.endswith(".py"):
            matched.add(f)  # a test file itself changed -- always run it
            continue
        if f.startswith("frontend/src/"):
            frontend_changed = True
            continue
        if not f.startswith(_SRC_PREFIX):
            continue  # tools/, docs/, config files, etc. -- nothing to run
        tests = _matching_tests(f)
        if tests:
            matched.update(tests)
        else:
            unmapped.append(f)

    return sorted(matched), unmapped, frontend_changed


def _run(cmd: list[str], cwd: Path) -> bool:
    print(f"\n$ {' '.join(cmd)}  (in {cwd.relative_to(_REPO_ROOT) or '.'})")
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--full", action="store_true", help="Always run the whole backend suite, ignore the mapping.")
    parser.add_argument("--fast", action="store_true", help="Never fall back to the full suite, even if mapping is incomplete.")
    parser.add_argument("--no-frontend", action="store_true", help="Skip frontend lint/typecheck/vitest even if frontend/src changed.")
    args = parser.parse_args(argv)

    changed = git_info.changed_files(_REPO_ROOT)
    if not changed:
        print("No uncommitted changes -- nothing to validate.")
        return 0

    print(f"{len(changed)} changed file(s):")
    for f in changed:
        print(f"  {f}")

    matched, unmapped, frontend_changed = _plan(changed)

    run_full_backend = args.full
    if unmapped and not args.fast and not args.full:
        print(
            f"\nNo direct test file found for {len(unmapped)} changed file(s) "
            f"({', '.join(unmapped)}) -- falling back to the full backend suite "
            "for safety (pass --fast to skip this and only run the direct matches)."
        )
        run_full_backend = True
    elif unmapped:
        print(f"\nNo direct test file found for: {', '.join(unmapped)} (not run -- --fast/--full was given).")

    backend_ok = True
    if run_full_backend:
        backend_ok = _run([sys.executable, "-m", "pytest", "-q"], cwd=_REPO_ROOT)
    elif matched:
        print(f"\nRunning {len(matched)} mapped test file(s): {', '.join(matched)}")
        backend_ok = _run([sys.executable, "-m", "pytest", "-q", *matched], cwd=_REPO_ROOT)
    else:
        print("\nNo backend test files to run.")

    frontend_ok = True
    if frontend_changed and not args.no_frontend:
        frontend_dir = _REPO_ROOT / "frontend"
        npx = "npx.cmd" if sys.platform == "win32" else "npx"
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        frontend_ok = (
            _run([npx, "tsc", "-b"], cwd=frontend_dir)
            and _run([npm, "run", "lint"], cwd=frontend_dir)
            and _run([npm, "test"], cwd=frontend_dir)
        )
    elif frontend_changed:
        print("\nfrontend/src changed but --no-frontend was given -- skipped lint/typecheck/vitest.")

    print("\n" + "=" * 60)
    print(f"Backend:  {'PASS' if backend_ok else 'FAIL'}")
    if frontend_changed and not args.no_frontend:
        print(f"Frontend: {'PASS' if frontend_ok else 'FAIL'}")
    print("=" * 60)

    return 0 if (backend_ok and frontend_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
