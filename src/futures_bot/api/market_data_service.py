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
        # KNOWN_ISSUES.md ISSUE-042: this used to loop `store.all_products()`
        # and call contracts_stored()/resolutions_stored()/coverage()/
        # fetch_gaps() once EACH per product -- against this codebase's own
        # historical-archive data (ISSUE-001), product_code legitimately has
        # ~700 distinct values (one per contract ticker), so that loop made
        # thousands of sequential round trips and could take minutes against
        # a real Postgres/TimescaleDB server. product_coverage_summary()
        # computes the same per-product coverage via two GROUP BY queries
        # instead of per-product ones; fetch_gaps(None, ...)/contract_rolls(None)
        # already supported "all products in one call" (unused before this
        # fix) -- gaps are grouped by product_code here in Python rather
        # than re-querying per product.
        coverage_summary = store.product_coverage_summary()
        all_open_gaps = store.fetch_gaps(None, unresolved_only=True)
        open_gaps_by_product: dict[str, int] = {}
        for gap in all_open_gaps:
            open_gaps_by_product[gap["product_code"]] = open_gaps_by_product.get(gap["product_code"], 0) + 1

        products = []
        total_bars = 0
        for row in coverage_summary:
            open_gaps = open_gaps_by_product.get(row["product_code"], 0)
            total_bars += row["bars_stored"]
            products.append(ProductCoverageOut(
                product_code=row["product_code"], contracts_stored=row["contracts_stored"],
                bars_stored=row["bars_stored"],
                earliest=row["earliest"].isoformat() if row["earliest"] else None,
                latest=row["latest"].isoformat() if row["latest"] else None,
                open_gaps=open_gaps,
            ))
        total_open_gaps = len(all_open_gaps)

        recent_runs = store.fetch_sync_runs(limit=1)
        last_run = recent_runs[0] if recent_runs else None
        rolls = store.contract_rolls(None)
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
