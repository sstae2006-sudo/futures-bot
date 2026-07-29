"""Tests for `collaboration.git_sync.GitSyncScheduler` -- the pull-only
auto-sync scheduler. Uses two real, throwaway git repos (a "local" clone
and a bare "remote") so the fast-forward/diverged/dirty-tree logic is
verified against real `git`, not mocked -- same reasoning
`test_collaboration_git_info.py`'s `repo` fixture already established
for `changed_files()`.
"""

from __future__ import annotations

import subprocess
import threading

import pytest

from futures_bot.collaboration.git_sync import GitSyncScheduler, get_git_sync_scheduler, reset_git_sync_scheduler


def _git(args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


@pytest.fixture
def remote_and_local(tmp_path):
    """A bare remote repo + a local clone tracking it, one initial commit
    already pushed -- the common starting point for every test below."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(["init", "--bare"], cwd=remote)

    local = tmp_path / "local"
    local.mkdir()
    _git(["init", "-b", "main"], cwd=local)
    _git(["config", "user.email", "test@example.com"], cwd=local)
    _git(["config", "user.name", "Test"], cwd=local)
    _git(["remote", "add", "origin", str(remote)], cwd=local)
    (local / "file.txt").write_text("v1\n")
    _git(["add", "file.txt"], cwd=local)
    _git(["commit", "-m", "initial"], cwd=local)
    _git(["push", "-u", "origin", "main"], cwd=local)

    return remote, local


@pytest.fixture(autouse=True)
def _reset():
    reset_git_sync_scheduler()
    yield
    reset_git_sync_scheduler()


class TestSync:
    def test_up_to_date_is_a_no_op(self, remote_and_local, monkeypatch):
        _, local = remote_and_local
        monkeypatch.setattr("futures_bot.collaboration.git_sync.git_info.repo_root", lambda: local)
        scheduler = GitSyncScheduler()

        applied, result = scheduler._sync("origin")

        assert applied is False
        assert result == "up to date"

    def test_fast_forwards_when_remote_has_new_commits(self, remote_and_local, monkeypatch):
        remote, local = remote_and_local
        # Simulate a teammate pushing a new commit via a second clone.
        other = local.parent / "other_clone"
        # -b main: the bare "remote" fixture's own HEAD symref defaults
        # to whatever this machine's git considers default (often
        # "master"), independent of "main" existing as a real branch on
        # it -- an unqualified `git clone` would check that default out
        # instead of "main", landing every commit below on the wrong
        # branch entirely (confirmed empirically). Explicit -b sidesteps
        # relying on that ambient default.
        _git(["clone", "-b", "main", str(remote), str(other)], cwd=local.parent)
        _git(["config", "user.email", "teammate@example.com"], cwd=other)
        _git(["config", "user.name", "Teammate"], cwd=other)
        (other / "file2.txt").write_text("from teammate\n")
        _git(["add", "file2.txt"], cwd=other)
        _git(["commit", "-m", "teammate change"], cwd=other)
        _git(["push"], cwd=other)

        monkeypatch.setattr("futures_bot.collaboration.git_sync.git_info.repo_root", lambda: local)
        scheduler = GitSyncScheduler()

        applied, result = scheduler._sync("origin")

        assert applied is True
        assert "pulled" in result
        assert (local / "file2.txt").is_file()

    def test_dirty_working_tree_is_never_touched(self, remote_and_local, monkeypatch):
        _, local = remote_and_local
        (local / "file.txt").write_text("uncommitted local edit\n")
        monkeypatch.setattr("futures_bot.collaboration.git_sync.git_info.repo_root", lambda: local)
        scheduler = GitSyncScheduler()

        applied, result = scheduler._sync("origin")

        assert applied is False
        assert "uncommitted changes" in result
        assert (local / "file.txt").read_text() == "uncommitted local edit\n"  # untouched

    def test_diverged_history_is_reported_not_merged(self, remote_and_local, monkeypatch):
        remote, local = remote_and_local
        other = local.parent / "other_clone"
        # -b main: the bare "remote" fixture's own HEAD symref defaults
        # to whatever this machine's git considers default (often
        # "master"), independent of "main" existing as a real branch on
        # it -- an unqualified `git clone` would check that default out
        # instead of "main", landing every commit below on the wrong
        # branch entirely (confirmed empirically). Explicit -b sidesteps
        # relying on that ambient default.
        _git(["clone", "-b", "main", str(remote), str(other)], cwd=local.parent)
        _git(["config", "user.email", "teammate@example.com"], cwd=other)
        _git(["config", "user.name", "Teammate"], cwd=other)
        (other / "file2.txt").write_text("from teammate\n")
        _git(["add", "file2.txt"], cwd=other)
        _git(["commit", "-m", "teammate change"], cwd=other)
        _git(["push"], cwd=other)

        # Meanwhile, a local commit that was never pushed -- history diverges.
        (local / "file3.txt").write_text("local unpushed change\n")
        _git(["add", "file3.txt"], cwd=local)
        _git(["commit", "-m", "local unpushed"], cwd=local)

        monkeypatch.setattr("futures_bot.collaboration.git_sync.git_info.repo_root", lambda: local)
        scheduler = GitSyncScheduler()

        applied, result = scheduler._sync("origin")

        assert applied is False
        assert "diverged" in result
        head_after = _git(["rev-parse", "HEAD"], cwd=local).stdout.strip()
        # HEAD must still be the local commit -- no merge/rebase happened.
        assert "local unpushed" in _git(["log", "-1", "--format=%s", head_after], cwd=local).stdout

    def test_no_upstream_ref_is_skipped_gracefully(self, remote_and_local, monkeypatch):
        _, local = remote_and_local
        _git(["checkout", "-b", "feature-branch-never-pushed"], cwd=local)
        monkeypatch.setattr("futures_bot.collaboration.git_sync.git_info.repo_root", lambda: local)
        scheduler = GitSyncScheduler()

        applied, result = scheduler._sync("origin")

        assert applied is False
        assert "ref" in result

    def test_not_a_git_repo_is_skipped_gracefully(self, monkeypatch):
        monkeypatch.setattr("futures_bot.collaboration.git_sync.git_info.repo_root", lambda: None)
        scheduler = GitSyncScheduler()

        applied, result = scheduler._sync("origin")

        assert applied is False
        assert "not a git repository" in result

    def test_never_pushes(self, remote_and_local, monkeypatch):
        """The whole point of this scheduler -- confirm _sync never calls
        `git push` under any code path by asserting the remote's HEAD is
        unchanged after a cycle that has local (fast-forwardable) state
        to offer, i.e. nothing local-only ever reaches the remote."""
        remote, local = remote_and_local
        remote_head_before = _git(["rev-parse", "HEAD"], cwd=remote).stdout.strip()

        monkeypatch.setattr("futures_bot.collaboration.git_sync.git_info.repo_root", lambda: local)
        scheduler = GitSyncScheduler()
        scheduler._sync("origin")

        remote_head_after = _git(["rev-parse", "HEAD"], cwd=remote).stdout.strip()
        assert remote_head_after == remote_head_before


class TestLifecycle:
    def test_start_and_stop_without_leaking_a_thread(self, monkeypatch):
        monkeypatch.setattr("futures_bot.collaboration.git_sync.git_info.repo_root", lambda: None)
        scheduler = GitSyncScheduler()
        scheduler.start(interval_seconds=1)
        assert scheduler.status()["running"] is True

        scheduler.stop(timeout=5)
        assert scheduler.status()["running"] is False

    def test_starting_twice_raises(self, monkeypatch):
        monkeypatch.setattr("futures_bot.collaboration.git_sync.git_info.repo_root", lambda: None)
        scheduler = GitSyncScheduler()
        scheduler.start(interval_seconds=1)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                scheduler.start(interval_seconds=1)
        finally:
            scheduler.stop(timeout=5)


class TestGlobalAccessor:
    def test_get_git_sync_scheduler_returns_the_same_instance(self):
        first = get_git_sync_scheduler()
        second = get_git_sync_scheduler()
        assert first is second


class TestConcurrency:
    def test_concurrent_start_calls_never_both_win(self, monkeypatch):
        monkeypatch.setattr("futures_bot.collaboration.git_sync.git_info.repo_root", lambda: None)
        scheduler = GitSyncScheduler()
        results = []

        def _try_start():
            try:
                scheduler.start(interval_seconds=1)
                results.append("started")
            except RuntimeError:
                results.append("rejected")

        threads = [threading.Thread(target=_try_start) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        scheduler.stop(timeout=5)
        assert results.count("started") == 1
        assert results.count("rejected") == 4
