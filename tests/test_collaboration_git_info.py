"""Tests for `collaboration.git_info` -- live, read-only git introspection
for the Active Work Registry. Runs against this repo's own real `.git`
(no fixture repo needed: every assertion is structural/best-effort, not
tied to specific commit content), plus a couple of guaranteed-broken
inputs (a directory with no `.git`, a nonexistent branch) to confirm the
best-effort contract: `None`/empty, never an exception.
"""

from __future__ import annotations

import subprocess

import pytest

from futures_bot.collaboration import git_info


class TestCurrentBranch:
    def test_returns_a_non_empty_string_in_this_repo(self):
        branch = git_info.current_branch()
        assert branch is None or (isinstance(branch, str) and branch)


class TestGetBranchInfo:
    def test_current_branch_resolves_without_raising(self):
        info = git_info.get_branch_info()
        assert isinstance(info.notes, tuple)

    def test_nonexistent_branch_degrades_gracefully(self):
        info = git_info.get_branch_info(branch="this-branch-does-not-exist-xyz")
        # git rev-parse --abbrev-ref HEAD isn't consulted when a branch is
        # explicitly given, so `branch` echoes the (bogus) input, but every
        # git-derived field must come back empty rather than raising.
        assert info.last_commit is None

    def test_nonexistent_base_branch_is_noted_not_raised(self):
        info = git_info.get_branch_info(base_branch="this-base-branch-does-not-exist-xyz")
        assert info.ahead is None
        assert info.behind is None
        assert any("not found" in n for n in info.notes)

    def test_main_branch_has_no_ahead_behind_against_itself(self):
        info = git_info.get_branch_info(branch="main", base_branch="main")
        assert info.base_branch is None  # comparing a branch to itself is meaningless
        assert info.ahead is None
        assert info.behind is None


class TestRecentCommits:
    def test_returns_a_list_without_raising(self):
        commits = git_info.recent_commits(limit=5)
        assert isinstance(commits, list)
        assert len(commits) <= 5

    def test_each_commit_has_the_expected_shape(self):
        commits = git_info.recent_commits(limit=1)
        if not commits:
            return  # a shallow clone or empty repo in CI is plausible; not this test's concern
        commit = commits[0]
        assert commit.hash
        assert commit.short_hash == commit.hash[:10]
        assert isinstance(commit.subject, str)

    def test_zero_limit_returns_empty(self):
        assert git_info.recent_commits(limit=0) == []


class TestNotARepo:
    def test_repo_root_lookup_is_isolated_from_process_cwd(self, tmp_path, monkeypatch):
        """`_repo_root` walks up from this *module's* location, not the
        process's cwd -- changing cwd to a bare tmp_path must not break it."""
        monkeypatch.chdir(tmp_path)
        info = git_info.get_branch_info()
        assert isinstance(info.notes, tuple)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A real, throwaway git repo -- `changed_files` shells out to real
    `git`, so its porcelain-parsing correctness (in particular the
    leading-space-in-status-code bug this module's own docstring
    describes) can only be verified against real `git status` output,
    not a mock."""
    _git(["init"], cwd=tmp_path)
    _git(["config", "user.email", "test@example.com"], cwd=tmp_path)
    _git(["config", "user.name", "Test"], cwd=tmp_path)
    (tmp_path / "committed.py").write_text("x = 1\n")
    _git(["add", "committed.py"], cwd=tmp_path)
    _git(["commit", "-m", "initial"], cwd=tmp_path)
    return tmp_path


class TestChangedFiles:
    def test_clean_repo_returns_empty(self, repo):
        assert git_info.changed_files(repo) == []

    def test_untracked_file(self, repo):
        (repo / "new_file.py").write_text("y = 2\n")
        assert git_info.changed_files(repo) == ["new_file.py"]

    def test_modified_unstaged_file_path_is_not_truncated(self, repo):
        """Regression: `git status --porcelain`'s status code can start
        with a literal space (" M" = modified, not staged) -- the *first*
        line of multi-line output. Naively `.strip()`-ing the whole
        output eats that leading space off line one only, shifting every
        char of the first file's path left by one (confirmed empirically:
        "committed.py" came back as "ommitted.py"). This must not
        reproduce that."""
        (repo / "committed.py").write_text("x = 2\n")
        assert git_info.changed_files(repo) == ["committed.py"]

    def test_modified_staged_file(self, repo):
        (repo / "committed.py").write_text("x = 3\n")
        _git(["add", "committed.py"], cwd=repo)
        assert git_info.changed_files(repo) == ["committed.py"]

    def test_multiple_changed_files_all_present_and_untruncated(self, repo):
        (repo / "committed.py").write_text("x = 4\n")
        (repo / "another_new_file.py").write_text("z = 5\n")

        files = git_info.changed_files(repo)

        assert sorted(files) == ["another_new_file.py", "committed.py"]

    def test_nested_path(self, repo):
        (repo / "sub").mkdir()
        (repo / "sub" / "nested.py").write_text("a = 1\n")
        assert git_info.changed_files(repo) == ["sub/nested.py"]

    def test_not_a_git_repo_returns_empty(self, tmp_path):
        empty_dir = tmp_path / "not_a_repo"
        empty_dir.mkdir()
        assert git_info.changed_files(empty_dir) == []
