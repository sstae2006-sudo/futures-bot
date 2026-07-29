"""Validation planning -- maps a set of changed files to the tests most
likely to catch a regression in them.

This is the exact heuristic `tools/local_validate.py` (SIL Phase 4,
"Automatic Local Validation") already implemented for a developer's own
uncommitted changes; it is extracted here, unchanged, so SIL Phase 6
Milestone 2's Integration Review can recommend a validation strategy for
a *work item's* `estimated_files` the same way, without a second,
independently-drifting copy of the mapping logic. `tools/local_validate.py`
now imports `plan_validation` from here instead of defining its own
`_plan`/`_matching_tests`/`_candidate_test_globs`.

See that module's own docstring for why the mapping is a heuristic, not a
guarantee, and why an unmapped file means "recommend the full suite,"
never "silently skip validation" -- correctness over speed (CLAUDE.md
section 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_SRC_PREFIX = "src/futures_bot/"
_TESTS_DIR = "tests"


def candidate_test_globs(changed_file: str) -> list[str]:
    """`src/futures_bot/collaboration/git_watcher.py` ->
    `["test_git_watcher*.py", "test_collaboration_git_watcher*.py"]` --
    tried in this order (module-stem alone, then package-qualified) since
    real examples in this repo use both conventions inconsistently."""
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


def matching_tests(changed_file: str, repo_root: Path) -> list[str]:
    tests_dir = repo_root / _TESTS_DIR
    matches: set[str] = set()
    for pattern in candidate_test_globs(changed_file):
        if not tests_dir.is_dir():
            break
        for path in tests_dir.glob(pattern):
            matches.add(f"{_TESTS_DIR}/{path.name}")
    return sorted(matches)


@dataclass(frozen=True)
class ValidationPlan:
    matched_tests: list[str] = field(default_factory=list)
    unmapped_files: list[str] = field(default_factory=list)
    frontend_changed: bool = False
    #: Mirrors `local_validate.py`'s default behavior: any unmapped
    #: backend source file means "don't silently under-test," recommend
    #: the full backend suite for this change instead of only the direct
    #: matches. Advisory here (Milestone 2 only *recommends*, `local_validate.py`
    #: is the one that actually runs anything) -- see that module's
    #: `--fast` flag for the opt-out this mirrors.
    recommend_full_suite: bool = False


def plan_validation(changed_files: list[str], repo_root: Path) -> ValidationPlan:
    """Same classification `local_validate.py::_plan` already performed,
    now shared. `changed_files` need not come from a real `git diff` --
    Milestone 2 calls this with a work item's self-reported
    `estimated_files`, same proxy limitation `_readiness_note` already
    documents for merge-readiness scoring."""
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
        tests = matching_tests(f, repo_root)
        if tests:
            matched.update(tests)
        else:
            unmapped.append(f)

    return ValidationPlan(
        matched_tests=sorted(matched), unmapped_files=unmapped, frontend_changed=frontend_changed,
        recommend_full_suite=bool(unmapped),
    )
