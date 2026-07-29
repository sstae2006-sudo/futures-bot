"""Automatic *pull*-only git sync -- keeps this checkout's current branch
up to date with its remote (default `origin`) without anyone running
`git pull` by hand. Explicitly does **not** push: publishing local
commits to a shared remote is a "pushing code" action this project's own
operating rules treat as something to confirm with a human each time,
not something to run unattended in a background loop. If two-way sync is
ever wanted, that is a separate, explicitly-opted-into capability, not
something this scheduler grows into silently.

Same daemon-thread shape `market_data/scheduler.py::MarketDataScheduler`/
`git_watcher.py::GitWatcherScheduler` already establish. Deliberately
conservative on the write side -- the only mutation this ever performs
on the working tree is a fast-forward merge, and only when all of the
following hold:

- the working tree is clean (`git status --porcelain` empty) -- a dirty
  tree is never touched, full stop; no auto-stash, no auto-anything.
- the local branch has a real upstream to compare against.
- the merge is a genuine fast-forward (local HEAD is an ancestor of the
  remote-tracking ref) -- a diverged history (local commits not yet
  pushed, sitting alongside new remote commits) is reported, never
  merged/rebased/forced.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import git_info
from ..journal import LOGGER_NAME

log = logging.getLogger(LOGGER_NAME)

_GIT_TIMEOUT_SECONDS = 15  # fetch is a network call -- more generous than git_info's local-only 5s


@dataclass
class _Status:
    running: bool = False
    last_cycle_at: Optional[str] = None
    last_result: Optional[str] = None
    last_error: Optional[str] = None
    cycles_completed: int = 0
    pulls_applied_count: int = 0


def _git(args: list[str], cwd: Optional[Path]) -> tuple[bool, str]:
    """Returns `(succeeded, stdout_or_stderr)` -- unlike `git_info._git`,
    callers here need to distinguish "command failed" from "command
    succeeded with empty output," and want the error text on failure
    (surfaced in `last_result`/`last_error`) rather than a bare `None`."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, result.stdout.strip()


def _is_working_tree_clean(root: Path) -> bool:
    return not git_info.changed_files(root)


class GitSyncScheduler:
    """One instance keeps this checkout's current branch fast-forwarded
    from its remote. Embedded in the API process, same lifecycle as
    `GitWatcherScheduler`/`MaintenanceScheduler` -- see
    `api/app.py::_maybe_start_automation`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._status = _Status()

    def start(self, interval_seconds: int = 120, remote: str = "origin") -> None:
        with self._lock:
            if self._status.running:
                raise RuntimeError("The git-sync scheduler is already running.")
            self._status = _Status(running=True)
            self._stop_event = threading.Event()
            stop_event = self._stop_event

        thread = threading.Thread(
            target=self._run, args=(interval_seconds, remote, stop_event),
            daemon=True, name="futures-bot-git-sync",
        )
        with self._lock:
            self._thread = thread
        thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self._status.running = False

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._status.running,
                "last_cycle_at": self._status.last_cycle_at,
                "last_result": self._status.last_result,
                "last_error": self._status.last_error,
                "cycles_completed": self._status.cycles_completed,
                "pulls_applied_count": self._status.pulls_applied_count,
            }

    def _run(self, interval_seconds: int, remote: str, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self._run_cycle(datetime.now(timezone.utc), remote)
            stop_event.wait(interval_seconds)

    def _run_cycle(self, now: datetime, remote: str) -> None:
        try:
            applied, result = self._sync(remote)
            with self._lock:
                self._status.last_cycle_at = now.isoformat()
                self._status.last_result = result
                self._status.last_error = None
                self._status.cycles_completed += 1
                if applied:
                    self._status.pulls_applied_count += 1
        except Exception as exc:  # noqa: BLE001 -- one bad cycle must not kill the thread
            log.error("git-sync cycle failed: %s", exc, exc_info=True)
            with self._lock:
                self._status.last_cycle_at = now.isoformat()
                self._status.last_error = str(exc)

    def _sync(self, remote: str) -> tuple[bool, str]:
        """Returns `(pull_applied, human_readable_result)`. Every early
        return here is a deliberate "don't touch anything" case -- see
        this module's own docstring for the exact preconditions."""
        root = git_info.repo_root()
        if root is None:
            return False, "not a git repository -- nothing to sync"

        if not _is_working_tree_clean(root):
            return False, "skipped: uncommitted changes present (never touched automatically)"

        branch = git_info.current_branch(root)
        if branch is None:
            return False, "skipped: detached HEAD (no branch to sync)"

        # Fetches everything the remote has, not just `branch` -- a named
        # single-ref fetch (`git fetch origin <branch>`) errors loudly if
        # that branch was never pushed at all, which is a legitimate,
        # skip-gracefully case (a brand-new local feature branch), not a
        # real fetch failure.
        ok, fetch_output = _git(["fetch", "--quiet", remote], cwd=root)
        if not ok:
            return False, f"fetch failed: {fetch_output}" if fetch_output else f"fetch failed (is {remote!r} reachable?)"

        remote_ref = f"{remote}/{branch}"
        ok, _ = _git(["rev-parse", "--verify", remote_ref], cwd=root)
        if not ok:
            return False, f"skipped: no {remote_ref!r} ref (branch not pushed / no upstream yet)"

        ok, local_hash = _git(["rev-parse", "HEAD"], cwd=root)
        ok2, remote_hash = _git(["rev-parse", remote_ref], cwd=root)
        if not (ok and ok2):
            return False, "skipped: could not resolve local/remote commit hashes"

        if local_hash == remote_hash:
            return False, "up to date"

        # Fast-forward only, ever: is local HEAD an ancestor of the remote ref?
        is_ancestor, _ = _git(["merge-base", "--is-ancestor", "HEAD", remote_ref], cwd=root)
        if not is_ancestor:
            return False, (
                f"skipped: local {branch!r} has diverged from {remote_ref!r} "
                "(local commits not yet pushed) -- fast-forward not possible, never merges/rebases automatically"
            )

        ok, merge_output = _git(["merge", "--ff-only", remote_ref], cwd=root)
        if not ok:
            return False, f"fast-forward failed unexpectedly: {merge_output}"
        return True, f"pulled {local_hash[:10]} -> {remote_hash[:10]} on {branch!r}"


_sync_scheduler: Optional[GitSyncScheduler] = None
_sync_scheduler_lock = threading.Lock()


def get_git_sync_scheduler() -> GitSyncScheduler:
    global _sync_scheduler
    with _sync_scheduler_lock:
        if _sync_scheduler is None:
            _sync_scheduler = GitSyncScheduler()
        return _sync_scheduler


def reset_git_sync_scheduler() -> None:
    """Test-only. Production code never calls this."""
    global _sync_scheduler
    with _sync_scheduler_lock:
        _sync_scheduler = None
