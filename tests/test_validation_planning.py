"""Unit tests for `collaboration.validation_planning` -- the file->test
mapping heuristic shared between `tools/local_validate.py` (SIL Phase 4)
and SIL Phase 6 Milestone 2's Integration Review "Validation Planning".
"""

from __future__ import annotations

from pathlib import Path

from futures_bot.collaboration.validation_planning import (
    candidate_test_globs, matching_tests, plan_validation,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestCandidateTestGlobs:
    def test_module_stem_and_package_qualified_globs(self):
        globs = candidate_test_globs("src/futures_bot/collaboration/git_watcher.py")

        assert globs == ["test_git_watcher*.py", "test_collaboration_git_watcher*.py"]

    def test_top_level_module_has_only_one_glob(self):
        assert candidate_test_globs("src/futures_bot/engine.py") == ["test_engine*.py"]

    def test_non_python_file_returns_empty(self):
        assert candidate_test_globs("frontend/src/api.ts") == []

    def test_init_file_returns_empty(self):
        assert candidate_test_globs("src/futures_bot/collaboration/__init__.py") == []

    def test_outside_src_prefix_returns_empty(self):
        assert candidate_test_globs("tools/local_validate.py") == []


class TestMatchingTests:
    def test_finds_real_test_file_in_this_repo(self):
        # collaboration/git_watcher.py -> tests/test_git_watcher.py exists in this repo.
        matches = matching_tests("src/futures_bot/collaboration/git_watcher.py", _REPO_ROOT)

        assert "tests/test_git_watcher.py" in matches

    def test_nonexistent_tests_dir_returns_empty(self, tmp_path):
        assert matching_tests("src/futures_bot/collaboration/git_watcher.py", tmp_path) == []


class TestPlanValidation:
    def test_test_file_itself_is_always_included(self):
        plan = plan_validation(["tests/test_foo.py"], _REPO_ROOT)

        assert plan.matched_tests == ["tests/test_foo.py"]
        assert plan.unmapped_files == []
        assert plan.recommend_full_suite is False

    def test_frontend_file_sets_frontend_changed(self):
        plan = plan_validation(["frontend/src/api.ts"], _REPO_ROOT)

        assert plan.frontend_changed is True
        assert plan.matched_tests == []
        assert plan.unmapped_files == []

    def test_non_source_file_is_ignored(self):
        plan = plan_validation(["docs/ARCHITECTURE.md", "config.yaml"], _REPO_ROOT)

        assert plan.matched_tests == []
        assert plan.unmapped_files == []
        assert plan.recommend_full_suite is False

    def test_mapped_backend_file_recommends_only_direct_matches(self):
        plan = plan_validation(["src/futures_bot/collaboration/git_watcher.py"], _REPO_ROOT)

        assert "tests/test_git_watcher.py" in plan.matched_tests
        assert plan.unmapped_files == []
        assert plan.recommend_full_suite is False

    def test_unmapped_backend_file_recommends_full_suite(self):
        """"Never silently under-test" -- an unmapped file must flip
        `recommend_full_suite`, not just get silently dropped."""
        plan = plan_validation(["src/futures_bot/does_not_exist_anywhere.py"], _REPO_ROOT)

        assert plan.recommend_full_suite is True
        assert "src/futures_bot/does_not_exist_anywhere.py" in plan.unmapped_files
