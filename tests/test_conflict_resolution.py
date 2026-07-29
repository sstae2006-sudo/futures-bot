"""Unit tests for `collaboration.conflict_resolution` -- SIL Phase 6
Milestone 2's Conflict Resolution Assistant (suggested resolution +
integration order layered on top of Overlap Engine V2's existing
scoring).
"""

from __future__ import annotations

from futures_bot.collaboration.conflict_resolution import build_conflict_resolutions
from futures_bot.collaboration.overlap_v2 import OverlapWarningV2


def _item(item_id, title, status="open", created_at="2026-01-01 00:00:00", estimated_files=None):
    return {"id": item_id, "title": title, "status": status, "created_at": created_at, "estimated_files": estimated_files or []}


def _warning(work_item_id="wi2", title="Other", risk="high", confidence=60, owner="alice"):
    return OverlapWarningV2(work_item_id=work_item_id, title=title, owner_user_id=owner, risk=risk, confidence=confidence, factors={}, reason="reason text")


class TestBuildConflictResolutions:
    def test_combines_subsystems_from_both_items(self):
        this_item = _item("wi1", "This", estimated_files=["src/futures_bot/risk/manager.py"])
        other_item = _item("wi2", "Other", estimated_files=["src/futures_bot/strategy/base.py"])

        resolutions = build_conflict_resolutions(this_item, [_warning()], {"wi2": other_item})

        assert set(resolutions[0].architecture_components_affected) == {"Risk Management", "Strategy Engine"}

    def test_missing_other_item_still_produces_a_resolution(self):
        """A warning whose target is no longer in the active-items map
        (e.g. completed concurrently) must degrade gracefully, not raise."""
        this_item = _item("wi1", "This", estimated_files=["src/futures_bot/risk/manager.py"])

        resolutions = build_conflict_resolutions(this_item, [_warning()], {})

        assert len(resolutions) == 1
        assert resolutions[0].architecture_components_affected == ["Risk Management"]
        assert resolutions[0].suggested_resolution

    def test_high_risk_suggests_integration_order_and_owner_coordination(self):
        this_item = _item("wi1", "This", status="testing")
        other_item = _item("wi2", "Other", status="in_progress")

        resolutions = build_conflict_resolutions(this_item, [_warning(risk="high", owner="bob")], {"wi2": other_item})

        text = resolutions[0].suggested_resolution
        assert "This" in text  # further along the pipeline (testing > in_progress) -- integrates first
        assert "bob" in text

    def test_further_along_pipeline_item_integrates_first(self):
        this_item = _item("wi1", "Ahead", status="ready_for_review")
        other_item = _item("wi2", "Behind", status="open")

        resolutions = build_conflict_resolutions(this_item, [_warning(risk="critical")], {"wi2": other_item})

        text = resolutions[0].suggested_resolution
        assert text.index("Ahead") < text.index("Behind")

    def test_tied_pipeline_rank_breaks_on_created_at_ascending(self):
        this_item = _item("wi1", "Newer", status="open", created_at="2026-02-01 00:00:00")
        other_item = _item("wi2", "Older", status="open", created_at="2026-01-01 00:00:00")

        resolutions = build_conflict_resolutions(this_item, [_warning(risk="critical")], {"wi2": other_item})

        text = resolutions[0].suggested_resolution
        assert text.index("Older") < text.index("Newer")

    def test_medium_risk_recommends_a_quick_sync(self):
        this_item = _item("wi1", "This")
        resolutions = build_conflict_resolutions(this_item, [_warning(risk="medium", owner="carol")], {})

        assert "sync" in resolutions[0].suggested_resolution.lower()
        assert "carol" in resolutions[0].suggested_resolution

    def test_low_risk_recommends_no_special_sequencing(self):
        this_item = _item("wi1", "This")
        resolutions = build_conflict_resolutions(this_item, [_warning(risk="low", owner="dave")], {})

        assert "no special sequencing" in resolutions[0].suggested_resolution.lower()

    def test_unclaimed_owner_falls_back_to_generic_phrasing(self):
        this_item = _item("wi1", "This")
        resolutions = build_conflict_resolutions(this_item, [_warning(risk="low", owner=None)], {})

        assert "unclaimed" in resolutions[0].suggested_resolution.lower()

    def test_empty_warnings_returns_empty_list(self):
        this_item = _item("wi1", "This")
        assert build_conflict_resolutions(this_item, [], {}) == []
