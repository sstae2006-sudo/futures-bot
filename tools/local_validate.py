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

The mapping logic itself lives in `collaboration/validation_planning.py`
(extracted there in SIL Phase 6 Milestone 2) so the Integration Review's
"Validation Planning" feature can recommend the same thing for a work
item's `estimated_files`, without a second, independently-drifting copy.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from futures_bot.collaboration import git_info
from futures_bot.collaboration.validation_planning import plan_validation

_REPO_ROOT = Path(__file__).resolve().parent.parent


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

    plan = plan_validation(changed, _REPO_ROOT)
    matched, unmapped, frontend_changed = plan.matched_tests, plan.unmapped_files, plan.frontend_changed

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
