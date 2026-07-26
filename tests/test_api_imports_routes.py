"""HTTP-level tests for `/api/imports/*` -- profile CRUD, upload -> preview,
confirm -> poll job -> trades visible through the *existing* `/api/trades`
endpoint (Trade Explorer's own data source, unmodified), and duplicate
detection on re-upload (Phase 10.1)."""

from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient

TRADOVATE_CSV = (
    "Order ID,Account,Contract,Product,B/S,Filled Qty,Fill Time,Avg Fill Price,Commission,P/L\n"
    "1,ACC1,MESZ5,MES,Buy,2,2024-01-01T09:00:00+00:00,5000,1.24,\n"
    "2,ACC1,MESZ5,MES,Sell,1,2024-01-01T10:00:00+00:00,5010,0.62,\n"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))

    from futures_bot.api import jobs
    from futures_bot.api.app import create_app

    jobs.reset_executor()
    yield TestClient(create_app())
    jobs.reset_executor()


def _wait_for_terminal(client, job_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish in time")


def _create_profile(client, name="john-doe"):
    resp = client.post("/api/imports/profiles", json={"name": name})
    assert resp.status_code == 200
    return resp.json()["id"]


def _upload(client, profile_id, content=TRADOVATE_CSV, filename="trades.csv"):
    files = {"file": (filename, io.BytesIO(content.encode()), "text/csv")}
    return client.post("/api/imports/upload", data={"profile_id": profile_id}, files=files)


class TestClientProfiles:
    def test_create_and_list(self, client):
        _create_profile(client, "alice")
        resp = client.get("/api/imports/profiles")
        assert resp.status_code == 200
        assert [p["name"] for p in resp.json()] == ["alice"]

    def test_duplicate_name_is_rejected(self, client):
        _create_profile(client, "alice")
        resp = client.post("/api/imports/profiles", json={"name": "alice"})
        assert resp.status_code == 400


class TestUploadPreview:
    def test_upload_detects_tradovate_and_returns_a_preview(self, client):
        profile_id = _create_profile(client)
        resp = _upload(client, profile_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["detected_format"] == "tradovate"
        assert body["total_rows"] == 2
        assert body["duplicate_count"] == 0
        assert body["error_count"] == 0
        assert body["matched_trade_count"] == 1
        assert len(body["preview_fill_rows"]) == 2

    def test_upload_against_unknown_profile_fails(self, client):
        resp = _upload(client, "not-a-real-profile-id")
        assert resp.status_code == 400

    def test_upload_with_bad_rows_reports_errors_without_failing_the_request(self, client):
        profile_id = _create_profile(client)
        bad_csv = (
            "Order ID,Account,Contract,Product,B/S,Filled Qty,Fill Time,Avg Fill Price,Commission,P/L\n"
            "1,ACC1,MESZ5,MES,Buy,notanumber,2024-01-01T09:00:00+00:00,5000,1.24,\n"
        )
        resp = _upload(client, profile_id, content=bad_csv)
        assert resp.status_code == 200
        assert resp.json()["error_count"] == 1
        assert resp.json()["total_rows"] == 1


class TestConfirmImport:
    def test_confirm_creates_trades_visible_via_existing_trades_endpoint(self, client):
        profile_id = _create_profile(client)
        upload = _upload(client, profile_id).json()

        resp = client.post(f"/api/imports/{upload['import_id']}/confirm", json={"mapping": {}})
        assert resp.status_code == 200
        job = resp.json()
        assert job["kind"] == "client_import"
        final = _wait_for_terminal(client, job["id"])
        assert final["status"] == "completed"

        trades = client.get("/api/trades", params={"strategy": "import:john-doe"}).json()
        assert len(trades) == 1
        assert trades[0]["side"] == "long"
        assert trades[0]["entry_metadata"]["pnl_basis"] == "computed_from_contract"

        history = client.get("/api/imports/history").json()
        assert len(history) == 1
        assert history[0]["status"] == "completed"
        assert history[0]["trades_created"] == 1

    def test_confirming_an_unknown_staging_id_fails_clearly(self, client):
        resp = client.post("/api/imports/does-not-exist/confirm", json={"mapping": {}})
        assert resp.status_code == 400

    def test_reupload_of_the_same_file_is_all_duplicates(self, client):
        profile_id = _create_profile(client)
        upload1 = _upload(client, profile_id).json()
        confirm1 = client.post(f"/api/imports/{upload1['import_id']}/confirm", json={"mapping": {}}).json()
        _wait_for_terminal(client, confirm1["id"])

        upload2 = _upload(client, profile_id).json()
        assert upload2["duplicate_count"] == 2
        assert upload2["matched_trade_count"] == 0

        confirm2 = client.post(f"/api/imports/{upload2['import_id']}/confirm", json={"mapping": {}}).json()
        final2 = _wait_for_terminal(client, confirm2["id"])
        assert final2["status"] == "completed"

        trades = client.get("/api/trades", params={"strategy": "import:john-doe"}).json()
        assert len(trades) == 1  # still just the one trade from the first import

        history = client.get("/api/imports/history", params={"profile_id": profile_id}).json()
        assert len(history) == 2
        assert history[0]["duplicate_fill_count"] == 2
        assert history[0]["trades_created"] == 0


class TestCancelStaging:
    def test_cancel_removes_the_staged_upload(self, client):
        profile_id = _create_profile(client)
        upload = _upload(client, profile_id).json()
        resp = client.delete(f"/api/imports/staging/{upload['import_id']}")
        assert resp.status_code == 200
        confirm = client.post(f"/api/imports/{upload['import_id']}/confirm", json={"mapping": {}})
        assert confirm.status_code == 400
