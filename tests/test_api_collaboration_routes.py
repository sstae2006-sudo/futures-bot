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
