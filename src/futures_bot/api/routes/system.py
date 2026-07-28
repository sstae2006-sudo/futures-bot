from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

from ... import __version__
from ...config import load_settings
from .. import services
from ..connected_users import TRACKER
from ..schemas import DatabaseHealthOut, InsightOut, LogEntry, SystemHealthOut, SystemOverview

router = APIRouter(tags=["system"])

#: Recorded once, at module import time -- `app.py` imports every route
#: module exactly once per process, at startup, before the first request
#: can be served, so this is a faithful "process start" timestamp.
#: `time.monotonic()` (not `time.time()`) because uptime is an elapsed
#: duration, not a wall-clock instant -- immune to the system clock being
#: adjusted mid-run.
_PROCESS_START = time.monotonic()

#: Same env-var-override convention as `FUTURES_BOT_CONFIG`/
#: `FUTURES_BOT_RESEARCH_DB` -- written by
#: `tools/backup_timescaledb.py::_write_marker` (`db_backups/last_backup.json`
#: by default, matching that script's own `DEFAULT_BACKUP_DIR`/
#: `MARKER_FILENAME`), read here. Contains no credentials, so (unlike
#: `FUTURES_BOT_DATABASE_URL`) there's no reason this path itself needs to
#: be a secret.
_BACKUP_MARKER_ENV = "FUTURES_BOT_BACKUP_MARKER"
_DEFAULT_BACKUP_MARKER = Path("db_backups") / "last_backup.json"


def _backup_marker_path() -> Path:
    return Path(os.environ.get(_BACKUP_MARKER_ENV, str(_DEFAULT_BACKUP_MARKER)))


def _last_backup_at() -> Optional[str]:
    """Reads the timestamp `tools/backup_timescaledb.py::_write_marker`
    wrote after its most recent successful run (`{"timestamp": ...,
    "path": ..., "size_bytes": ...}`). Never raises: a missing marker (no
    backup has ever run) or a corrupt one (a backup crashed mid-write)
    both just mean "unknown," not a reason `/api/system/health` itself
    should fail."""
    try:
        payload = json.loads(_backup_marker_path().read_text())
        timestamp = payload.get("timestamp")
        return timestamp if isinstance(timestamp, str) else None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


@router.get("/api/system/health", response_model=SystemHealthOut)
def get_health() -> SystemHealthOut:
    """Team-deployment mode's real-data source for Mission Control's
    `StatusBar`/`HealthGrid` (see plan item #7). Every field here degrades
    gracefully rather than 500ing: an unconfigured/unreachable database, a
    missing backup marker, and zero other connected clients are all valid,
    expected states for a single-developer setup, not errors."""
    settings_deployment_environment = "development"
    config_path = Path(os.environ.get("FUTURES_BOT_CONFIG", "config.yaml"))
    if config_path.exists():
        try:
            settings_deployment_environment = load_settings(config_path).deployment.environment
        except Exception:  # noqa: BLE001 -- a bad config must not break the health route.
            pass

    try:
        # Lazy import: `db.health` pulls in SQLAlchemy, which -- per
        # `db/engine.py`'s own module docstring -- a SQLite-only
        # single-developer setup (the default) must never be required to
        # install. `ModuleNotFoundError` here just means "this process
        # can't possibly be team-deployment mode," the same conclusion
        # `check_database_health()` itself reaches when the env var is
        # unset -- not a reason this route should 500. (Can't reuse
        # `db.health.DatabaseHealth` for the fallback value either -- that
        # whole module fails to import for the identical reason.)
        from ...db.health import check_database_health

        db_health_out = DatabaseHealthOut(
            **check_database_health().__dict__
        )
    except ModuleNotFoundError:
        db_health_out = DatabaseHealthOut(configured=False, ok=False, latency_ms=None, error=None)
    return SystemHealthOut(
        version=__version__,
        environment=settings_deployment_environment,
        uptime_seconds=time.monotonic() - _PROCESS_START,
        database=db_health_out,
        last_backup_at=_last_backup_at(),
        connected_users=TRACKER.count(),
    )


@router.get("/api/system/overview", response_model=SystemOverview)
def get_overview() -> SystemOverview:
    return services.system_overview()


@router.get("/api/insights", response_model=list[InsightOut])
def get_insights() -> list[InsightOut]:
    return services.generate_insights()


@router.get("/api/logs", response_model=list[LogEntry])
def get_logs(limit: int = 200, kind: str | None = None) -> list[LogEntry]:
    return [LogEntry(**e) for e in services.read_logs(limit=limit, kind=kind)]
