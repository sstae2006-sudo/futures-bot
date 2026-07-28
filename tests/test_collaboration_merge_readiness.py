"""Tests for `collaboration.merge_readiness` -- the explainable 0-100
merge-readiness score. `test_status` is deliberately never anything but
"unknown" (no CI integration exists) -- these tests confirm that
contract holds regardless of every other factor's value.
"""

from __future__ import annotations

from futures_bot.collaboration.git_info import BranchInfo
from futures_bot.collaboration.merge_readiness import compute_merge_readiness
from futures_bot.collaboration.overlap_v2 import OverlapWarningV2


def _branch_info(**overrides) -> BranchInfo:
    defaults = dict(
        branch="feature/x", is_detached=False, base_branch="main", branch_age_days=1.0,
        ahead=1, behind=0, last_commit=None, notes=(),
    )
    defaults.update(overrides)
    return BranchInfo(**defaults)


class TestScoreAndLevel:
    def test_pristine_branch_is_fully_ready(self):
        readiness = compute_merge_readiness([], [], _branch_info())
        assert readiness.score == 100
        assert readiness.level == "ready"

    def test_test_status_is_always_unknown(self):
        readiness = compute_merge_readiness(["a.py"] * 50, [], _branch_info(branch_age_days=30, behind=25))
        assert readiness.test_status == "unknown"

    def test_critical_overlap_drops_score_substantially(self):
        warning = OverlapWarningV2(work_item_id="w1", title="Other", owner_user_id="alice", risk="critical", confidence=90, factors={})
        readiness = compute_merge_readiness([], [warning], _branch_info())
        assert readiness.score == 60
        assert any(f.name == "overlap" and f.penalty == -40 for f in readiness.factors)

    def test_old_branch_is_penalized(self):
        readiness = compute_merge_readiness([], [], _branch_info(branch_age_days=20))
        assert readiness.score < 100
        assert any(f.name == "branch_age" for f in readiness.factors)

    def test_unknown_branch_age_is_not_penalized(self):
        readiness = compute_merge_readiness([], [], _branch_info(branch_age_days=None))
        assert readiness.score == 100

    def test_far_behind_base_is_penalized(self):
        readiness = compute_merge_readiness([], [], _branch_info(behind=25))
        assert readiness.score < 100

    def test_large_changeset_is_penalized(self):
        readiness = compute_merge_readiness([f"f{i}.py" for i in range(40)], [], _branch_info())
        assert readiness.score < 100

    def test_score_never_goes_below_zero(self):
        warning = OverlapWarningV2(work_item_id="w1", title="Other", owner_user_id=None, risk="critical", confidence=100, factors={})
        readiness = compute_merge_readiness(
            [f"f{i}.py" for i in range(100)], [warning, warning, warning], _branch_info(branch_age_days=90, behind=100),
        )
        assert readiness.score >= 0
        assert readiness.level == "not_ready"

    def test_level_thresholds(self):
        assert compute_merge_readiness([], [], _branch_info()).level == "ready"

        warning = OverlapWarningV2(work_item_id="w1", title="Other", owner_user_id=None, risk="medium", confidence=30, factors={})
        readiness = compute_merge_readiness([], [warning], _branch_info(branch_age_days=10))
        assert readiness.level in ("needs_review", "risky", "ready")
