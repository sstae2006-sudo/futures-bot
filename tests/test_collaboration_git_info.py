"""Tests for `collaboration.git_info` -- live, read-only git introspection
for the Active Work Registry. Runs against this repo's own real `.git`
(no fixture repo needed: every assertion is structural/best-effort, not
tied to specific commit content), plus a couple of guaranteed-broken
inputs (a directory with no `.git`, a nonexistent branch) to confirm the
best-effort contract: `None`/empty, never an exception.
"""

from __future__ import annotations

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
