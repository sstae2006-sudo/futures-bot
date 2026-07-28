"""Read-only git introspection for the Active Work Registry -- current
branch, branch age, ahead/behind counts relative to a base branch, and
the last commit -- so Mission Control "always knows which work item
belongs to which branch" (SIL Phase 2's own wording) without storing any
of it: everything here is computed live via `git` subprocess calls, never
persisted, so it can't go stale and needs no schema/migration.

Best-effort by design: every function here returns `None`/empty fields
instead of raising when the repo isn't a git checkout, HEAD is detached,
there's no configured upstream, or `git` itself isn't on PATH -- all of
those are legitimate states for a Mission Control instance to observe,
not errors worth surfacing as a 500.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

#: Separates fields within one `git log --format` line -- a byte that
#: will never appear in a commit subject/author, unlike a comma or pipe.
_FIELD_SEP = "\x1f"
_GIT_TIMEOUT_SECONDS = 5


def _repo_root() -> Optional[Path]:
    """Walks up from this file's own location to find the repo root --
    independent of the API process's current working directory, unlike
    relying on `git`'s own cwd-relative discovery alone."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    return None


def repo_root() -> Optional[Path]:
    """Public entry point for other collaboration modules (`overlap_v2.py`,
    `timeline.py`) that need the repo root to read file contents or run
    their own git commands -- avoids each duplicating `_repo_root`'s
    upward-walk logic."""
    return _repo_root()


def _git(args: list[str], cwd: Optional[Path] = None) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@dataclass(frozen=True)
class Commit:
    hash: str
    short_hash: str
    subject: str
    author: str
    authored_at: Optional[str]


@dataclass(frozen=True)
class BranchInfo:
    branch: Optional[str]
    is_detached: bool
    base_branch: Optional[str]
    branch_age_days: Optional[float]
    ahead: Optional[int]
    behind: Optional[int]
    last_commit: Optional[Commit]
    #: Non-fatal problems encountered while gathering the above (e.g. "no
    #: git binary found", "not inside a git repository") -- surfaced so a
    #: caller/UI can explain a partially-empty result instead of silently
    #: showing blanks.
    notes: tuple[str, ...] = field(default_factory=tuple)


def current_branch(repo_root: Optional[Path] = None) -> Optional[str]:
    root = repo_root or _repo_root()
    name = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    if name is None or name == "HEAD":  # detached HEAD reports literally "HEAD"
        return None
    return name


def _parse_commit(raw: Optional[str]) -> Optional[Commit]:
    if not raw:
        return None
    parts = raw.split(_FIELD_SEP)
    if len(parts) != 4:
        return None
    full_hash, subject, author, authored_at = parts
    return Commit(hash=full_hash, short_hash=full_hash[:10], subject=subject, author=author, authored_at=authored_at)


def _last_commit(branch: str, repo_root: Optional[Path]) -> Optional[Commit]:
    fmt = f"%H{_FIELD_SEP}%s{_FIELD_SEP}%an{_FIELD_SEP}%aI"
    return _parse_commit(_git(["log", "-1", f"--format={fmt}", branch], cwd=repo_root))


def _ahead_behind(branch: str, base_branch: str, repo_root: Optional[Path]) -> tuple[Optional[int], Optional[int]]:
    raw = _git(["rev-list", "--left-right", "--count", f"{base_branch}...{branch}"], cwd=repo_root)
    if raw is None:
        return None, None
    try:
        behind_str, ahead_str = raw.split()
        return int(ahead_str), int(behind_str)
    except (ValueError, IndexError):
        return None, None


def _branch_age_days(branch: str, base_branch: str, repo_root: Optional[Path]) -> Optional[float]:
    """Age of the branch's own work -- time since it diverged from
    `base_branch`, not the repo's first commit. `--reverse` + `-1` finds
    the oldest commit unique to `branch`."""
    raw = _git(
        ["log", f"{base_branch}..{branch}", "--reverse", "--format=%aI", "-1"], cwd=repo_root,
    )
    if not raw:
        return None
    try:
        first_unique_commit_at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    now = datetime.now(timezone.utc)
    return round((now - first_unique_commit_at).total_seconds() / 86400, 2)


def get_branch_info(branch: Optional[str] = None, base_branch: str = "main") -> BranchInfo:
    """`branch=None` means "whatever's currently checked out". Every
    sub-lookup is independent and best-effort -- a missing base branch
    (e.g. this repo uses `master`) degrades `ahead`/`behind`/`branch_age_days`
    to `None` rather than failing the whole call."""
    notes: list[str] = []
    root = _repo_root()
    if root is None:
        return BranchInfo(
            branch=None, is_detached=False, base_branch=None, branch_age_days=None,
            ahead=None, behind=None, last_commit=None, notes=("Not inside a git repository.",),
        )

    resolved_branch = branch or current_branch(root)
    is_detached = resolved_branch is None
    if is_detached:
        notes.append("HEAD is detached (no branch currently checked out).")

    last_commit = _last_commit(resolved_branch or "HEAD", root)

    ahead = behind = None
    branch_age_days = None
    if resolved_branch is not None:
        base_exists = _git(["rev-parse", "--verify", base_branch], cwd=root) is not None
        if base_exists and resolved_branch != base_branch:
            ahead, behind = _ahead_behind(resolved_branch, base_branch, root)
            branch_age_days = _branch_age_days(resolved_branch, base_branch, root)
        elif not base_exists:
            notes.append(f"Base branch {base_branch!r} not found -- ahead/behind/age unavailable.")

    return BranchInfo(
        branch=resolved_branch, is_detached=is_detached, base_branch=base_branch if resolved_branch != base_branch else None,
        branch_age_days=branch_age_days, ahead=ahead, behind=behind, last_commit=last_commit, notes=tuple(notes),
    )


def recent_commits(limit: int = 20, branch: Optional[str] = None) -> list[Commit]:
    """Most recent commits on `branch` (default: whatever's checked out),
    newest first -- feeds the activity timeline (`timeline.py`). Empty
    list, never an exception, if this isn't a git repo or `git log`
    fails."""
    root = _repo_root()
    if root is None:
        return []
    fmt = f"%H{_FIELD_SEP}%s{_FIELD_SEP}%an{_FIELD_SEP}%aI"
    raw = _git(["log", f"-{max(limit, 0)}", f"--format={fmt}", branch or "HEAD"], cwd=root)
    if not raw:
        return []
    commits = [_parse_commit(line) for line in raw.splitlines()]
    return [c for c in commits if c is not None]
