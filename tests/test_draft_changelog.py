"""Tests for `tools/draft_changelog.py` -- SIL Phase 4's documentation
draft assistant. `git_info` calls are monkeypatched (no real git history
needed); the collaboration store is real but isolated via
`FUTURES_BOT_RESEARCH_DB` (the same `tmp_path`-scoped convention every
other collaboration test uses).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import draft_changelog  # noqa: E402

from futures_bot.collaboration.git_info import Commit
from futures_bot.collaboration.store import get_collaboration_store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))


def _commit(hash_="a" * 40, subject="Some change", authored_at="2026-07-28T00:00:00+00:00"):
    return Commit(hash=hash_, short_hash=hash_[:10], subject=subject, author="Test", authored_at=authored_at)


class TestBoundary:
    def test_no_changelog_history_returns_none_none(self, monkeypatch):
        monkeypatch.setattr(draft_changelog.git_info, "last_commit_touching", lambda path: None)
        ref, since = draft_changelog._boundary()
        assert ref is None
        assert since is None

    def test_returns_hash_and_parsed_datetime(self, monkeypatch):
        commit = _commit(authored_at="2026-07-20T12:00:00+00:00")
        monkeypatch.setattr(draft_changelog.git_info, "last_commit_touching", lambda path: commit)
        ref, since = draft_changelog._boundary()
        assert ref == "a" * 40
        assert since == datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_unparseable_date_returns_hash_with_none_datetime(self, monkeypatch):
        commit = _commit(authored_at="not-a-date")
        monkeypatch.setattr(draft_changelog.git_info, "last_commit_touching", lambda path: commit)
        ref, since = draft_changelog._boundary()
        assert ref == "a" * 40
        assert since is None


class TestRecentlyDoneWorkItems:
    def test_none_since_returns_every_done_item_regardless_of_date(self):
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Old completed")
        store.complete_work_item("w1")

        items = draft_changelog._recently_done_work_items(since=None)

        assert [i["id"] for i in items] == ["w1"]

    def test_excludes_items_not_yet_done(self):
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Still open")

        assert draft_changelog._recently_done_work_items(since=None) == []

    def test_since_filters_out_items_completed_before_the_boundary(self):
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Completed long ago")
        store.complete_work_item("w1")

        far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        items = draft_changelog._recently_done_work_items(since=far_future)

        assert items == []

    def test_since_includes_items_completed_after_the_boundary(self):
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Just completed")
        store.complete_work_item("w1")

        far_past = datetime(2000, 1, 1, tzinfo=timezone.utc)
        items = draft_changelog._recently_done_work_items(since=far_past)

        assert [i["id"] for i in items] == ["w1"]

    def test_merged_status_also_counts_as_done(self):
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Task")
        store.update_status("w1", "merged")

        items = draft_changelog._recently_done_work_items(since=None)

        assert [i["id"] for i in items] == ["w1"]


class TestBuildDraft:
    def test_includes_commit_subjects(self, monkeypatch):
        monkeypatch.setattr(draft_changelog.git_info, "last_commit_touching", lambda path: None)
        monkeypatch.setattr(
            draft_changelog.git_info, "commits_since", lambda ref, limit=200, branch=None: [_commit(subject="Fix the thing")],
        )

        draft = draft_changelog.build_draft()

        assert "Fix the thing" in draft
        assert "### Commits (1)" in draft

    def test_includes_completed_work_item_titles(self, monkeypatch):
        monkeypatch.setattr(draft_changelog.git_info, "last_commit_touching", lambda path: None)
        monkeypatch.setattr(draft_changelog.git_info, "commits_since", lambda ref, limit=200, branch=None: [])
        store = get_collaboration_store()
        store.create_work_item(item_id="w1", title="Ship the feature", estimated_files=["src/x.py"])
        store.complete_work_item("w1")

        draft = draft_changelog.build_draft()

        assert "Ship the feature" in draft
        assert "src/x.py" in draft

    def test_no_activity_still_produces_valid_scaffolding(self, monkeypatch):
        monkeypatch.setattr(draft_changelog.git_info, "last_commit_touching", lambda path: None)
        monkeypatch.setattr(draft_changelog.git_info, "commits_since", lambda ref, limit=200, branch=None: [])

        draft = draft_changelog.build_draft()

        assert "### Commits (0)" in draft
        assert "### Completed/merged work items (0)" in draft
        assert "**Breaking changes:**" in draft

    def test_never_touches_the_real_changelog_file(self, monkeypatch, tmp_path, capsys):
        """`main()` writes only to `--out`, never to CHANGELOG.md itself --
        the whole point of this being a draft assistant, not an editor."""
        monkeypatch.setattr(draft_changelog.git_info, "last_commit_touching", lambda path: None)
        monkeypatch.setattr(draft_changelog.git_info, "commits_since", lambda ref, limit=200, branch=None: [])
        out_path = tmp_path / "draft.md"

        draft_changelog.main(["--out", str(out_path)])

        assert out_path.is_file()
        assert not (tmp_path / "CHANGELOG.md").exists()
