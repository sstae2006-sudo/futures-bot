"""Tests for `api.app`'s optional built-frontend mount (`_maybe_mount_frontend`)
-- part of Fix 7's deployment path: one process serving both the API and the
dashboard's static build (see deploy/Dockerfile.api). Silent no-op when no
build is present is what every other test in this suite already relies on
implicitly by calling `create_app()` without setting
`FUTURES_BOT_FRONTEND_DIST`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from futures_bot.api.app import create_app


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
