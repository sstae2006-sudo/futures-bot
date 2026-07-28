"""Tests for `api.app`'s optional built-frontend mount (`_maybe_mount_frontend`)
-- part of Fix 7's deployment path: one process serving both the API and the
dashboard's static build (see deploy/Dockerfile.api). Silent no-op when no
build is present is what every other test in this suite already relies on
implicitly by calling `create_app()` without setting
`FUTURES_BOT_FRONTEND_DIST`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from futures_bot.api import services
from futures_bot.api.app import create_app
from futures_bot.journal import LOGGER_NAME


def _write_fake_build(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>dashboard shell</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    return dist


class TestNoFrontendBuild:
    def test_is_a_silent_no_op_when_the_dist_dir_does_not_exist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_FRONTEND_DIST", str(tmp_path / "does-not-exist"))
        client = TestClient(create_app())

        resp = client.get("/")

        assert resp.status_code == 404  # no frontend mounted, no catch-all route registered
        assert client.get("/api/health").status_code == 200


class TestFrontendMount:
    def test_serves_index_html_at_root(self, tmp_path, monkeypatch):
        dist = _write_fake_build(tmp_path)
        monkeypatch.setenv("FUTURES_BOT_FRONTEND_DIST", str(dist))
        client = TestClient(create_app())

        resp = client.get("/")

        assert resp.status_code == 200
        assert "dashboard shell" in resp.text

    def test_serves_a_real_static_asset_directly(self, tmp_path, monkeypatch):
        dist = _write_fake_build(tmp_path)
        monkeypatch.setenv("FUTURES_BOT_FRONTEND_DIST", str(dist))
        client = TestClient(create_app())

        resp = client.get("/assets/app.js")

        assert resp.status_code == 200
        assert "console.log" in resp.text

    def test_falls_back_to_index_html_for_a_client_side_route(self, tmp_path, monkeypatch):
        """A hard refresh on e.g. /live (a react-router route, not a real
        file on disk) must still boot the app instead of 404ing."""
        dist = _write_fake_build(tmp_path)
        monkeypatch.setenv("FUTURES_BOT_FRONTEND_DIST", str(dist))
        client = TestClient(create_app())

        resp = client.get("/live")

        assert resp.status_code == 200
        assert "dashboard shell" in resp.text

    def test_api_routes_still_take_precedence_over_the_frontend_mount(self, tmp_path, monkeypatch):
        dist = _write_fake_build(tmp_path)
        monkeypatch.setenv("FUTURES_BOT_FRONTEND_DIST", str(dist))
        client = TestClient(create_app())

        resp = client.get("/api/health")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_an_unknown_api_path_404s_rather_than_falling_back_to_the_frontend(self, tmp_path, monkeypatch):
        dist = _write_fake_build(tmp_path)
        monkeypatch.setenv("FUTURES_BOT_FRONTEND_DIST", str(dist))
        client = TestClient(create_app())

        resp = client.get("/api/this-route-does-not-exist")

        assert resp.status_code == 404
        assert "dashboard shell" not in resp.text

    def test_cannot_escape_the_dist_directory_via_path_traversal(self, tmp_path, monkeypatch):
        """httpx normalizes ".." segments client-side before a plain GET
        ever leaves the test process, so it can't be used here to prove the
        server-side guard works -- call the mounted route handler directly
        with a traversal payload instead, which is what actually protects
        against a request that arrives with a raw, unnormalized path."""
        import asyncio

        dist = _write_fake_build(tmp_path)
        secret = tmp_path / "secret.txt"
        secret.write_text("should never be served", encoding="utf-8")
        monkeypatch.setenv("FUTURES_BOT_FRONTEND_DIST", str(dist))

        route = next(r for r in create_app().routes if getattr(r, "path", None) == "/{full_path:path}")
        result = asyncio.run(route.endpoint(full_path="../secret.txt"))

        assert Path(result.path) == (dist / "index.html").resolve()


class TestUnhandledExceptionHandler:
    """Regression coverage (Stabilization Mode, 2026-07-28, KNOWN_ISSUES.md
    ISSUE-019): an unhandled exception in a route already returned a safe
    generic 500 (Starlette's own default -- no traceback/message leak
    either way), but was never logged anywhere this app's own logging
    system controls, a real "silent failure". These tests confirm the new
    catch-all handler logs the exception and returns a consistent JSON
    error shape, without disturbing the existing, more specific ApiError/
    KeyError/HTTPException handling.
    """

    def test_logs_and_returns_a_safe_500_for_a_genuinely_unexpected_exception(self, monkeypatch, caplog):
        monkeypatch.setattr(services, "generate_insights", lambda: (_ for _ in ()).throw(
            ValueError("deliberate test crash for stabilization sweep")
        ))
        client = TestClient(create_app(), raise_server_exceptions=False)

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            resp = client.get("/api/insights")

        assert resp.status_code == 500
        assert resp.json() == {"detail": "Internal server error"}
        assert "deliberate test crash for stabilization sweep" not in resp.text
        assert any(
            "Unhandled exception" in r.message and "deliberate test crash" in (r.exc_text or "")
            for r in caplog.records
        )

    def test_api_error_still_returns_400_not_the_generic_500(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "research.db"))
        client = TestClient(create_app())

        resp = client.get("/api/performance/does-not-exist")

        assert resp.status_code == 400
        assert resp.json() != {"detail": "Internal server error"}

    def test_http_exception_404_still_passes_through_unmodified(self):
        client = TestClient(create_app())

        resp = client.get("/api/this-route-does-not-exist")

        assert resp.status_code == 404
