"""Merge-readiness scoring -- a single explainable 0-100 score built only
from what this codebase can actually observe before a merge: Overlap
Engine V2's conflict risk, how long the branch has been diverged, how far
behind its base it's fallen, and how large the change is. Deliberately
does NOT report pass/fail test status: that needs real CI integration
(reading a workflow run's result, or executing the suite here and
blocking on it), which is a separate, larger effort -- see ROADMAP.md's
"Future" section. Guessing at test status from indirect signals would be
worse than admitting we don't know it, so `test_status` is always the
literal string `"unknown"`, not a guess.

Same heuristic, explainable-over-clever design as `overlap.py`/
`overlap_v2.py`: every point deducted has a named factor and a
human-readable explanation, not a black-box model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .git_info import BranchInfo
from .overlap_v2 import OverlapWarningV2

_OVERLAP_PENALTY = {"critical": -40, "high": -25, "medium": -10, "low": -5, "no_risk": 0}

_READY_THRESHOLD = 85
_NEEDS_REVIEW_THRESHOLD = 60
_RISKY_THRESHOLD = 35


@dataclass(frozen=True)
class ReadinessFactor:
    name: str
    penalty: int  # <= 0
    explanation: str


@dataclass(frozen=True)
class MergeReadiness:
    score: int  # 0-100
    level: str  # ready | needs_review | risky | not_ready
    test_status: str  # always "unknown" -- see module docstring
    factors: list[ReadinessFactor] = field(default_factory=list)


def _level(score: int) -> str:
    if score >= _READY_THRESHOLD:
        return "ready"
    if score >= _NEEDS_REVIEW_THRESHOLD:
        return "needs_review"
    if score >= _RISKY_THRESHOLD:
        return "risky"
    return "not_ready"


def _overlap_factor(overlap_warnings: list[OverlapWarningV2]) -> ReadinessFactor:
    if not overlap_warnings:
        return ReadinessFactor("overlap", 0, "No Overlap Engine V2 conflicts against currently active work.")
    top = overlap_warnings[0]
    penalty = _OVERLAP_PENALTY[top.risk]
    return ReadinessFactor(
        "overlap", penalty,
        f"Highest overlap risk is {top.risk} (with '{top.title}', confidence {top.confidence}/100).",
    )


def _branch_age_factor(branch_info: BranchInfo) -> ReadinessFactor:
    days = branch_info.branch_age_days
    if days is None:
        return ReadinessFactor("branch_age", 0, "Branch age unavailable (no base branch to compare against).")
    if days > 14:
        return ReadinessFactor("branch_age", -15, f"Branch diverged {days:.1f} days ago -- likely to have drifted from its base.")
    if days > 7:
        return ReadinessFactor("branch_age", -8, f"Branch diverged {days:.1f} days ago.")
    return ReadinessFactor("branch_age", 0, f"Branch diverged {days:.1f} day(s) ago -- recent.")


def _behind_factor(branch_info: BranchInfo) -> ReadinessFactor:
    behind = branch_info.behind
    if behind is None:
        return ReadinessFactor("behind_base", 0, "Ahead/behind counts unavailable.")
    if behind > 20:
        return ReadinessFactor("behind_base", -10, f"{behind} commits behind base -- a rebase/merge is likely needed first.")
    if behind > 5:
        return ReadinessFactor("behind_base", -5, f"{behind} commits behind base.")
    return ReadinessFactor("behind_base", 0, f"{behind} commit(s) behind base -- current.")


def _size_factor(changed_files: list[str]) -> ReadinessFactor:
    count = len(changed_files)
    if count > 30:
        return ReadinessFactor("change_size", -15, f"{count} files changed -- a large diff is harder to review safely.")
    if count > 15:
        return ReadinessFactor("change_size", -8, f"{count} files changed.")
    return ReadinessFactor("change_size", 0, f"{count} file(s) changed -- a reviewable size.")


def compute_merge_readiness(
    changed_files: list[str], overlap_warnings: list[OverlapWarningV2], branch_info: BranchInfo,
) -> MergeReadiness:
    factors = [
        _overlap_factor(overlap_warnings),
        _branch_age_factor(branch_info),
        _behind_factor(branch_info),
        _size_factor(changed_files),
    ]
    score = max(0, min(100, 100 + sum(f.penalty for f in factors)))
    return MergeReadiness(score=score, level=_level(score), test_status="unknown", factors=factors)
