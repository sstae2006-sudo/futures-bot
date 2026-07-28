"""Phase 8A: the dashboard-facing surface over the market-data pipeline --
mirrors `live_session.py`'s split (a manager object wrapping background
work, a thin service module translating to/from API schemas) rather than
being folded into the already-large `services.py`, matching how
`api/analytics.py`/`api/regime.py`/`api/jobs.py` are each their own module
for one bounded concern.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from ..market_data import scheduler as scheduler_module
from ..market_data.store import default_db_path, get_market_data_store
from ..market_data.sync import backfill as md_backfill
from ..market_data.sync import repair_gaps as md_repair_gaps
from ..market_data.sync import sync_incremental as md_sync_incremental
from ..market_data.sync import verify as md_verify
from .schemas import (
    GapOut, MarketDataOverviewOut, ProductCoverageOut, SchedulerStatusOut, SyncRunOut,
)
from .services import ApiError


def _get_api_key() -> str:
    """Read fresh on every call (not cached) -- see `MarketDataScheduler`'s
    own docstring on why: a key set after the API process started should
    still work on the very next sync without a restart."""
    return os.environ.get("MASSIVE_API_KEY", "")


def _require_api_key() -> str:
    api_key = _get_api_key()
    if not api_key:
        raise ApiError(
            "MASSIVE_API_KEY environment variable is not set. The data pipeline's credential is read "
            "from the environment, never from config.yaml -- see docs/USER_MANUAL.md."
        )
    return api_key


def market_data_overview() -> MarketDataOverviewOut:
    store = get_market_data_store()
    try:
        products = []
        total_bars = 0
        total_open_gaps = 0
        for product_code in store.all_products():
            contracts = store.contracts_stored(product_code)
            # A product can have bars at more than one resolution; report
            # coverage for whichever is most complete (the primary series).
            resolutions = store.resolutions_stored(product_code)
            best = max((store.coverage(product_code, r) for r in resolutions), key=lambda c: c.count, default=None)
            open_gaps = len(store.fetch_gaps(product_code, unresolved_only=True))
            total_open_gaps += open_gaps
            if best is not None:
                total_bars += best.count
                products.append(ProductCoverageOut(
                    product_code=product_code, contracts_stored=contracts, bars_stored=best.count,
                    earliest=best.earliest.isoformat() if best.earliest else None,
                    latest=best.latest.isoformat() if best.latest else None,
                    open_gaps=open_gaps,
                ))

        recent_runs = store.fetch_sync_runs(limit=1)
        last_run = recent_runs[0] if recent_runs else None
        rolls = []
        for product_code in store.all_products():
            rolls.extend(store.contract_rolls(product_code))
        rolls.sort(key=lambda r: r["rolled_at"], reverse=True)

        scheduler_running = False
        try:
            scheduler_running = scheduler_module.get_scheduler().status()["running"]
        except RuntimeError:
            pass  # scheduler never started this process -- not running, no error

        return MarketDataOverviewOut(
            total_bars=total_bars, products=products, total_open_gaps=total_open_gaps,
            database_path=store.location, database_size_bytes=store.size_bytes,
            last_sync_at=last_run["started_at"] if last_run else None,
            last_sync_status=last_run["status"] if last_run else None,
            recent_rolls=rolls[:10], scheduler_running=scheduler_running,
        )
    finally:
        store.close()


def list_sync_runs(product_code: str | None = None, limit: int = 50) -> list[SyncRunOut]:
    store = get_market_data_store()
    try:
        return [SyncRunOut(**r) for r in store.fetch_sync_runs(product_code=product_code, limit=limit)]
    finally:
        store.close()


def list_gaps(product_code: str | None = None) -> list[GapOut]:
    store = get_market_data_store()
    try:
        return [GapOut(**g) for g in store.fetch_gaps(product_code=product_code, unresolved_only=True)]
    finally:
        store.close()


def run_sync(product_code: str, resolution: str) -> SyncRunOut:
    api_key = _require_api_key()
    store = get_market_data_store()
    try:
        try:
            result = md_sync_incremental(store, api_key, product_code, resolution)
        except Exception as exc:  # noqa: BLE001 -- surfaced as a client-facing 400, not a 500
            raise ApiError(f"Sync failed: {exc}") from exc
        runs = store.fetch_sync_runs(product_code=product_code, limit=1)
        return SyncRunOut(**runs[0])
    finally:
        store.close()


def run_backfill(product_code: str, resolution: str, start: str, end: str) -> SyncRunOut:
    api_key = _require_api_key()
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ApiError(f"Invalid date: {exc}") from exc

    store = get_market_data_store()
    try:
        try:
            result = md_backfill(store, api_key, product_code, resolution, start_dt, end_dt)
        except Exception as exc:  # noqa: BLE001
            raise ApiError(f"Backfill failed: {exc}") from exc
        runs = store.fetch_sync_runs(product_code=product_code, limit=1)
        return SyncRunOut(**runs[0])
    finally:
        store.close()


def run_verify(product_code: str, resolution: str) -> dict:
    store = get_market_data_store()
    try:
        report = md_verify(store, product_code, resolution)
        return {
            "product_code": report.product_code, "resolution": report.resolution,
            "bars_stored": report.bars_stored,
            "earliest": report.earliest.isoformat() if report.earliest else None,
            "latest": report.latest.isoformat() if report.latest else None,
            "new_gaps": len(report.new_gaps), "total_open_gaps": report.total_open_gaps,
        }
    finally:
        store.close()


def run_repair(product_code: str, resolution: str) -> dict:
    api_key = _require_api_key()
    store = get_market_data_store()
    try:
        try:
            report = md_repair_gaps(store, api_key, product_code, resolution)
        except Exception as exc:  # noqa: BLE001
            raise ApiError(f"Repair failed: {exc}") from exc
        return {
            "product_code": report.product_code, "resolution": report.resolution,
            "gaps_attempted": report.gaps_attempted, "gaps_resolved": report.gaps_resolved,
            "bars_recovered": report.bars_recovered,
        }
    finally:
        store.close()


# --- Scheduler control ---

def start_scheduler(targets: list[tuple[str, str]], interval_seconds: int) -> SchedulerStatusOut:
    scheduler = scheduler_module.get_scheduler(default_db_path(), _get_api_key)
    try:
        scheduler.start(
            [scheduler_module.SyncTarget(product_code=p, resolution=r) for p, r in targets],
            interval_seconds=interval_seconds,
        )
    except RuntimeError as exc:
        raise ApiError(str(exc)) from exc
    return SchedulerStatusOut(**scheduler.status())


def stop_scheduler() -> SchedulerStatusOut:
    scheduler = scheduler_module.get_scheduler(default_db_path(), _get_api_key)
    scheduler.stop()
    return SchedulerStatusOut(**scheduler.status())


def scheduler_status() -> SchedulerStatusOut:
    try:
        scheduler = scheduler_module.get_scheduler()
    except RuntimeError:
        return SchedulerStatusOut(running=False)
    return SchedulerStatusOut(**scheduler.status())
