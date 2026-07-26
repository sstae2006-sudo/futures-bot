"""The incremental, resumable sync engine -- the part that actually talks
to Massive's aggregate-bars endpoint and decides what's still missing.

Three operations, one shared fetch primitive (`_fetch_aggs`, the same
pagination shape `fetch_mes_data.py` and `feeds/massive.py` already use
against this endpoint):

* `backfill` -- populate history over an explicit date range, using
  `contracts_client.front_month_schedule` to fetch each sub-window from
  the contract that was *actually* front-month on those dates, so a
  multi-quarter backfill never needs the by-hand contract stitching this
  session's own `data_MES_full.csv` required.
* `sync_incremental` -- the steady-state operation: detect today's active
  contract, notice if it rolled since last time, and fetch forward from
  wherever the store's coverage already ends.
* `verify` / `repair_gaps` -- scan stored bars for unexpected gaps (reusing
  `contracts.is_market_open` to avoid flagging ordinary weekend/holiday
  closures) and re-fetch specifically those windows.

Every operation logs a `sync_runs` row via `MarketDataStore` and
checkpoints `last_committed_through` as it goes, so a killed process
resumes from the checkpoint on the next call rather than the start --
that, plus `upsert_bars`'s `INSERT OR IGNORE`, is what "resumable" and
"never download duplicate data" mean concretely here.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import requests

from ..contracts import CME_TZ, is_market_open
from ..feeds.massive import resolution_minutes
from ..journal import LOGGER_NAME
from ..models import Bar
from .contracts_client import ContractsApiError, MassiveContractsClient
from .store import MarketDataStore

log = logging.getLogger(LOGGER_NAME)

AGGS_BASE_URL = "https://api.massive.com/futures/v1/aggs/{ticker}"


class SyncError(RuntimeError):
    pass


@dataclass
class SyncResult:
    run_id: str
    product_code: str
    resolution: str
    bars_fetched: int
    contracts_used: list[str] = field(default_factory=list)
    rolled: Optional[tuple[Optional[str], str]] = None  # (from, to), only for sync_incremental


@dataclass
class VerifyReport:
    product_code: str
    resolution: str
    bars_stored: int
    earliest: Optional[datetime]
    latest: Optional[datetime]
    new_gaps: list[tuple[datetime, datetime]]
    total_open_gaps: int


@dataclass
class RepairReport:
    product_code: str
    resolution: str
    gaps_attempted: int
    gaps_resolved: int
    bars_recovered: int


def _fetch_aggs(
    session: requests.Session, api_key: str, ticker: str, resolution: str, start: datetime, end: datetime,
) -> list[dict]:
    """Pages through `/futures/v1/aggs/{ticker}` for [start, end], same
    convention `fetch_mes_data.py`/`feeds/massive.py` already use (next_url
    when present, otherwise advance window_start.gte past the last bar)."""
    bars: list[dict] = []
    params = {
        "resolution": resolution,
        "window_start.gte": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "window_start.lte": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "limit": 50000,
        "sort": "window_start.asc",
        "apiKey": api_key,
    }
    url = AGGS_BASE_URL.format(ticker=ticker)

    while True:
        try:
            response = session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise SyncError(f"Massive aggs request failed for {ticker}: {exc}") from exc

        results = data.get("results", [])
        if not results:
            break
        bars.extend(results)

        next_url = data.get("next_url")
        if next_url:
            url, params = next_url, {"apiKey": api_key}
        elif len(results) < params.get("limit", 50000):
            break
        else:
            last_ns = results[-1]["window_start"]
            last_dt = datetime.fromtimestamp(last_ns / 1e9, tz=timezone.utc)
            new_start = last_dt.isoformat().replace("+00:00", "Z")
            if params.get("window_start.gte") == new_start:
                break
            params["window_start.gte"] = new_start
            url = AGGS_BASE_URL.format(ticker=ticker)

    return bars


def _parse_bar(raw: dict) -> Bar:
    ts = datetime.fromtimestamp(raw["window_start"] / 1e9, tz=timezone.utc)
    return Bar(
        timestamp=ts, open=Decimal(str(raw["open"])), high=Decimal(str(raw["high"])),
        low=Decimal(str(raw["low"])), close=Decimal(str(raw["close"])), volume=int(raw.get("volume", 0)),
    )


def _day_start_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _day_end_utc(d: date) -> datetime:
    return _day_start_utc(d) + timedelta(days=1) - timedelta(microseconds=1)


def backfill(
    store: MarketDataStore, api_key: str, product_code: str, resolution: str,
    start: datetime, end: datetime,
    session: Optional[requests.Session] = None, contracts_client: Optional[MassiveContractsClient] = None,
) -> SyncResult:
    """Populates history over [start, end], resolving which contract was
    actually front-month for each sub-window via
    `MassiveContractsClient.front_month_schedule` rather than requiring the
    caller to already know it (or to hand-stitch quarters the way this
    session's own `data_MES_full.csv` was built)."""
    session = session or requests.Session()
    contracts_client = contracts_client or MassiveContractsClient(api_key, session=session)

    run_id = uuid.uuid4().hex[:12]
    store.start_sync_run(run_id, product_code, resolution, "backfill", requested_start=start, requested_end=end)

    # Resume from a prior interrupted backfill's checkpoint, if any, rather
    # than re-requesting a window already fully committed.
    resume_from = store.last_committed_through(product_code, resolution)
    effective_start = max(start, resume_from + timedelta(seconds=1)) if resume_from else start

    try:
        schedule = contracts_client.front_month_schedule(product_code, effective_start.date(), end.date())
        if not schedule:
            store.complete_sync_run(run_id)
            return SyncResult(run_id=run_id, product_code=product_code, resolution=resolution, bars_fetched=0)

        total_fetched = 0
        contracts_used: list[str] = []
        for window_start_date, window_end_date, ticker in schedule:
            window_start = max(effective_start, _day_start_utc(window_start_date))
            window_end = min(end, _day_end_utc(window_end_date))
            if window_start > window_end:
                continue

            raw = _fetch_aggs(session, api_key, ticker, resolution, window_start, window_end)
            bars = sorted((_parse_bar(r) for r in raw), key=lambda b: b.timestamp)
            inserted = store.upsert_bars(product_code, ticker, resolution, "massive", bars)
            total_fetched += inserted
            contracts_used.append(ticker)
            store.checkpoint_sync_run(run_id, bars_fetched=total_fetched, last_committed_through=window_end)

        store.complete_sync_run(run_id)
        return SyncResult(
            run_id=run_id, product_code=product_code, resolution=resolution,
            bars_fetched=total_fetched, contracts_used=contracts_used,
        )
    except Exception as exc:  # noqa: BLE001 -- must reach fail_sync_run before propagating
        store.fail_sync_run(run_id, f"{type(exc).__name__}: {exc}")
        raise


def sync_incremental(
    store: MarketDataStore, api_key: str, product_code: str, resolution: str,
    now: Optional[datetime] = None,
    session: Optional[requests.Session] = None, contracts_client: Optional[MassiveContractsClient] = None,
) -> SyncResult:
    """The steady-state operation a scheduler calls repeatedly: detect
    today's front-month contract, notice a roll, and fetch forward from
    wherever the store's own coverage already ends -- never further back,
    so a healthy running system makes one small request per cycle, not a
    full re-scan."""
    now = now or datetime.now(timezone.utc)
    session = session or requests.Session()
    contracts_client = contracts_client or MassiveContractsClient(api_key, session=session)

    run_id = uuid.uuid4().hex[:12]
    store.start_sync_run(run_id, product_code, resolution, "incremental")

    try:
        active = contracts_client.active_contract(product_code, as_of=now.date())
        previous_ticker = store.get_active_contract(product_code)
        store.set_active_contract(product_code, active.ticker)
        rolled = (previous_ticker, active.ticker) if previous_ticker != active.ticker else None

        coverage = store.coverage(product_code, resolution)
        contract_start = _day_start_utc(active.first_trade_date)
        start = max(coverage.latest + timedelta(seconds=1), contract_start) if coverage.latest else contract_start

        if start > now:
            store.complete_sync_run(run_id)
            return SyncResult(run_id=run_id, product_code=product_code, resolution=resolution, bars_fetched=0, rolled=rolled)

        raw = _fetch_aggs(session, api_key, active.ticker, resolution, start, now)
        bars = sorted((_parse_bar(r) for r in raw), key=lambda b: b.timestamp)
        inserted = store.upsert_bars(product_code, active.ticker, resolution, "massive", bars)
        if bars:
            store.checkpoint_sync_run(run_id, bars_fetched=inserted, last_committed_through=bars[-1].timestamp)

        store.complete_sync_run(run_id)
        return SyncResult(
            run_id=run_id, product_code=product_code, resolution=resolution,
            bars_fetched=inserted, contracts_used=[active.ticker], rolled=rolled,
        )
    except Exception as exc:  # noqa: BLE001
        store.fail_sync_run(run_id, f"{type(exc).__name__}: {exc}")
        raise


def verify(store: MarketDataStore, product_code: str, resolution: str, max_gap_minutes: Optional[int] = None) -> VerifyReport:
    """Scans stored bars for gaps wider than expected during market hours
    (`contracts.is_market_open` distinguishes a real gap from an ordinary
    overnight/weekend/holiday closure -- the same reasoning
    `backtest/data.py::DataQualityReport` already applies to a CSV import,
    just against the DB's own coverage instead). Newly-found gaps are
    recorded via `store.record_gap` so `repair_gaps` has something to act
    on; already-known gaps aren't re-recorded."""
    step_minutes = max_gap_minutes or (resolution_minutes(resolution) * 3)
    bars = store.fetch_bars(product_code, resolution)
    coverage = store.coverage(product_code, resolution)

    known = {(g["gap_start"], g["gap_end"]) for g in store.fetch_gaps(product_code, unresolved_only=False)}
    new_gaps: list[tuple[datetime, datetime]] = []

    for prev, curr in zip(bars, bars[1:]):
        delta_minutes = (curr.timestamp - prev.timestamp).total_seconds() / 60
        if delta_minutes <= step_minutes:
            continue
        # Walk the gap in small steps -- strictly between prev and curr,
        # never touching curr.timestamp itself (which is, by definition,
        # the next bar the market actually produced, so it's never useful
        # evidence about whether the *gap* was a real closure) -- to decide
        # whether the market was closed for the whole span. A gap spanning
        # a weekend, holiday, or the daily maintenance halt is expected;
        # one occurring entirely during market hours is not.
        gap_minutes = int(delta_minutes)
        # `max(gap_minutes, step_minutes + 1)` guarantees at least one probe
        # point -- without it, a gap whose *truncated* minute count happens
        # to equal `step_minutes` (delta_minutes barely over the threshold,
        # e.g. step_minutes + 0.4) produced an empty range here, so `any()`
        # below was vacuously False and a real gap that exceeded the
        # threshold got silently treated as an expected closure instead of
        # being recorded.
        probe_offsets = range(step_minutes, max(gap_minutes, step_minutes + 1), max(step_minutes, 1))
        if not any(is_market_open(prev.timestamp + timedelta(minutes=m)) for m in probe_offsets):
            continue
        key = (prev.timestamp.isoformat(), curr.timestamp.isoformat())
        if key not in known:
            store.record_gap(product_code, resolution, prev.timestamp, curr.timestamp)
            new_gaps.append((prev.timestamp, curr.timestamp))

    return VerifyReport(
        product_code=product_code, resolution=resolution, bars_stored=coverage.count,
        earliest=coverage.earliest, latest=coverage.latest, new_gaps=new_gaps,
        total_open_gaps=len(store.fetch_gaps(product_code, unresolved_only=True)),
    )


def repair_gaps(
    store: MarketDataStore, api_key: str, product_code: str, resolution: str,
    session: Optional[requests.Session] = None, contracts_client: Optional[MassiveContractsClient] = None,
) -> RepairReport:
    """Re-fetches every unresolved recorded gap for a product+resolution,
    using the same front-month-schedule resolution `backfill` uses (a gap
    can straddle a rollover), and marks each one resolved once its window's
    bars are actually recovered. A gap that comes back empty again (a real
    market holiday, not a missed sync) is left unresolved rather than
    silently dropped -- see `verify`'s docstring on why that distinction
    matters."""
    session = session or requests.Session()
    contracts_client = contracts_client or MassiveContractsClient(api_key, session=session)

    gaps = store.fetch_gaps(product_code, unresolved_only=True)
    recovered_total = 0
    resolved_count = 0

    for gap in gaps:
        gap_start = datetime.fromisoformat(gap["gap_start"])
        gap_end = datetime.fromisoformat(gap["gap_end"])
        schedule = contracts_client.front_month_schedule(product_code, gap_start.date(), gap_end.date())

        recovered_this_gap = 0
        for window_start_date, window_end_date, ticker in schedule:
            window_start = max(gap_start, _day_start_utc(window_start_date))
            window_end = min(gap_end, _day_end_utc(window_end_date))
            if window_start > window_end:
                continue
            raw = _fetch_aggs(session, api_key, ticker, resolution, window_start, window_end)
            bars = [_parse_bar(r) for r in raw]
            recovered_this_gap += store.upsert_bars(product_code, ticker, resolution, "massive", bars)

        recovered_total += recovered_this_gap
        if recovered_this_gap > 0:
            store.resolve_gaps(product_code, resolution, gap_start, gap_end)
            resolved_count += 1

    return RepairReport(
        product_code=product_code, resolution=resolution,
        gaps_attempted=len(gaps), gaps_resolved=resolved_count, bars_recovered=recovered_total,
    )
