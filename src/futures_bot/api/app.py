"""Assembles the FastAPI app.

Run it with:

    uvicorn futures_bot.api.app:app --reload

or ``python -m futures_bot.api`` (see ``__main__.py``). Single-worker by
convention -- see `store.py`'s docstring on why a multi-worker deployment
needs a different persistence layer first.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..collaboration.git_watcher import get_git_watcher
from ..config import load_settings
from ..journal import LOGGER_NAME
from ..research_server.orchestrator import get_research_server
from .connected_users import ConnectedUsersMiddleware
from .routes import (
    accounts, backtests, collaboration, compare, experiments, imports, jobs, live, market_data, ml, optimizer,
    reports, research_server, strategies, system, trades,
)
from .services import ApiError

log = logging.getLogger(LOGGER_NAME)

#: Same default `cli.py` uses for `--config`, and the same env-var
#: override convention `FUTURES_BOT_RESEARCH_DB`/`FUTURES_BOT_MARKET_DATA_DB`
#: already establish -- lets a test point autonomous-mode auto-start at a
#: config file elsewhere without needing `monkeypatch.chdir`.
_CONFIG_PATH_ENV = "FUTURES_BOT_CONFIG"


def _config_path() -> Path:
    return Path(os.environ.get(_CONFIG_PATH_ENV, "config.yaml"))


def _maybe_start_research_server() -> None:
    """Phase 8B: autonomous mode is opt-in (`research_server.enabled` in
    config.yaml, default `False`) -- everyone who was already running the
    dashboard before this phase existed sees zero behavior change unless
    they deliberately turn it on. A config file that doesn't exist (most
    test fixtures, and anyone who hasn't run `cp config.example.yaml
    config.yaml` yet) or that fails to parse is treated as "not enabled,"
    never as a reason the whole API should fail to boot."""
    config_path = _config_path()
    if not config_path.exists():
        return
    try:
        settings = load_settings(config_path)
    except Exception as exc:  # noqa: BLE001 -- a bad config must not crash the API's own startup.
        log.error("Could not load %s for research-server auto-start: %s", config_path, exc)
        return
    if not settings.research_server.enabled:
        return

    api_key = os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        log.warning(
            "research_server.enabled is true in %s but MASSIVE_API_KEY is not set -- "
            "autonomous mode was not started. Set the environment variable and restart, "
            "or start it manually from the Research Server dashboard page once a key is set.",
            config_path,
        )
        return

    try:
        get_research_server().start(settings, api_key)
        log.info("Autonomous research server started on boot (config: %s).", config_path)
    except Exception as exc:  # noqa: BLE001 -- see docstring: never take the API down over this.
        log.error("Failed to auto-start the research server: %s", exc, exc_info=True)


def _maybe_start_automation() -> None:
    """SIL Phase 4: opt-in via `automation.enabled` in config.yaml
    (default `False` -- see `config.py::AutomationSettings`'s docstring).
    Same "never fail API startup over this" posture as
    `_maybe_start_research_server` above: a missing/unparseable config
    file is treated as "not enabled," never as a reason the whole API
    should fail to boot."""
    config_path = _config_path()
    if not config_path.exists():
        return
    try:
        settings = load_settings(config_path)
    except Exception as exc:  # noqa: BLE001 -- a bad config must not crash the API's own startup.
        log.error("Could not load %s for automation auto-start: %s", config_path, exc)
        return
    if not settings.automation.enabled:
        return
    try:
        get_git_watcher().start(interval_seconds=settings.automation.git_watcher_interval_seconds)
        log.info("SIL Phase 4 git-watcher started on boot (interval=%ss).", settings.automation.git_watcher_interval_seconds)
    except Exception as exc:  # noqa: BLE001 -- see docstring: never take the API down over this.
        log.error("Failed to auto-start the git-watcher: %s", exc, exc_info=True)


def _maybe_prime_db_engine() -> None:
    """Team-deployment mode only: if `FUTURES_BOT_DATABASE_URL` is set,
    build the shared pooled `Engine` now, tuned from `config.yaml`'s
    `deployment` section (see `db/engine.py::prime_engine`'s docstring for
    why this has to happen explicitly rather than relying on whichever
    store constructs first). A missing/unparseable config file falls back
    to `prime_engine`'s own defaults -- same "never fail API startup over
    a bad config file" posture as `_maybe_start_research_server` below."""
    from ..db.engine import prime_engine

    config_path = _config_path()
    pool_size, max_overflow, pool_recycle_seconds = 5, 10, 1800
    if config_path.exists():
        try:
            deployment = load_settings(config_path).deployment
            pool_size, max_overflow, pool_recycle_seconds = (
                deployment.pool_size, deployment.max_overflow, deployment.pool_recycle_seconds,
            )
        except Exception as exc:  # noqa: BLE001 -- a bad config must not crash startup.
            log.error("Could not load %s for database pool tuning: %s", config_path, exc)
    engine = prime_engine(pool_size=pool_size, max_overflow=max_overflow, pool_recycle_seconds=pool_recycle_seconds)
    if engine is not None:
        log.info(
            "Database engine primed for team-deployment mode (pool_size=%d, max_overflow=%d, pool_recycle_seconds=%d).",
            pool_size, max_overflow, pool_recycle_seconds,
        )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _maybe_prime_db_engine()
    _maybe_start_research_server()
    _maybe_start_automation()
    yield
    try:
        get_research_server().stop()
    except Exception:  # noqa: BLE001 -- shutdown must not raise past this point.
        log.error("Failed to stop the research server during API shutdown.", exc_info=True)
    try:
        watcher = get_git_watcher()
        if watcher.status()["running"]:
            watcher.stop()
    except Exception:  # noqa: BLE001 -- shutdown must not raise past this point.
        log.error("Failed to stop the git-watcher during API shutdown.", exc_info=True)


def create_app() -> FastAPI:
    app = FastAPI(
        title="futures-bot Research API",
        description=(
            "Internal quantitative research workstation API -- backtests, optimization, trade "
            "analysis, and reports over historical data, plus a dashboard-controlled paper-trading "
            "session (/api/live/*, PaperBroker only, enforced at runtime) and an opt-in autonomous "
            "research server (/api/research-server/*, Phase 8B). Nothing here can place, "
            "modify, or cancel a REAL order under any configuration -- see "
            "docs/RESEARCH_INTERFACE.md and api/live_session.py's module docstring."
        ),
        version=__version__,
        lifespan=_lifespan,
    )

    # Local-dev CORS: the React dev server runs on a different port
    # (Vite's default 5173) than uvicorn's default 8000. Wide open on
    # purpose for this internal tool -- there is no user data or write
    # access to anything beyond this process's own research database and
    # generated report files, and it is not meant to be exposed past
    # localhost/a trusted network (see docs/RESEARCH_INTERFACE.md).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Feeds /api/system/health's "connected users" field -- see
    # connected_users.py's module docstring for exactly what this can and
    # can't mean without a real auth/session system.
    app.add_middleware(ConnectedUsersMiddleware)

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def _key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
        # StrategyRegistry.get and similar lookups raise KeyError with an
        # already-helpful message (see strategy/base.py) -- surfaced as a
        # 404 rather than the generic 500 an uncaught KeyError would produce.
        return JSONResponse(status_code=404, content={"detail": str(exc).strip('"')})

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Without this, an unhandled exception in any route already returns
        # a safe generic 500 (Starlette's own default -- confirmed directly:
        # no traceback or exception message leaks to the client either way),
        # but the exception itself was never logged anywhere this app's own
        # logging system controls -- invisible to logs/bot.log and to the
        # dashboard's own Logs page (GET /api/logs), a real "silent failure"
        # (found 2026-07-28, KNOWN_ISSUES.md ISSUE-019). Logging it here,
        # through the same `futures_bot` logger every other log line in this
        # process uses, and returning the same JSON error shape
        # (`{"detail": ...}`) every other handler in this file already
        # returns -- Starlette's own default is a bare "Internal Server
        # Error" text body, inconsistent with the rest of this API. The
        # message itself stays generic (no exception text/traceback in the
        # response) -- this API has no authentication in front of it (see
        # this module's own docstring), so the exception detail belongs in
        # the server-side log, not the response body.
        log.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    for router_module in (
        system, strategies, backtests, trades, compare, optimizer, reports, ml, jobs, experiments, live,
        market_data, research_server, imports, accounts, collaboration,
    ):
        app.include_router(router_module.router)

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    _maybe_mount_frontend(app)

    return app


#: Where the deployment image's frontend build stage (see
#: deploy/Dockerfile.api) copies `frontend/dist` to. Overridable so a
#: non-Docker deployment (Option A in DEPLOYMENT.md) can point this at
#: wherever `npm run build` put its output.
_FRONTEND_DIST_ENV = "FUTURES_BOT_FRONTEND_DIST"


def _maybe_mount_frontend(app: FastAPI) -> None:
    """Serves the built React dashboard from this same process/port when a
    build is present -- optional and silent when it isn't, so local dev
    (Vite's own dev server on :5173, talking to this API cross-origin via
    the wide-open CORS policy above) and the test suite (which never builds
    the frontend) are both unaffected.

    Registered dead last, after every `/api/*` router: Starlette matches
    routes in registration order, so those explicit routes -- `/api/health`
    included -- are always tried first; only a path none of them own falls
    through to here.

    Deliberately not a single `StaticFiles(html=True)` mount at `/`: that
    only serves `index.html` for a *directory* URL (`/`, `/foo/`), not for
    an arbitrary unmatched path -- Starlette's own `html=True` docs cover
    this, and it means a hard refresh on a client-side (react-router) route
    like `/live` would 404. Instead: real files under `dist/` (the hashed
    JS/CSS bundle, favicon, ...) are served as themselves; everything else
    -- any path react-router, not this server, is meant to resolve --
    serves `index.html` so the app boots and its own router takes over.
    """
    dist = Path(os.environ.get(_FRONTEND_DIST_ENV, "frontend/dist")).resolve()
    index_file = dist / "index.html"
    if not index_file.is_file():
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_frontend(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        # `full_path` is attacker-controlled -- resolve and confirm it's
        # still inside `dist` before serving it, so a path like
        # `../../../../etc/passwd` (however it got past routing) falls
        # through to the `index.html` response below instead of reading an
        # arbitrary file off disk.
        requested = (dist / full_path).resolve()
        if full_path and requested.is_relative_to(dist) and requested.is_file():
            return FileResponse(requested)
        return FileResponse(index_file)


app = create_app()
