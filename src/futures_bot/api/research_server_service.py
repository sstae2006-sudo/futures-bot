"""Phase 8B: the dashboard-facing surface over the autonomous research
server -- mirrors `live_session.py`/`market_data_service.py`'s split (a
manager/orchestrator object doing the work, a thin service module
translating to/from API schemas and mapping domain errors to `ApiError`).
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from ..config import load_settings
from ..research_server import insights as insights_module
from ..research_server.nightly_jobs import get_nightly_job_scheduler
from ..research_server.orchestrator import ResearchServerError, get_research_server
from ..research_server.paper_trader import PaperTraderError
from .schemas import ConfigDeploymentOut, InsightOut, ResearchServerStatusOut
from .services import ApiError
from .store import get_store

#: Full config.yaml snapshots, taken before every deploy/rollback -- the
#: real safety net for `deploy_strategy_params` below, since the mutation
#: itself goes through yaml.safe_load/dump and does not preserve comments.
CONFIG_BACKUP_DIR = Path("config_backups")

#: Guards the read -> backup -> write sequence in `deploy_strategy_params`/
#: `rollback_config_deployment` -- FastAPI runs sync route handlers across a
#: worker thread pool (see `api/store.py`'s docstring on the same concern for
#: `TradeStore`), so two deploys (or a deploy racing a rollback) landing on
#: different threads at the same instant could otherwise interleave and have
#: the later write silently clobber the earlier one's intent. A single
#: process-wide lock is enough here -- this is a single-process local
#: dashboard, not a multi-process deployment, so a stdlib `threading.Lock`
#: covers the real race without a new file-locking dependency.
_config_lock = threading.Lock()


def _get_api_key() -> str:
    return os.environ.get("MASSIVE_API_KEY", "")


def _require_api_key() -> str:
    api_key = _get_api_key()
    if not api_key:
        raise ApiError(
            "MASSIVE_API_KEY environment variable is not set. The research server's credential is read "
            "from the environment, never from config.yaml -- see docs/USER_MANUAL.md."
        )
    return api_key


def start_research_server(config_path: Path = Path("config.yaml")) -> ResearchServerStatusOut:
    settings = load_settings(config_path)
    api_key = _require_api_key()
    server = get_research_server()
    try:
        status = server.start(settings, api_key)
    except (ResearchServerError, PaperTraderError) as exc:
        raise ApiError(str(exc)) from exc
    return ResearchServerStatusOut(**status)


def stop_research_server() -> ResearchServerStatusOut:
    server = get_research_server()
    status = server.stop()
    return ResearchServerStatusOut(**status)


def research_server_status() -> ResearchServerStatusOut:
    server = get_research_server()
    return ResearchServerStatusOut(**server.status())


def run_nightly_batch_now(config_path: Path = Path("config.yaml")) -> str:
    """Triggers one nightly research batch immediately -- bypasses the
    daily-trigger-hour check, same operation the dashboard's manual button
    and tests use instead of waiting for the real clock."""
    settings = load_settings(config_path)
    scheduler = get_nightly_job_scheduler()
    return scheduler.run_now(settings, datetime.now(timezone.utc))


def get_findings(config_path: Path = Path("config.yaml")) -> list[InsightOut]:
    """Degradation / regime-drift / parameter-recommendation findings for
    every strategy `research_server.paper_strategies` names -- the
    dashboard's "recent discoveries" feed. Computed fresh on every call
    (see `research_server.insights`'s module docstring for why)."""
    settings = load_settings(config_path)
    findings: list[dict] = []
    for name in settings.research_server.paper_strategies:
        params = settings.strategy_params if name == settings.strategy_name else {}
        for finding in insights_module.all_findings(name, current_params=params):
            # Phase 10.2: a recommendation can only be *deployed* for the
            # strategy config.yaml's own `strategy_name` currently names --
            # that's the only strategy with a config-file strategy_params
            # slot at all (see `deploy_strategy_params`'s docstring). Other
            # paper_strategies entries still get the finding, just flagged
            # as not deployable, rather than a confusing button that fails.
            if finding["category"] == "recommendation" and finding.get("details"):
                finding["details"]["is_deployable"] = (name == settings.strategy_name)
            findings.append(finding)
    return [InsightOut(**f) for f in findings]


def _backup_config(config_path: Path) -> str:
    CONFIG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    backup_path = CONFIG_BACKUP_DIR / f"{timestamp}_{config_path.name}"
    backup_path.write_bytes(config_path.read_bytes())
    return str(backup_path)


def deploy_strategy_params(
    strategy: str, params: dict, run_id: Optional[str] = None, config_path: Path = Path("config.yaml"),
) -> ConfigDeploymentOut:
    """Writes `params` into config.yaml's `strategy_params` -- only when
    `strategy` matches the file's own `strategy_name`; deploying a
    recommendation for any other paper-traded strategy isn't structurally
    possible today (only the active strategy has a config-file
    strategy_params slot -- see `research_server/paper_trader.py`'s own
    "Simplification, stated plainly" docstring).

    Backs up the whole file first, byte-for-byte, comments included --
    the mutation itself goes through `yaml.safe_load`/`yaml.safe_dump`,
    which does **not** preserve comments or key ordering beyond
    `sort_keys=False`. The backup, not the round-trip, is what makes this
    safe to undo.
    """
    if not config_path.exists():
        raise ApiError(f"{config_path} does not exist.")

    with _config_lock:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if data.get("strategy_name") != strategy:
            raise ApiError(
                f"{strategy!r} is not the active strategy in {config_path} "
                f"(strategy_name={data.get('strategy_name')!r}). Only the active strategy's params live in "
                f"config.yaml today -- see docs/RESEARCH_SERVER.md's 'Known gaps' section on per-strategy "
                f"parameter overrides."
            )

        backup_path = _backup_config(config_path)
        data["strategy_params"] = params
        config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    store = get_store()
    deployment_id = uuid.uuid4().hex[:12]
    store.insert_config_deployment(
        deployment_id=deployment_id, strategy=strategy, action="deploy", params=params, backup_path=backup_path,
    )
    return ConfigDeploymentOut(**store.fetch_config_deployment(deployment_id))


def rollback_config_deployment(deployment_id: str, config_path: Path = Path("config.yaml")) -> ConfigDeploymentOut:
    """Restores the exact config.yaml snapshot taken immediately before
    `deployment_id`'s change -- byte-for-byte, since the backup is a full
    file copy, not a re-serialized one. Also backs up whatever's being
    overwritten first, so rolling back a rollback is possible too."""
    store = get_store()
    deployment = store.fetch_config_deployment(deployment_id)
    if deployment is None:
        raise ApiError(f"No such config deployment: {deployment_id!r}")
    if not deployment["backup_path"]:
        raise ApiError(f"Deployment {deployment_id!r} has no backup to roll back to.")
    backup_path = Path(deployment["backup_path"])
    if not backup_path.exists():
        raise ApiError(f"Backup file {backup_path} is missing -- cannot roll back.")

    with _config_lock:
        pre_rollback_backup = _backup_config(config_path)
        config_path.write_bytes(backup_path.read_bytes())
        restored = yaml.safe_load(backup_path.read_text(encoding="utf-8")) or {}

    new_id = uuid.uuid4().hex[:12]
    store.insert_config_deployment(
        deployment_id=new_id, strategy=deployment["strategy"], action="rollback",
        params=restored.get("strategy_params", {}), backup_path=pre_rollback_backup,
    )
    return ConfigDeploymentOut(**store.fetch_config_deployment(new_id))


def list_config_deployments(strategy: Optional[str] = None) -> list[ConfigDeploymentOut]:
    return [ConfigDeploymentOut(**d) for d in get_store().fetch_config_deployments(strategy=strategy)]
