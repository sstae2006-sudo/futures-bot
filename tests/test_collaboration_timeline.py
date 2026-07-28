"""Tests for `collaboration.timeline.build_timeline` -- merges work-item
activity with real git commits into one searchable feed.
`monkeypatch.setattr` on `timeline.get_collaboration_store` points it at a
throwaway `CollaborationStore` so these tests never touch the real
project database, and `include_commits=False` isolates the work-item-only
behavior from this repo's actual (unpredictable, growing) commit history.
"""

from __future__ import annotations

import pytest

from futures_bot.collaboration import timeline
from futures_bot.collaboration.store import CollaborationStore
from futures_bot.collaboration.timeline import _parse_timestamp, build_timeline


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = CollaborationStore(tmp_path / "collab_test.db")
    monkeypatch.setattr(timeline, "get_collaboration_store", lambda: s)
    yield s
    s.close()


class TestParseTimestamp:
    def test_parses_sqlite_style_naive_timestamp(self):
        dt = _parse_timestamp("2026-07-28 15:00:00")
        assert dt.year == 2026 and dt.month == 7 and dt.day == 28

    def test_parses_iso_with_z(self):
        dt = _parse_timestamp("2026-07-28T15:00:00Z")
        assert dt.tzinfo is not None

    def test_parses_iso_with_offset(self):
        dt = _parse_timestamp("2026-07-28T15:00:00-04:00")
        assert dt.tzinfo is not None

    def test_naive_and_z_forms_of_the_same_instant_are_equal(self):
        assert _parse_timestamp("2026-07-28 15:00:00") == _parse_timestamp("2026-07-28T15:00:00Z")


class TestBuildTimeline:
    def test_merges_and_sorts_work_item_activity(self, store):
        store.create_work_item(item_id="w1", title="Task")
        store.claim_work_item("w1", "u1")
        store.complete_work_item("w1", actor_user_id="u1")

        entries = build_timeline(include_commits=False)

        assert [e.title for e in entries] == ["completed", "claimed", "created"]
        assert all(e.kind == "work_item" for e in entries)

    def test_filters_by_event_type(self, store):
        store.create_work_item(item_id="w1", title="Task")
        store.claim_work_item("w1", "u1")

        entries = build_timeline(event_type="claimed", include_commits=False)

        assert len(entries) == 1
        assert entries[0].title == "claimed"

    def test_filters_by_query_text_against_actor(self, store):
        store.create_work_item(item_id="w1", title="Task")
        store.claim_work_item("w1", "alice")
        store.release_work_item("w1", actor_user_id="bob")

        entries = build_timeline(q="alice", include_commits=False)

        assert len(entries) == 1
        assert entries[0].actor == "alice"

    def test_filters_by_work_item_id_excludes_commits(self, store):
        store.create_work_item(item_id="w1", title="Task")

        entries = build_timeline(work_item_id="w1", include_commits=True)

        assert all(e.kind == "work_item" for e in entries)

    def test_respects_limit(self, store):
        for i in range(5):
            store.create_work_item(item_id=f"w{i}", title=f"Task {i}")

        entries = build_timeline(include_commits=False, limit=2)

        assert len(entries) == 2

    def test_since_until_filters_out_of_range_entries(self, store):
        store.create_work_item(item_id="w1", title="Task")

        far_future_entries = build_timeline(since="2999-01-01T00:00:00Z", include_commits=False)
        far_past_entries = build_timeline(until="2000-01-01T00:00:00Z", include_commits=False)

        assert far_future_entries == []
        assert far_past_entries == []

    def test_no_activity_returns_empty(self, store):
        assert build_timeline(include_commits=False) == []
