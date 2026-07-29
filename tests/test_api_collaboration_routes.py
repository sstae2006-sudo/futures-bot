"""HTTP-level tests for `/api/work-items*` -- the Active Work Registry's
routes. See `collaboration/store.py`/`api/collaboration_service.py` for
the underlying logic.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from futures_bot.api.app import create_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data.db"))
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
    return TestClient(create_app())


class TestCreateWorkItem:
    def test_create_without_overlap(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": "Fix login bug", "estimated_files": ["src/auth.py"]})

        assert resp.status_code == 200
        body = resp.json()
        assert body["work_item"]["title"] == "Fix login bug"
        assert body["work_item"]["status"] == "open"
        assert body["overlap_warnings"] == []

    def test_create_with_owner_is_claimed(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": "Task", "owner_user_id": "u1"})

        assert resp.json()["work_item"]["status"] == "claimed"

    def test_create_warns_about_file_overlap_but_still_creates(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "First", "estimated_files": ["src/a.py"]})

        resp = client.post("/api/work-items", json={"title": "Second", "estimated_files": ["src/a.py"]})

        assert resp.status_code == 200
        assert len(resp.json()["overlap_warnings"]) == 1
        assert resp.json()["overlap_warnings"][0]["risk"] in ("low", "medium", "high", "critical")

    def test_invalid_priority_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": "Task", "priority": "urgent"})

        assert resp.status_code == 422

    def test_empty_title_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": ""})

        assert resp.status_code == 422


class TestListAndGet:
    def test_list_and_get_round_trip(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        created = client.post("/api/work-items", json={"title": "Task"}).json()["work_item"]

        resp = client.get(f"/api/work-items/{created['id']}")
        assert resp.status_code == 200
        assert resp.json() == created

        resp = client.get("/api/work-items")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_filters_by_status(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Open"})
        client.post("/api/work-items", json={"title": "Claimed", "owner_user_id": "u1"})

        resp = client.get("/api/work-items", params={"status": "claimed"})

        assert [i["title"] for i in resp.json()] == ["Claimed"]

    def test_get_unknown_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/work-items/does-not-exist")

        assert resp.status_code == 400

    def test_list_filters_by_org_id(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Org A", "org_id": "org-a"})
        client.post("/api/work-items", json={"title": "Org B", "org_id": "org-b"})

        resp = client.get("/api/work-items", params={"org_id": "org-a"})

        assert [i["title"] for i in resp.json()] == ["Org A"]

    def test_org_scoped_overlap_does_not_leak_across_orgs(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Org A task", "estimated_files": ["shared.py"], "org_id": "org-a"})

        resp = client.post("/api/work-items", json={
            "title": "Org B task", "estimated_files": ["shared.py"], "org_id": "org-b",
        })

        assert resp.json()["overlap_warnings"] == []


class TestClaimReleaseCompleteReassign:
    def test_full_lifecycle(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Task"}).json()["work_item"]

        resp = client.post(f"/api/work-items/{item['id']}/claim", json={"user_id": "u1"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "claimed"

        resp = client.post(f"/api/work-items/{item['id']}/release")
        assert resp.status_code == 200
        assert resp.json()["status"] == "open"

        client.post(f"/api/work-items/{item['id']}/claim", json={"user_id": "u1"})
        resp = client.post(f"/api/work-items/{item['id']}/reassign", json={"user_id": "u2"})
        assert resp.status_code == 200
        assert resp.json()["owner_user_id"] == "u2"

        resp = client.post(f"/api/work-items/{item['id']}/complete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_claiming_already_claimed_by_someone_else_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Task", "owner_user_id": "u1"}).json()["work_item"]

        resp = client.post(f"/api/work-items/{item['id']}/claim", json={"user_id": "u2"})

        assert resp.status_code == 400


class TestOverlapAndActivity:
    def test_overlap_endpoint_recomputes_against_current_active_items(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "First", "estimated_files": ["src/a.py"]}).json()["work_item"]
        assert client.get(f"/api/work-items/{item['id']}/overlap").json() == []

        client.post("/api/work-items", json={"title": "Second", "estimated_files": ["src/a.py"]})

        resp = client.get(f"/api/work-items/{item['id']}/overlap")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_activity_log_reflects_the_lifecycle(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Task"}).json()["work_item"]
        client.post(f"/api/work-items/{item['id']}/claim", json={"user_id": "u1"})
        client.post(f"/api/work-items/{item['id']}/complete")

        resp = client.get("/api/work-items-activity", params={"work_item_id": item["id"]})

        assert resp.status_code == 200
        events = [a["event"] for a in reversed(resp.json())]
        assert events == ["created", "claimed", "completed"]


class TestMergeSummary:
    def test_no_overlap_reports_no_risk(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items/merge-summary", json={"changed_files": ["src/unrelated.py"]})

        assert resp.status_code == 200
        body = resp.json()
        assert body["overlap_warnings"] == []
        assert body["highest_risk"] == "no_risk"
        assert body["related_work_item"] is None

    def test_overlapping_active_work_is_surfaced(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Other task", "estimated_files": ["src/a.py", "src/b.py"]})

        resp = client.post("/api/work-items/merge-summary", json={"changed_files": ["src/a.py", "src/b.py"]})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["overlap_warnings"]) == 1
        assert body["highest_risk"] == "critical"

    def test_completed_work_items_are_not_flagged_as_overlap(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Done", "estimated_files": ["src/a.py"]}).json()["work_item"]
        client.post(f"/api/work-items/{item['id']}/complete")

        resp = client.post("/api/work-items/merge-summary", json={"changed_files": ["src/a.py"]})

        assert resp.json()["overlap_warnings"] == []

    def test_related_work_item_included_when_id_supplied(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "My task", "estimated_files": ["src/a.py"]}).json()["work_item"]

        resp = client.post("/api/work-items/merge-summary", json={
            "changed_files": ["src/a.py"], "work_item_id": item["id"],
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["related_work_item"]["id"] == item["id"]
        # The referenced item itself must not appear in its own overlap list.
        assert body["overlap_warnings"] == []

    def test_unknown_related_work_item_id_is_tolerated(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items/merge-summary", json={
            "changed_files": ["src/a.py"], "work_item_id": "does-not-exist",
        })

        assert resp.status_code == 200
        assert resp.json()["related_work_item"] is None

    def test_empty_changed_files_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items/merge-summary", json={"changed_files": []})

        assert resp.status_code == 422


class TestOwnerTypeAndStatusLifecycle:
    def test_owner_type_defaults_to_human(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": "Task"})

        assert resp.json()["work_item"]["owner_type"] == "human"

    def test_owner_type_can_be_ai(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": "AI task", "owner_type": "ai"})

        assert resp.json()["work_item"]["owner_type"] == "ai"

    def test_invalid_owner_type_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items", json={"title": "Task", "owner_type": "robot"})

        assert resp.status_code == 422

    def test_status_route_moves_through_lifecycle(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Task"}).json()["work_item"]

        resp = client.post(f"/api/work-items/{item['id']}/status", json={"status": "in_progress"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_status_route_rejects_open_claimed_completed(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Task"}).json()["work_item"]

        resp = client.post(f"/api/work-items/{item['id']}/status", json={"status": "completed"})

        assert resp.status_code == 422  # not in ManualWorkItemStatusLiteral

    def test_status_route_unknown_item_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items/does-not-exist/status", json={"status": "testing"})

        assert resp.status_code == 400


class TestBranchInfoRoutes:
    def test_current_branch_info_is_always_200(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/git/branch-info")

        assert resp.status_code == 200
        assert "notes" in resp.json()

    def test_named_branch_info(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/git/branch-info", params={"branch": "main"})

        assert resp.status_code == 200

    def test_work_item_branch_info_uses_the_items_own_branch(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Task", "branch": "main"}).json()["work_item"]

        resp = client.get(f"/api/work-items/{item['id']}/branch-info")

        assert resp.status_code == 200

    def test_work_item_branch_info_unknown_item_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/work-items/does-not-exist/branch-info")

        assert resp.status_code == 400


class TestOverlapV2Route:
    def test_overlap_v2_for_an_isolated_item_is_empty(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Solo task"}).json()["work_item"]

        resp = client.get(f"/api/work-items/{item['id']}/overlap-v2")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_overlap_v2_detects_keyword_overlap(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Refactor authentication middleware"})

        item = client.post("/api/work-items", json={"title": "Fix authentication bug"}).json()["work_item"]
        resp = client.get(f"/api/work-items/{item['id']}/overlap-v2")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert "confidence" in body[0]
        assert "factors" in body[0]

    def test_overlap_v2_unknown_item_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/work-items/does-not-exist/overlap-v2")

        assert resp.status_code == 400


class TestConflictsRoute:
    def test_no_active_items_means_no_conflicts(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/work-items/conflicts")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_finds_a_conflict_between_two_items(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "First", "estimated_files": ["src/shared.py"]})
        client.post("/api/work-items", json={"title": "Second", "estimated_files": ["src/shared.py"]})

        resp = client.get("/api/work-items/conflicts")

        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestPreWorkCheckRoute:
    def test_no_overlap_recommends_proceed(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items/pre-work-check", json={"proposed_files": ["src/brand_new.py"]})

        assert resp.status_code == 200
        body = resp.json()
        assert body["suggested_action"] == "proceed"
        assert "branch_info" in body

    def test_heavy_overlap_recommends_a_different_task(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={
            "title": "Existing task touching many things",
            "estimated_files": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        })

        resp = client.post("/api/work-items/pre-work-check", json={
            "proposed_files": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["suggested_action"] == "choose_different_task"
        assert len(body["overlap_warnings"]) == 1


class TestMergeReadinessRoute:
    def test_clean_change_scores_high(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items/merge-readiness", json={"changed_files": ["a.py"]})

        assert resp.status_code == 200
        body = resp.json()
        assert body["test_status"] == "unknown"
        assert 0 <= body["score"] <= 100
        assert "factors" in body

    def test_empty_changed_files_is_422(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items/merge-readiness", json={"changed_files": []})

        assert resp.status_code == 422

    def test_overlapping_change_lowers_score(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={
            "title": "Existing", "estimated_files": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        })

        clean = client.post("/api/work-items/merge-readiness", json={"changed_files": ["z.py"]}).json()
        conflicting = client.post("/api/work-items/merge-readiness", json={
            "changed_files": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        }).json()

        assert conflicting["score"] < clean["score"]


class TestTimelineRoute:
    def test_returns_work_item_events(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Task"})

        resp = client.get("/api/activity/timeline", params={"include_commits": False})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["kind"] == "work_item"
        assert body[0]["title"] == "created"

    def test_filters_by_event_type(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Task"}).json()["work_item"]
        client.post(f"/api/work-items/{item['id']}/claim", json={"user_id": "u1"})

        resp = client.get("/api/activity/timeline", params={"event_type": "claimed", "include_commits": False})

        assert resp.status_code == 200
        assert [e["title"] for e in resp.json()] == ["claimed"]

    def test_respects_limit(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        for i in range(3):
            client.post("/api/work-items", json={"title": f"Task {i}"})

        resp = client.get("/api/activity/timeline", params={"include_commits": False, "limit": 2})

        assert len(resp.json()) == 2


class TestContextBundleRoute:
    """SIL Phase 4's "Automatic AI Context" call --
    `collaboration/context_bundle.py`'s module docstring covers what each
    field is (and isn't)."""

    def test_empty_request_still_returns_branch_and_commits(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/collaboration/context-bundle", json={})

        assert resp.status_code == 200
        body = resp.json()
        assert body["active_work_items"] == []
        assert body["similar_past_work"] == []
        assert "branch_info" in body
        assert isinstance(body["recent_commits"], list)

    def test_includes_active_work_items(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Existing task", "estimated_files": ["a.py"]})

        resp = client.post("/api/collaboration/context-bundle", json={"proposed_files": ["b.py"]})

        assert resp.status_code == 200
        assert [i["title"] for i in resp.json()["active_work_items"]] == ["Existing task"]

    def test_surfaces_overlap_warnings_for_proposed_files(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Existing task", "estimated_files": ["a.py"]})

        resp = client.post("/api/collaboration/context-bundle", json={"proposed_files": ["a.py"]})

        assert resp.status_code == 200
        assert len(resp.json()["overlap_warnings"]) == 1

    def test_similar_past_work_matches_completed_items_by_keyword(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post(
            "/api/work-items", json={"title": "Refactor authentication middleware", "estimated_files": ["auth.py"]},
        ).json()["work_item"]
        client.post(f"/api/work-items/{item['id']}/complete")

        resp = client.post(
            "/api/collaboration/context-bundle",
            json={"title": "Fix authentication middleware bug", "proposed_files": ["auth.py"]},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["active_work_items"] == []  # completed items aren't "active"
        assert [i["title"] for i in body["similar_past_work"]] == ["Refactor authentication middleware"]

    def test_org_scoping_excludes_other_orgs_active_work(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Org A task", "org_id": "org-a"})

        resp = client.post("/api/collaboration/context-bundle", json={"org_id": "org-b"})

        assert resp.status_code == 200
        assert resp.json()["active_work_items"] == []


class TestDraftWorkItemRoutes:
    """SIL Phase 4's git-watcher output. The scheduler itself is tested
    separately (`tests/test_git_watcher.py`); these tests exercise the
    HTTP-level draft lifecycle a draft goes through once created (drafts
    are created directly via the store here, matching how the
    background thread would)."""

    def _create_draft(self, tmp_path, monkeypatch, **overrides):
        from futures_bot.collaboration.store import get_collaboration_store

        client = _client(tmp_path, monkeypatch)
        kwargs = {"item_id": "d1", "title": "Uncommitted changes: src", "estimated_files": ["src/a.py"], "is_draft": True}
        kwargs.update(overrides)
        get_collaboration_store().create_work_item(**kwargs)
        return client

    def test_list_drafts_only_returns_drafts(self, tmp_path, monkeypatch):
        client = self._create_draft(tmp_path, monkeypatch)
        client.post("/api/work-items", json={"title": "Real item"})

        resp = client.get("/api/work-items/drafts")

        assert resp.status_code == 200
        assert [i["id"] for i in resp.json()] == ["d1"]

    def test_approve_draft_clears_the_flag(self, tmp_path, monkeypatch):
        client = self._create_draft(tmp_path, monkeypatch)

        resp = client.post("/api/work-items/d1/approve-draft")

        assert resp.status_code == 200
        assert resp.json()["is_draft"] is False
        assert client.get("/api/work-items/drafts").json() == []

    def test_approve_unknown_draft_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        resp = client.post("/api/work-items/does-not-exist/approve-draft")

        assert resp.status_code == 400

    def test_approve_a_real_item_is_400(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Real item"}).json()["work_item"]

        resp = client.post(f"/api/work-items/{item['id']}/approve-draft")

        assert resp.status_code == 400

    def test_discard_draft_deletes_it(self, tmp_path, monkeypatch):
        client = self._create_draft(tmp_path, monkeypatch)

        resp = client.delete("/api/work-items/d1/draft")

        assert resp.status_code == 200
        assert resp.json() == {"discarded": True}
        assert client.get("/api/work-items/d1").status_code == 400

    def test_discard_a_real_item_is_400_and_does_not_delete_it(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        item = client.post("/api/work-items", json={"title": "Real item"}).json()["work_item"]

        resp = client.delete(f"/api/work-items/{item['id']}/draft")

        assert resp.status_code == 400
        assert client.get(f"/api/work-items/{item['id']}").status_code == 200


class TestGitWatcherStatusRoute:
    def test_returns_not_running_when_the_watcher_was_never_started(self, tmp_path, monkeypatch):
        from futures_bot.collaboration.git_watcher import reset_git_watcher

        reset_git_watcher()
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/collaboration/git-watcher/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] is False
        assert body["cycles_completed"] == 0
        assert body["drafts_created_count"] == 0


class TestGitSyncStatusRoute:
    def test_returns_not_running_when_never_started(self, tmp_path, monkeypatch):
        from futures_bot.collaboration.git_sync import reset_git_sync_scheduler

        reset_git_sync_scheduler()
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/collaboration/git-sync/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] is False
        assert body["cycles_completed"] == 0
        assert body["pulls_applied_count"] == 0


class TestAutomationStatusRoute:
    def test_returns_all_schedulers_not_running_by_default(self, tmp_path, monkeypatch):
        from futures_bot.collaboration.git_sync import reset_git_sync_scheduler
        from futures_bot.collaboration.git_watcher import reset_git_watcher
        from futures_bot.collaboration.maintenance import reset_maintenance_scheduler

        reset_git_watcher()
        reset_maintenance_scheduler()
        reset_git_sync_scheduler()
        client = _client(tmp_path, monkeypatch)

        resp = client.get("/api/automation/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["git_watcher"]["running"] is False
        assert body["maintenance"]["running"] is False
        assert body["maintenance"]["stale_drafts_discarded_count"] == 0
        assert body["git_sync"]["running"] is False
        assert body["git_sync"]["pulls_applied_count"] == 0
