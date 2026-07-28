"""The actual work behind every route: runs a backtest, an optimization, a
comparison, or a report, and persists the result. Kept independently
callable (and independently tested, see `tests/test_api_services.py`)
without going through HTTP, the same separation `backtest.runner` keeps
from `cli.py`.

Nothing here imports `brokers.tradovate` or `feeds.massive` -- this API is
historical-data-only, on purpose (see the package docstring).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional, Sequence

from .. import __version__
from ..backtest.data import is_db_dataset, load_bars, load_bars_from_db, parse_db_dataset
from ..backtest.html_report import generate_html_report
from ..backtest.metrics import BacktestMetrics
from ..backtest.runner import run_backtest, split_bars
from ..config import Settings, load_settings
from ..contracts import CME_TZ
from ..journal import LOGGER_NAME
from ..market_data.store import MarketDataStore, default_db_path
from ..models import Bar
from ..research.comparison import compare_strategies
from ..research.features import build_trade_records
from ..research.optimizer import run_optimization
from ..research.trade_store import TradeStore
from ..strategy.base import StrategyRegistry
from . import jobs
from .analytics import compute_excursions
from .introspection import all_strategy_schemas, strategy_param_schema
from ..research.regime import compute_regimes
from .schemas import (
    AiBacktestComparisonRequest, BacktestRunRequest, ClientProfileCreateRequest, ClientProfileOut,
    CompareEntryOut, CompareRequest, CompareResult, CorrelationRowOut, DatasetHealthOut, DatasetInfo,
    DeploymentOut, DeploymentStatusOut, EquityPoint, DrawdownPoint, ExperimentCreateRequest, ExperimentOut,
    FeatureDistributionOut, ImportHistoryOut, ImportUploadResponse, InsightOut, MlModelOut, ModelTrainRequest,
    OptimizerResultOut, OptimizerRunRequest, OverfitVerdict, PerformanceOut, PredictRequest, PredictionOut,
    RegimeBucket, RegimePerformanceOut, ReportOut, RunDetail, RunSummary, StrategyInfo, StrategyParamOut,
    SystemOverview, TradeAnalyticsSummary, TradeOut, TrialOut,
)
from .market_data_store import get_market_data_store
from .store import get_store

log = logging.getLogger(LOGGER_NAME)

#: Where generated HTML reports are written -- separate from `logs/`
#: (decision journal output) and `reports/` (compare_strategies.ps1's own
#: convention), to keep API-generated artifacts distinguishable from either.
REPORTS_DIR = Path("api_reports")

#: Registers every bundled strategy so `StrategyRegistry` has something to
#: return -- `strategy/__init__.py` imports each one (a side effect), same
#: single import `cli.py`/`research/optimizer.py` use (this module is an
#: alternative entry point into the same registry, not a different one).
from .. import strategy as _strategy  # noqa: F401,E402


class ApiError(ValueError):
    """Raised for a client-facing, well-understood failure (bad dataset
    name, unknown strategy, malformed date). Routes map this to HTTP 400 --
    see `routes/__init__.py`'s exception handler."""


# --- Datasets ---

#: Phase 8A: a "dataset" can also name a product+resolution stored in the
#: local market-data DB, e.g. "db:MES:5min" -- distinguished from a bare CSV
#: filename by the "db:" prefix (`backtest.data.is_db_dataset`/
#: `parse_db_dataset`, shared with the CLI so the naming convention lives
#: in exactly one place). Every existing caller (backtest, optimizer,
#: compare) already goes through `_load_request_bars`/`_resolve_dataset`,
#: so recognizing this one convention there is enough to make DB-backed
#: history a drop-in alternative to a CSV everywhere, with no changes
#: needed in `backtest/runner.py` or anything downstream of `list[Bar]`.
def _db_dataset_name(product_code: str, resolution: str) -> str:
    return f"db:{product_code}:{resolution}"


def list_datasets(root: Path = Path(".")) -> list[DatasetInfo]:
    out = []
    for path in sorted(root.glob("*.csv")):
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
                bars_hint = sum(1 for _ in fh) - 1
        except OSError:
            bars_hint = None
        out.append(DatasetInfo(filename=path.name, size_bytes=path.stat().st_size, bars_hint=max(bars_hint, 0) if bars_hint is not None else None))

    # Team deployment (FUTURES_BOT_DATABASE_URL set): "does the db exist"
    # isn't the right question for Postgres the way `db_path.exists()` is
    # for a local SQLite file -- a configured-but-unreachable database is
    # handled gracefully (empty list, not a crash), matching this
    # project's "fail gracefully if the database is unavailable"
    # requirement, rather than surfacing a 500 from what's meant to be a
    # convenience listing.
    from ..db.engine import database_url
    if database_url():
        try:
            store = get_market_data_store()
            try:
                for product_code in store.all_products():
                    for resolution in store.resolutions_stored(product_code):
                        coverage = store.coverage(product_code, resolution)
                        out.append(DatasetInfo(
                            filename=_db_dataset_name(product_code, resolution),
                            size_bytes=store.size_bytes, bars_hint=coverage.count,
                        ))
            finally:
                store.close()
        except Exception as exc:  # noqa: BLE001 -- see comment above: never let a DB outage break this listing.
            log.warning("Could not list market-data datasets from TimescaleDB: %s", exc)
        return out

    db_path = default_db_path()
    if db_path.exists():
        store = MarketDataStore(db_path)
        try:
            for product_code in store.all_products():
                for resolution in store.resolutions_stored(product_code):
                    coverage = store.coverage(product_code, resolution)
                    out.append(DatasetInfo(
                        filename=_db_dataset_name(product_code, resolution),
                        size_bytes=db_path.stat().st_size, bars_hint=coverage.count,
                    ))
        finally:
            store.close()
    return out


def _resolve_dataset(dataset: str, root: Path = Path(".")) -> Path:
    """Only ever resolves to a bare filename directly under `root` -- never
    a path with directory components -- so a request can't read an
    arbitrary file on the host via `../../etc/passwd`-style input. This
    matters more here than it would purely internally: `dataset` is
    attacker-reachable request input the moment this API is exposed on any
    network, even a local one."""
    if dataset != Path(dataset).name:
        raise ApiError(f"Invalid dataset name {dataset!r}: must be a bare filename, no path segments.")
    path = root / dataset
    if not path.is_file():
        raise ApiError(f"No such dataset: {dataset!r}. See GET /api/datasets for what's available.")
    return path


def _parse_date_bound(value: Optional[str], *, end_of_day: bool) -> Optional[datetime]:
    if value is None:
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        raise ApiError(f"Could not parse date {value!r}; expected YYYY-MM-DD or full ISO 8601.") from None
    if d.tzinfo is None:
        d = d.replace(tzinfo=CME_TZ)
    if end_of_day and len(value) <= 10:  # a bare date, not a full timestamp
        d = d.replace(hour=23, minute=59, second=59)
    return d


# --- Strategies ---

def list_strategies() -> list[StrategyInfo]:
    return [
        StrategyInfo(
            name=name,
            parameters=[
                StrategyParamOut(name=p.name, type=p.type, default=p.default, description=p.description)
                for p in schema
            ],
        )
        for name, schema in all_strategy_schemas().items()
    ]


def get_strategy(name: str) -> StrategyInfo:
    if name not in StrategyRegistry.names():
        raise ApiError(f"Unknown strategy {name!r}. Registered: {', '.join(StrategyRegistry.names())}")
    schema = strategy_param_schema(name)
    return StrategyInfo(
        name=name,
        parameters=[
            StrategyParamOut(name=p.name, type=p.type, default=p.default, description=p.description)
            for p in schema
        ],
    )


# --- Settings assembly ---

def _settings_for_request(req: BacktestRunRequest, config_path: Path = Path("config.yaml")) -> Settings:
    """Layers the request's overrides onto `config.yaml`'s base settings,
    then re-validates the whole thing through `Settings.model_validate` --
    not `model_copy(update=...)` alone, which skips field/model validators
    entirely. That matters here specifically: an override bad enough to
    trip `_known_contract` or `_live_mode_guard` needs to surface as a
    clear 400, not silently produce an invalid `Settings` object a backtest
    then runs against.
    """
    settings = load_settings(config_path)

    risk_overrides = {
        k: v for k, v in {
            "stop_loss_points": req.stop_loss_points,
            "take_profit_points": req.take_profit_points,
            "contracts_per_trade": req.contracts_per_trade,
            "daily_max_loss": req.daily_max_loss,
            "max_trades_per_session": req.max_trades_per_session,
        }.items() if v is not None
    }
    risk = settings.risk.model_copy(update=risk_overrides) if risk_overrides else settings.risk

    broker = settings.broker
    if req.starting_cash is not None:
        broker = broker.model_copy(update={"starting_cash": req.starting_cash})

    try:
        return Settings.model_validate({
            **settings.model_dump(),
            "contract": req.contract,
            "strategy_name": req.strategy_name,
            "strategy_params": req.strategy_params,
            "risk": risk.model_dump(),
            "broker": broker.model_dump(),
        })
    except Exception as exc:  # noqa: BLE001 -- surfaced as a client error, not a 500
        raise ApiError(f"Invalid backtest configuration: {exc}") from exc


def _build_strategy(settings: Settings):
    try:
        strategy_cls = StrategyRegistry.get(settings.strategy_name)
    except KeyError as exc:
        raise ApiError(str(exc)) from exc
    try:
        return strategy_cls(settings.contract_spec, **settings.strategy_params)
    except (TypeError, ValueError) as exc:
        raise ApiError(f"Invalid strategy_params for {settings.strategy_name!r}: {exc}") from exc


def _load_dataset(
    dataset: str, start: Optional[datetime] = None, end: Optional[datetime] = None,
) -> tuple[list[Bar], object]:
    """The one place that decides whether `dataset` names a CSV file or a
    Phase 8A `db:PRODUCT:RESOLUTION` pseudo-dataset -- every caller that
    loads bars from a request-supplied dataset string (backtests, the
    optimizer, report regeneration) goes through this, so "support DB
    datasets" only ever has to be implemented once. Missing this on
    `run_optimizer_job`/`generate_report` (each resolving `_resolve_dataset`
    directly instead) was a real bug caught by
    `tests/test_research_server_nightly_jobs.py`'s happy-path test -- a
    nightly optimizer/report job against `db:MES:5min` failed with "No such
    dataset" even though the exact same string worked for a backtest."""
    if is_db_dataset(dataset):
        try:
            product_code, resolution = parse_db_dataset(dataset)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
        return load_bars_from_db(product_code, resolution, start=start, end=end)
    csv_path = _resolve_dataset(dataset)
    return load_bars(csv_path, start=start, end=end)


def _load_request_bars(req: BacktestRunRequest) -> list[Bar]:
    start = _parse_date_bound(req.start, end_of_day=False)
    end = _parse_date_bound(req.end, end_of_day=True)
    bars, _ = _load_dataset(req.dataset, start=start, end=end)
    if not bars:
        raise ApiError(f"No bars loaded from {req.dataset!r} for the requested range.")
    return bars


# --- Backtests ---

def run_backtest_job(
    req: BacktestRunRequest, config_path: Path = Path("config.yaml"),
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> RunDetail:
    """``progress_callback``, if given, receives ``(bars_processed, total_bars)``
    -- threaded straight through to `backtest.runner.run_backtest`. Only
    the main slice reports progress for a walk-forward run (the training
    backtest, which dominates the runtime at the default 70/30 split); the
    validation backtest runs to completion without incremental updates,
    same as the synchronous Phase 6A behavior otherwise."""
    settings = _settings_for_request(req, config_path)
    bars = _load_request_bars(req)
    signal_filter = _load_ml_signal_filter(req.ml_model_id, req.ml_min_win_probability) if req.ml_model_id else None

    store = get_store()
    run_id = uuid.uuid4().hex[:12]
    store.insert_run(
        run_id=run_id, kind="walk_forward" if req.walk_forward else "backtest", status="running",
        strategy=settings.strategy_name, contract=settings.contract,
        strategy_params=settings.strategy_params, csv_path=str(req.dataset), walk_forward=req.walk_forward,
    )

    try:
        if req.walk_forward:
            train, test = split_bars(bars)
            train_metrics, train_journal = _run_with_journal(settings, train, progress_callback, signal_filter)
            test_metrics, _ = _run_with_journal(settings, test, signal_filter=signal_filter)
            _persist_completed_run(store, run_id, settings, train_metrics, train_journal, train, validation=test_metrics)
        else:
            metrics, journal = _run_with_journal(settings, bars, progress_callback, signal_filter)
            _persist_completed_run(store, run_id, settings, metrics, journal, bars)
    except Exception as exc:  # noqa: BLE001 -- record the failure, then re-raise for the route to map to a 4xx/5xx
        store.fail_run(run_id, str(exc))
        raise

    return get_run(run_id)


def _run_with_journal(
    settings: Settings, bars: list[Bar], progress_callback: Optional[Callable[[int, int], None]] = None,
    signal_filter: Optional[Callable] = None,
):
    """Runs a backtest with a `CountingJournal` wired in so the entry
    reason/indicator-snapshot metadata behind each trade (RSI, ADX, ATR,
    VWAP, ...) is captured, not just the exit-side `Trade` record -- the
    Trade Explorer's "Market Context" panel needs this, and it's only
    available by capturing entries as the backtest runs, not after the fact.

    ``signal_filter``, if given (Phase 9: `req.ml_model_id` set), is threaded
    straight into `run_backtest` -- `None` by default, unaffecting every
    existing caller.
    """
    from ..backtest.runner import CountingJournal

    journal = CountingJournal(settings.logging.directory, settings.logging.log_every_decision)
    metrics = run_backtest(
        settings, _build_strategy(settings), bars, journal=journal,
        progress_callback=progress_callback, signal_filter=signal_filter,
    )
    return metrics, journal


def _persist_completed_run(
    store: TradeStore, run_id: str, settings: Settings, metrics: BacktestMetrics, journal, bars: Sequence[Bar],
    validation: Optional[BacktestMetrics] = None,
) -> None:
    store.complete_run(
        run_id,
        starting_equity=metrics.starting_equity, trade_count=metrics.trade_count, net_pnl=metrics.net_pnl,
        profit_factor=metrics.profit_factor, win_rate=metrics.win_rate, expectancy=metrics.expectancy,
        sharpe_ratio=metrics.sharpe_ratio, sortino_ratio=metrics.sortino_ratio,
        max_drawdown=metrics.max_drawdown, max_drawdown_pct=metrics.max_drawdown_pct,
        caveats=metrics.caveats(), first_bar=metrics.first_bar, last_bar=metrics.last_bar,
        validation_trade_count=validation.trade_count if validation else None,
        validation_net_pnl=validation.net_pnl if validation else None,
        validation_profit_factor=validation.profit_factor if validation else None,
    )

    # Trades are stored too (denormalized, see trade_store.py) so the Trade
    # Explorer can filter/inspect individual trades from this run, joined
    # against the journal's captured entries -- same positional join
    # `build_trade_records` already documents and `research/features.py`
    # relies on elsewhere. `bars` is the exact slice this metrics run
    # replayed (the train slice for walk-forward, the full range otherwise)
    # -- MAE/MFE and regime labels are computed from it directly (see
    # api/analytics.py and api/regime.py), no second backtest needed.
    if metrics.trades:
        excursions = compute_excursions(metrics.trades, bars)
        regimes = compute_regimes(metrics.trades, bars)
        records = build_trade_records(
            metrics.trades, journal.entries,
            run_id=run_id, contract=settings.contract, strategy=settings.strategy_name,
            strategy_params=settings.strategy_params,
            excursions=excursions, regimes=regimes,
        )
        store.insert_trades(records)


def _run_to_summary(run: dict) -> RunSummary:
    return RunSummary(
        id=run["id"], kind=run["kind"], status=run["status"], strategy=run["strategy"], contract=run["contract"],
        trade_count=run["trade_count"], net_pnl=run["net_pnl"], profit_factor=run["profit_factor"],
        win_rate=run["win_rate"], sharpe_ratio=run["sharpe_ratio"], max_drawdown=run["max_drawdown"],
        validation_net_pnl=run["validation_net_pnl"], walk_forward=run["walk_forward"],
        created_at=run["created_at"], completed_at=run["completed_at"],
    )


def list_runs(strategy: Optional[str] = None, kind: Optional[str] = None, limit: int = 100) -> list[RunSummary]:
    store = get_store()
    return [_run_to_summary(r) for r in store.fetch_runs(strategy=strategy, kind=kind, limit=limit)]


def list_backtest_runs(strategy: Optional[str] = None, limit: int = 100) -> list[RunSummary]:
    """`/api/backtests`'s listing: backtest and walk-forward runs only --
    optimizer batches have their own `/api/optimizer/results` and aren't
    "a backtest" from a dashboard user's point of view even though they
    share the same `runs` table underneath."""
    store = get_store()
    rows = store.fetch_runs(strategy=strategy, limit=limit * 2)  # over-fetch to survive the kind filter below
    rows = [r for r in rows if r["kind"] in ("backtest", "walk_forward")][:limit]
    return [_run_to_summary(r) for r in rows]


def get_run(run_id: str) -> RunDetail:
    store = get_store()
    run = store.fetch_run(run_id)
    if run is None:
        raise ApiError(f"No such run: {run_id!r}")
    return RunDetail(
        **_run_to_summary(run).model_dump(),
        strategy_params=run["strategy_params"], csv_path=run["csv_path"], expectancy=run["expectancy"],
        sortino_ratio=run["sortino_ratio"], max_drawdown_pct=run["max_drawdown_pct"],
        validation_trade_count=run["validation_trade_count"], validation_profit_factor=run["validation_profit_factor"],
        caveats=run["caveats"], first_bar=run["first_bar"], last_bar=run["last_bar"],
        error_message=run["error_message"],
    )


# --- Background jobs (Phase 6B) ---
#
# Thin wrappers: each submits the *exact same* synchronous function this
# module already exposes (`run_backtest_job`, `run_optimizer_job`,
# `run_compare`, `generate_report`) to `jobs.submit`, with a progress
# callback wired in where the underlying function supports one. The
# synchronous versions above are completely unchanged and still work
# exactly as they did in Phase 6A -- these functions are additive, reached
# only through the new `/api/jobs/*` routes.

def submit_backtest_job(req: BacktestRunRequest, config_path: Path = Path("config.yaml")) -> str:
    def work(progress: "jobs.JobProgress"):
        def on_progress(current: int, total: int) -> None:
            progress.update(current, total, message=f"{current}/{total} bars processed")

        result = run_backtest_job(req, config_path, progress_callback=on_progress)
        return result.id, None

    kind = "walk_forward" if req.walk_forward else "backtest"
    return jobs.submit(kind, req.model_dump(mode="json"), work)


def _format_eta(elapsed_seconds: float, done: int, total: int) -> str:
    if done == 0:
        return "unknown"
    remaining = (elapsed_seconds / done) * (total - done)
    if remaining < 60:
        return f"{remaining:.0f}s"
    if remaining < 3600:
        return f"{remaining / 60:.1f}m"
    return f"{remaining / 3600:.1f}h"


def submit_optimizer_job(req: OptimizerRunRequest, config_path: Path = Path("config.yaml")) -> str:
    def work(progress: "jobs.JobProgress"):
        start_time = time.monotonic()

        def on_progress(done: int, total: int, best: Optional[dict]) -> None:
            # Percent/ETA aren't part of the callback payload itself (see
            # `run_optimization`'s docstring) -- both are trivial to derive
            # here from (done, total) plus this closure's own start time,
            # which keeps that lower-level function from needing to know
            # anything about wall-clock formatting.
            elapsed = time.monotonic() - start_time
            percent = (done / total * 100) if total else 100.0
            eta = _format_eta(elapsed, done, total)
            message = f"{percent:.1f}% ({done}/{total}), ETA {eta}"
            if best is not None:
                params_str = ", ".join(f"{k}={v}" for k, v in best["params"].items())
                message += f" -- best so far: {params_str} (score {best['score']:.2f})"
            progress.update(done, total, message=message)

        result = run_optimizer_job(req, config_path, progress_callback=on_progress)
        return result.batch_id, None

    return jobs.submit("optimizer", req.model_dump(mode="json"), work)


def submit_compare_job(req: CompareRequest, config_path: Path = Path("config.yaml")) -> str:
    def work(progress: "jobs.JobProgress"):
        progress.update(0, 1, message="Running comparison...")
        result = run_compare(req, config_path)
        progress.update(1, 1, message="Done")
        return None, result.model_dump(mode="python")

    return jobs.submit("compare", req.model_dump(mode="json"), work)


def submit_report_job(run_id: str) -> str:
    def work(progress: "jobs.JobProgress"):
        progress.update(0, 1, message=f"Generating report for run {run_id}...")
        report = generate_report(run_id)
        progress.update(1, 1, message="Done")
        return report.id, None

    return jobs.submit("report", {"run_id": run_id}, work)


def get_job(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        raise ApiError(f"No such job: {job_id!r}")
    return job


def list_jobs(status: Optional[str] = None, limit: int = 100) -> list[dict]:
    return jobs.list_jobs(status=status, limit=limit)


# --- Trades ---

def list_trades(
    run_id: Optional[str] = None, strategy: Optional[str] = None,
    side: Optional[str] = None, outcome: Optional[str] = None,
) -> list[TradeOut]:
    store = get_store()
    rows = store.fetch_trades(run_id=run_id, strategy=strategy, side=side, outcome=outcome)
    return [TradeOut(**row) for row in rows]


# --- Advanced trade analytics (Phase 6B) ---

#: A trade counts as a "missed opportunity" candidate only once the
#: available favorable move was at least this many points -- otherwise
#: every small losing trade with a 0.1-point MFE would qualify, which
#: isn't what "missed a real opportunity" means.
_MISSED_OPPORTUNITY_MIN_MFE = Decimal("2")


def trade_analytics_summary(
    run_id: Optional[str] = None, strategy: Optional[str] = None, top_n: int = 10,
) -> TradeAnalyticsSummary:
    """Three views over the same trade set, all keyed off `efficiency`
    (realized-favorable-points / MFE-points, computed in `research.features
    .build_trade_records`) and MFE:

    * **Best entries** -- highest efficiency: exits that captured most of
      the favorable move the trade ever saw. A strong entry alone doesn't
      show up here if the exit gave most of it back.
    * **Poor exits** -- lowest efficiency among trades that *had* a real
      favorable move available (MFE > 0): the entry was fine, the exit left
      money on the table.
    * **Missed opportunities** -- losing (or scratch) trades where a
      meaningful favorable move (>= `_MISSED_OPPORTUNITY_MIN_MFE` points)
      was available before it reversed -- the setup wasn't wrong, the
      management was.

    Trades with no computed efficiency (older data from before Phase 6B, or
    a trade whose bar window couldn't be found) are excluded from the first
    two buckets rather than sorted arbitrarily; the third only needs MFE,
    which is independent of efficiency.
    """
    store = get_store()
    rows = store.fetch_trades(run_id=run_id, strategy=strategy)

    with_efficiency = [r for r in rows if r["efficiency"] is not None]
    best_entries = sorted(with_efficiency, key=lambda r: r["efficiency"], reverse=True)[:top_n]

    had_favorable_move = [r for r in with_efficiency if r["mfe_points"] and r["mfe_points"] > 0]
    poor_exits = sorted(had_favorable_move, key=lambda r: r["efficiency"])[:top_n]

    missed = [
        r for r in rows
        if r["net_pnl"] <= 0 and r["mfe_points"] is not None and r["mfe_points"] >= _MISSED_OPPORTUNITY_MIN_MFE
    ]
    missed_opportunities = sorted(missed, key=lambda r: r["mfe_points"], reverse=True)[:top_n]

    return TradeAnalyticsSummary(
        best_entries=[TradeOut(**r) for r in best_entries],
        poor_exits=[TradeOut(**r) for r in poor_exits],
        missed_opportunities=[TradeOut(**r) for r in missed_opportunities],
    )


# --- Market regime performance (Phase 6B) ---

def _regime_buckets(rows: list[dict], field: str) -> list[RegimeBucket]:
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        value = r.get(field)
        if value is None:
            continue
        grouped.setdefault(value, []).append(r)

    buckets = []
    for value, group in grouped.items():
        wins = [r for r in group if r["net_pnl"] > 0]
        efficiencies = [r["efficiency"] for r in group if r["efficiency"] is not None]
        buckets.append(RegimeBucket(
            value=value,
            trade_count=len(group),
            net_pnl=sum((r["net_pnl"] for r in group), Decimal("0")),
            win_rate=(Decimal(len(wins)) / Decimal(len(group)) * 100) if group else None,
            average_efficiency=(sum(efficiencies) / Decimal(len(efficiencies))) if efficiencies else None,
        ))
    buckets.sort(key=lambda b: b.net_pnl, reverse=True)
    return buckets


def regime_performance(strategy: Optional[str] = None) -> RegimePerformanceOut:
    """Groups every stored trade (optionally filtered to one strategy) by
    its entry-time regime labels (see `api/regime.py`) and reports net P&L/
    win rate/efficiency per bucket -- "when does this strategy actually
    work?" made queryable instead of read off a heatmap by eye.
    """
    store = get_store()
    rows = store.fetch_trades(strategy=strategy)
    return RegimePerformanceOut(
        strategy=strategy,
        trend=_regime_buckets(rows, "regime_trend"),
        volatility=_regime_buckets(rows, "regime_volatility"),
        session=_regime_buckets(rows, "regime_session"),
    )


# --- Performance ---

def get_performance(run_id: str) -> PerformanceOut:
    store = get_store()
    run = store.fetch_run(run_id)
    if run is None:
        raise ApiError(f"No such run: {run_id!r}")
    rows = store.fetch_trades(run_id=run_id)

    equity_curve: list[EquityPoint] = [EquityPoint(trade_number=0, timestamp=run["first_bar"], equity=run["starting_equity"] or Decimal("0"))]
    running = run["starting_equity"] or Decimal("0")
    for i, row in enumerate(rows, start=1):
        running += row["net_pnl"]
        equity_curve.append(EquityPoint(trade_number=i, timestamp=row["exit_time"], equity=running))

    drawdown_curve: list[DrawdownPoint] = []
    peak = equity_curve[0].equity if equity_curve else Decimal("0")
    longest_dd = current_dd_len = 0
    for i, point in enumerate(equity_curve):
        peak = max(peak, point.equity)
        dd = point.equity - peak
        drawdown_curve.append(DrawdownPoint(trade_number=point.trade_number, timestamp=point.timestamp, drawdown=dd))
        if dd < 0:
            current_dd_len += 1
            longest_dd = max(longest_dd, current_dd_len)
        else:
            current_dd_len = 0

    wins = [r for r in rows if r["net_pnl"] > 0]
    losses = [r for r in rows if r["net_pnl"] < 0]
    avg_win = sum((r["net_pnl"] for r in wins), Decimal("0")) / len(wins) if wins else None
    avg_loss = sum((r["net_pnl"] for r in losses), Decimal("0")) / len(losses) if losses else None

    worst_streak = streak = 0
    for r in rows:
        streak = streak + 1 if r["net_pnl"] < 0 else 0
        worst_streak = max(worst_streak, streak)
    best_streak = streak = 0
    for r in rows:
        streak = streak + 1 if r["net_pnl"] > 0 else 0
        best_streak = max(best_streak, streak)

    avg_holding = sum(r["holding_minutes"] for r in rows) / len(rows) if rows else None

    return PerformanceOut(
        run_id=run_id, equity_curve=equity_curve, drawdown_curve=drawdown_curve,
        max_drawdown=run["max_drawdown"] or Decimal("0"), max_drawdown_pct=run["max_drawdown_pct"],
        longest_drawdown_trades=longest_dd, max_consecutive_losses=worst_streak, max_consecutive_wins=best_streak,
        wins=len(wins), losses=len(losses), average_win=avg_win, average_loss=avg_loss,
        expectancy=run["expectancy"], average_r_multiple=None, average_holding_minutes=avg_holding,
    )


# --- Compare ---

def run_compare(req: CompareRequest, config_path: Path = Path("config.yaml")) -> CompareResult:
    settings = load_settings(config_path)
    settings = settings.model_copy(update={"contract": req.contract})
    start = _parse_date_bound(req.start, end_of_day=False)
    end = _parse_date_bound(req.end, end_of_day=True)
    bars, _ = _load_dataset(req.dataset, start=start, end=end)
    if not bars:
        raise ApiError(f"No bars loaded from {req.dataset!r} for the requested range.")

    names = req.strategy_names or StrategyRegistry.names()
    unknown = [n for n in names if n not in StrategyRegistry.names()]
    if unknown:
        raise ApiError(f"Unknown strategy/strategies: {', '.join(unknown)}")

    entries = compare_strategies(settings, bars, [(name, {}) for name in names])
    out = []
    for entry in entries:
        m = entry.metrics
        curve = [EquityPoint(trade_number=0, timestamp=None, equity=m.starting_equity)]
        running = m.starting_equity
        for i, t in enumerate(m.trades, start=1):
            running += t.net_pnl
            curve.append(EquityPoint(trade_number=i, timestamp=t.exit_time.isoformat(), equity=running))
        out.append(CompareEntryOut(
            strategy=entry.strategy, net_pnl=m.net_pnl, profit_factor=m.profit_factor,
            win_rate=m.win_rate, sharpe_ratio=m.sharpe_ratio, max_drawdown=m.max_drawdown,
            trade_count=m.trade_count, equity_curve=curve,
        ))
    return CompareResult(entries=out)


# --- Optimizer ---

def run_optimizer_job(
    req: OptimizerRunRequest, config_path: Path = Path("config.yaml"),
    progress_callback: Optional[Callable[[int, int, Optional[dict]], None]] = None,
) -> OptimizerResultOut:
    """``progress_callback``, if given, receives ``(combos_done, combos_total,
    current_best)`` -- threaded straight through to
    `research.optimizer.run_optimization`."""
    settings = load_settings(config_path)
    settings = settings.model_copy(update={"contract": req.contract, "strategy_name": req.strategy_name})
    start = _parse_date_bound(req.start, end_of_day=False)
    end = _parse_date_bound(req.end, end_of_day=True)
    bars, _ = _load_dataset(req.dataset, start=start, end=end)
    if not bars:
        raise ApiError(f"No bars loaded from {req.dataset!r} for the requested range.")
    if req.strategy_name not in StrategyRegistry.names():
        raise ApiError(f"Unknown strategy {req.strategy_name!r}.")

    store = get_store()
    run_id = uuid.uuid4().hex[:12]
    store.insert_run(
        run_id=run_id, kind="optimizer", status="running", strategy=req.strategy_name,
        contract=req.contract, strategy_params=req.param_grid, csv_path=str(req.dataset),
    )

    try:
        result = run_optimization(
            settings, req.strategy_name, req.param_grid, bars,
            top_n=req.top_n, rolling=req.rolling, store=store, batch_id=run_id,
            progress_callback=progress_callback,
        )
    except Exception as exc:  # noqa: BLE001
        store.fail_run(run_id, str(exc))
        raise ApiError(str(exc)) from exc

    best = result.best
    if best is not None:
        store.complete_run(
            run_id, starting_equity=settings.broker.starting_cash, trade_count=best.train_metrics.trade_count,
            net_pnl=best.train_metrics.net_pnl, profit_factor=best.train_metrics.profit_factor,
            win_rate=best.train_metrics.win_rate, expectancy=best.train_metrics.expectancy,
            sharpe_ratio=best.train_metrics.sharpe_ratio, sortino_ratio=best.train_metrics.sortino_ratio,
            max_drawdown=best.train_metrics.max_drawdown, max_drawdown_pct=best.train_metrics.max_drawdown_pct,
            caveats=best.train_metrics.caveats(),
            validation_trade_count=best.validation_metrics.trade_count if best.validation_metrics else None,
            validation_net_pnl=best.validation_metrics.net_pnl if best.validation_metrics else None,
            validation_profit_factor=best.validation_metrics.profit_factor if best.validation_metrics else None,
        )
    else:
        store.fail_run(run_id, "No parameter combination completed a training backtest.")

    return OptimizerResultOut(
        batch_id=result.batch_id, strategy=result.strategy, combos_tried=result.combos_tried,
        ranked_trials=[
            TrialOut(
                rank=t.rank, params=t.params, train_trades=t.train_metrics.trade_count,
                train_net_pnl=t.train_metrics.net_pnl, train_profit_factor=t.train_metrics.profit_factor,
                train_max_drawdown=t.train_metrics.max_drawdown,
                validation_trades=t.validation_metrics.trade_count if t.validation_metrics else None,
                validation_net_pnl=t.validation_metrics.net_pnl if t.validation_metrics else None,
                validation_profit_factor=t.validation_metrics.profit_factor if t.validation_metrics else None,
                validation_max_drawdown=t.validation_metrics.max_drawdown if t.validation_metrics else None,
            )
            for t in result.ranked_trials
        ],
        confidence=result.safety.confidence if result.safety else None,
        warnings=result.safety.reasons if result.safety else [],
    )


def get_optimizer_trials(batch_id: str) -> list[TrialOut]:
    store = get_store()
    trials = store.fetch_optimization_trials(batch_id)
    return [
        TrialOut(
            rank=t["rank"], params=t["params"], train_trades=t["train_trades"],
            train_net_pnl=t["train_net_pnl"], train_profit_factor=t["train_profit_factor"],
            train_max_drawdown=t["train_max_drawdown"], validation_trades=t["validation_trades"],
            validation_net_pnl=t["validation_net_pnl"], validation_profit_factor=t["validation_profit_factor"],
            validation_max_drawdown=t["validation_max_drawdown"],
        )
        for t in trials
    ]


# --- Walk-forward / overfitting verdict ---

def overfit_verdict(run: RunDetail) -> OverfitVerdict:
    """A traffic-light summary of `research.safety`'s checks for one run,
    specifically for the Walk-Forward Analysis page. Thresholds mirror
    `research/safety.py`'s own (``DEGRADATION_WARNING_PCT``/``_SEVERE_PCT``,
    `MIN_VALIDATION_TRADES`) rather than inventing new ones -- this is a
    presentation layer over those checks, not a second opinion.
    """
    reasons: list[str] = []
    level = "green"

    if run.validation_trade_count is None:
        return OverfitVerdict(level="yellow", label="Not validated", reasons=["No out-of-sample validation was run for this result."])

    if run.validation_trade_count < 20:
        reasons.append(f"Only {run.validation_trade_count} validation trades -- close to noise.")
        level = "yellow"

    if run.net_pnl is not None and run.validation_net_pnl is not None:
        if run.net_pnl > 0 and run.validation_net_pnl <= 0:
            reasons.append("Profitable in training but not out-of-sample.")
            level = "red"
        elif run.expectancy and run.expectancy > 0:
            pass  # degradation-by-expectancy needs both expectancies; trade_count-based checks above cover the common case here

    if run.max_drawdown_pct is not None and run.max_drawdown_pct > 30:
        reasons.append(f"Max drawdown {run.max_drawdown_pct:.0f}% of starting equity.")
        if level == "green":
            level = "yellow"

    if not reasons:
        reasons.append("No automated warnings fired. This does not prove the result is correct.")

    label = {"green": "Validated", "yellow": "Questionable", "red": "Likely overfit"}[level]
    return OverfitVerdict(level=level, label=label, reasons=reasons)


# --- Reports ---

def generate_report(run_id: str) -> ReportOut:
    store = get_store()
    run = store.fetch_run(run_id)
    if run is None:
        raise ApiError(f"No such run: {run_id!r}")
    if run["csv_path"] is None:
        raise ApiError(f"Run {run_id!r} has no associated dataset to rebuild a report from.")

    settings = load_settings(Path("config.yaml")).model_copy(
        update={"strategy_name": run["strategy"], "strategy_params": run["strategy_params"], "contract": run["contract"]}
    )
    bars, data_report = _load_dataset(run["csv_path"])
    strategy_cls = StrategyRegistry.get(run["strategy"])
    metrics = run_backtest(settings, strategy_cls(settings.contract_spec, **settings.strategy_params), bars)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_id = uuid.uuid4().hex[:12]
    html = generate_html_report(metrics, settings, data_report=data_report, title=f"{run['strategy']} — {run_id}")
    path = REPORTS_DIR / f"{report_id}.html"
    path.write_text(html, encoding="utf-8")

    store.insert_report(report_id=report_id, run_id=run_id, format="html", path=str(path))
    return ReportOut(id=report_id, run_id=run_id, format="html", path=str(path), created_at=datetime.now(timezone.utc).isoformat())


def list_reports(run_id: Optional[str] = None) -> list[ReportOut]:
    store = get_store()
    return [ReportOut(**r) for r in store.fetch_reports(run_id=run_id)]


def read_report_html(report_id: str) -> str:
    store = get_store()
    reports = store.fetch_reports()
    match = next((r for r in reports if r["id"] == report_id), None)
    if match is None:
        raise ApiError(f"No such report: {report_id!r}")
    return Path(match["path"]).read_text(encoding="utf-8")


# --- ML dataset info ---

def ml_dataset_info() -> dict:
    store = get_store()
    trades = store.fetch_trades()
    feature_keys: set[str] = set()
    for t in trades:
        feature_keys.update(t["entry_metadata"].keys())
    outcomes = {}
    for t in trades:
        outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
    return {
        "trade_count": len(trades),
        "feature_columns": sorted(feature_keys),
        "labels": {"outcome": outcomes},
        "export_status": "available" if trades else "no trades recorded yet",
    }


# --- Experiment tracking (Phase 6B) ---

def create_experiment(req: ExperimentCreateRequest) -> ExperimentOut:
    store = get_store()
    experiment_id = uuid.uuid4().hex[:12]
    store.insert_experiment(
        experiment_id=experiment_id, name=req.name, hypothesis=req.hypothesis, strategy=req.strategy,
        dataset=req.dataset, parameters=req.parameters, run_id=req.run_id, notes=req.notes,
        model_id=req.model_id, dataset_version=req.dataset_version, metrics=req.metrics,
    )
    return get_experiment(experiment_id)


def get_experiment(experiment_id: str) -> ExperimentOut:
    store = get_store()
    exp = store.fetch_experiment(experiment_id)
    if exp is None:
        raise ApiError(f"No such experiment: {experiment_id!r}")
    return ExperimentOut(**exp)


def list_experiments(strategy: Optional[str] = None, limit: int = 100) -> list[ExperimentOut]:
    store = get_store()
    return [ExperimentOut(**e) for e in store.fetch_experiments(strategy=strategy, limit=limit)]


def update_experiment_notes(experiment_id: str, notes: str) -> ExperimentOut:
    store = get_store()
    if store.fetch_experiment(experiment_id) is None:
        raise ApiError(f"No such experiment: {experiment_id!r}")
    store.update_experiment_notes(experiment_id, notes)
    return get_experiment(experiment_id)


# --- ML research workstation (Phase 9) ---

def _git_commit() -> Optional[str]:
    """Best-effort provenance for a trained model's `app_version` -- never a
    reason training itself should fail (no git installed, not a checkout,
    a shallow clone without HEAD, ...)."""
    import subprocess
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def get_ml_dataset_health(strategy: str) -> DatasetHealthOut:
    from ..research.ml.dataset import dataset_health
    health = dataset_health(strategy)
    return DatasetHealthOut(
        status=health.status, reasons=health.reasons, trade_count=health.trade_count,
        win_count=health.win_count, loss_count=health.loss_count, win_rate=health.win_rate,
        date_range=list(health.date_range) if health.date_range else None,
        feature_count=health.feature_count, missing_value_count=health.missing_value_count,
        missing_value_ratio=health.missing_value_ratio,
        total_rows=health.total_rows, unique_timestamps=health.unique_timestamps,
        duplicate_market_events=health.duplicate_market_events,
    )


def get_feature_distribution(strategy: str, feature: str) -> FeatureDistributionOut:
    from ..research.ml.dataset import feature_distribution
    try:
        result = feature_distribution(strategy, feature)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return FeatureDistributionOut(**result)


def export_ml_dataset_csv(strategy: str) -> str:
    """One row per unique market opportunity, one column per feature -- the
    Dataset tab's "Export dataset" button. Reuses the same base-columns/
    metadata-union convention `research.features.write_ml_dataset_csv`
    established, working directly off `TradeStore.fetch_trades` dicts rather
    than reconstructing `TradeRecord` objects, since nothing here needs the
    excursion/regime fields that reconstruction would otherwise require.

    Deduped through the same `dedupe_market_events` the training pipeline
    uses (`research.ml.dataset.build_feature_matrix`) -- a rerun or
    overlapping walk-forward window must not hand this export the same
    trade twice. `strategy_params` columns are prefixed `param_` and kept
    strictly separate from the market-feature columns: they describe which
    backtest config produced a row, not what the market did."""
    import csv
    import io

    from ..research.ml.dataset import dedupe_market_events

    trades_raw = get_store().fetch_trades(strategy=strategy)
    trades = dedupe_market_events(trades_raw).trades
    base_columns = [
        "run_id", "contract", "strategy", "side", "entry_price", "exit_price", "net_pnl",
        "holding_minutes", "exit_reason", "session_date", "day_of_week", "hour", "entry_reason", "outcome",
    ]
    metadata_keys = sorted({key for t in trades for key in t["entry_metadata"]})
    param_keys = sorted({key for t in trades for key in t["strategy_params"]})
    param_columns = [f"param_{key}" for key in param_keys]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(base_columns + metadata_keys + param_columns)
    for t in trades:
        row = (
            [t.get(c) for c in base_columns]
            + [t["entry_metadata"].get(k, "") for k in metadata_keys]
            + [t["strategy_params"].get(k, "") for k in param_keys]
        )
        writer.writerow(row)
    return buffer.getvalue()


def get_correlation(strategy: str) -> list[CorrelationRowOut]:
    from ..research.ml.correlation import compute_correlations
    rows = compute_correlations(strategy)
    return [
        CorrelationRowOut(feature=r.feature, corr_vs_win=r.corr_vs_win, corr_vs_pnl=r.corr_vs_pnl, corr_vs_r=r.corr_vs_r)
        for r in rows
    ]


def submit_model_training_job(req: ModelTrainRequest) -> tuple[str, str]:
    """Inserts a new `ml_models` row (never updates an existing one -- see
    that table's docstring on versioning) and submits training through the
    same job system every other long-running task uses. Returns
    ``(job_id, model_id)`` -- the frontend drives `JobProgressBar` off
    `job_id` and displays the model report card by `model_id`."""
    from ..research.ml.dataset import build_feature_matrix, dataset_health
    from ..research.ml.stop_registry import register as stop_register

    health = dataset_health(req.strategy)
    if health.status == "NOT_ENOUGH_DATA":
        raise ApiError(f"Not enough data to train {req.strategy!r}: {'; '.join(health.reasons)}")

    fm = build_feature_matrix(req.strategy)
    model_id = uuid.uuid4().hex[:12]
    store = get_store()
    store.insert_model(
        model_id=model_id, strategy=req.strategy, model_type=req.model_type,
        feature_columns=fm.feature_columns, hyperparameters=req.hyperparameters,
        evaluation_mode=req.evaluation_mode, dataset_size=fm.dataset_size, dataset_version=fm.dataset_version,
    )
    stop_register(model_id)

    def work(progress: "jobs.JobProgress"):
        # Fresh imports/connection/feature-matrix build inside the worker
        # thread -- same "never share what the submitting thread loaded"
        # discipline `run_backtest_job` already follows, since sqlite3
        # connections (and, defensively, pandas objects built from them)
        # aren't meant to cross threads here.
        from ..research.ml import correlation as ml_correlation, predict as ml_predict, training as ml_training
        from ..research.ml.dataset import build_feature_matrix as _build_feature_matrix
        from ..research.ml.stop_registry import clear as stop_clear, should_stop as stop_should_stop

        worker_store = get_store()
        worker_store.update_model_status(model_id, "training")

        # `stop_clear` must run no matter how this job ends -- a deliberate
        # stop, a clean finish, or any other exception (a bad hyperparameter,
        # an sklearn/xgboost error). It used to only run on the first two
        # paths, leaking one `threading.Event` in `stop_registry` per job
        # that failed any other way, for the life of the process.
        try:
            fm_worker = _build_feature_matrix(req.strategy, store=worker_store)

            def progress_update(current: int, total: int, message: str) -> None:
                progress.update(current, total, message)

            def should_stop() -> bool:
                return stop_should_stop(model_id)

            try:
                result, metrics = ml_training.train_model(
                    req.model_type, fm_worker.X, fm_worker.y, fm_worker.trades, req.evaluation_mode,
                    req.hyperparameters, progress_update, should_stop,
                )
            except ml_training.TrainingStopped:
                worker_store.stop_model(model_id)
                return model_id, None

            metrics["diagnostics"] = result.diagnostics
            metrics["r_multiple_baseline"] = ml_correlation.r_multiple_baseline(fm_worker.trades)
            metrics["training_seconds"] = result.training_seconds

            train_indices = result.train_indices or []
            X_train_scaled = result.preprocessor["scaler"].transform(
                result.preprocessor["imputer"].transform(fm_worker.X.iloc[train_indices])
            )
            train_context = ml_predict.build_train_context(
                X_train_scaled, fm_worker.y.iloc[train_indices].to_numpy(), fm_worker.feature_columns,
                fm_worker.X.iloc[train_indices],
            )
            artifact_path = ml_predict.save_artifacts(
                model_id, req.model_type, result.preprocessor, result.fitted_model, fm_worker.feature_columns,
                fm_worker.numeric_raw_keys, fm_worker.dummy_source, train_context,
            )

            worker_store.complete_model(
                model_id, metrics=metrics, feature_importance=result.feature_importance,
                artifact_path=artifact_path, app_version=__version__, git_commit=_git_commit(),
                overfit_warning=result.overfit_warning, overfit_note=result.overfit_note,
            )
            return model_id, None
        except Exception as exc:
            # `jobs.py`'s own handler marks the *job* row failed on any
            # uncaught exception here, but nothing previously marked the
            # *model* row -- it used to stay at status='training' forever
            # (a bad hyperparameter or an sklearn/xgboost error left it
            # looking permanently in-progress in Model Comparison/report
            # cards, even though the job itself correctly showed 'failed').
            worker_store.fail_model(model_id, f"{type(exc).__name__}: {exc}")
            raise
        finally:
            stop_clear(model_id)

    job_id = jobs.submit("model_training", req.model_dump(mode="json"), work)
    store.update_model_job_id(model_id, job_id)
    return job_id, model_id


def _model_out(row: dict) -> MlModelOut:
    return MlModelOut(**row)


def get_model(model_id: str) -> MlModelOut:
    store = get_store()
    row = store.fetch_model(model_id)
    if row is None:
        raise ApiError(f"No such model: {model_id!r}")
    return _model_out(row)


def list_models(
    strategy: Optional[str] = None, model_family: Optional[str] = None, include_archived: bool = False,
) -> list[MlModelOut]:
    store = get_store()
    return [
        _model_out(r) for r in store.fetch_models(strategy=strategy, model_family=model_family, include_archived=include_archived)
    ]


def get_model_versions(model_family: str) -> list[MlModelOut]:
    store = get_store()
    return [_model_out(r) for r in store.fetch_model_versions(model_family)]


def stop_model(model_id: str) -> MlModelOut:
    from ..research.ml.stop_registry import request_stop
    if get_store().fetch_model(model_id) is None:
        raise ApiError(f"No such model: {model_id!r}")
    request_stop(model_id)
    return get_model(model_id)


def archive_model(model_id: str, archived: bool = True) -> MlModelOut:
    store = get_store()
    if store.fetch_model(model_id) is None:
        raise ApiError(f"No such model: {model_id!r}")
    store.archive_model(model_id, archived=archived)
    return get_model(model_id)


def delete_model(model_id: str) -> None:
    import shutil
    store = get_store()
    if store.fetch_model(model_id) is None:
        raise ApiError(f"No such model: {model_id!r}")
    artifact_path = store.delete_model(model_id)
    if artifact_path:
        shutil.rmtree(artifact_path, ignore_errors=True)


def update_model_notes(model_id: str, notes: str) -> MlModelOut:
    store = get_store()
    if store.fetch_model(model_id) is None:
        raise ApiError(f"No such model: {model_id!r}")
    store.update_model_notes(model_id, notes)
    return get_model(model_id)


def deploy_model(model_id: str) -> DeploymentOut:
    store = get_store()
    row = store.fetch_model(model_id)
    if row is None:
        raise ApiError(f"No such model: {model_id!r}")
    if row["status"] != "finished":
        raise ApiError(f"Model {model_id!r} is not finished training (status={row['status']!r}) -- cannot deploy.")
    deployment_id = uuid.uuid4().hex[:12]
    store.insert_deployment(deployment_id=deployment_id, strategy=row["strategy"], model_id=model_id, action="deploy")
    return DeploymentOut(**store.fetch_current_deployment(row["strategy"]))


def rollback_model(strategy: str, model_id: str) -> DeploymentOut:
    """A rollback is just another deployment-log row pointing at an earlier
    version -- nothing is destroyed, the full history stays visible."""
    store = get_store()
    row = store.fetch_model(model_id)
    if row is None or row["strategy"] != strategy:
        raise ApiError(f"No such model {model_id!r} for strategy {strategy!r}.")
    deployment_id = uuid.uuid4().hex[:12]
    store.insert_deployment(deployment_id=deployment_id, strategy=strategy, model_id=model_id, action="rollback")
    return DeploymentOut(**store.fetch_current_deployment(strategy))


def get_deployment(strategy: str) -> DeploymentStatusOut:
    store = get_store()
    current = store.fetch_current_deployment(strategy)
    history = store.fetch_deployment_history(strategy)
    return DeploymentStatusOut(
        strategy=strategy, current=DeploymentOut(**current) if current else None,
        history=[DeploymentOut(**h) for h in history],
    )


def predict_trade(req: PredictRequest) -> PredictionOut:
    from ..research.ml.predict import predict_one

    store = get_store()
    model_row = store.fetch_model(req.model_id)
    if model_row is None:
        raise ApiError(f"No such model: {req.model_id!r}")
    if model_row["status"] != "finished":
        raise ApiError(f"Model {req.model_id!r} has not finished training.")
    trades = store.fetch_trades(strategy=model_row["strategy"])
    trade = next((t for t in trades if t["id"] == req.trade_id), None)
    if trade is None:
        raise ApiError(f"No such trade {req.trade_id!r} for strategy {model_row['strategy']!r}.")

    feature_row = dict(trade["entry_metadata"])
    for key in ("regime_trend", "regime_volatility", "regime_session"):
        if trade.get(key) is not None:
            feature_row[key] = trade[key]

    result = predict_one(model_row, feature_row)
    return PredictionOut(**result)


def _load_ml_signal_filter(model_id: str, min_win_probability: float):
    from ..models import Signal, SignalAction
    from ..research.ml.predict import load_model, score as ml_score

    store = get_store()
    model_row = store.fetch_model(model_id)
    if model_row is None:
        raise ApiError(f"No such model: {model_id!r}")
    if model_row["status"] != "finished":
        raise ApiError(f"Model {model_id!r} has not finished training -- cannot use it as a filter.")
    loaded = load_model(model_row)

    def signal_filter(signal: Signal) -> Signal:
        probability = ml_score(loaded, dict(signal.metadata))
        if probability < min_win_probability:
            return Signal(
                action=SignalAction.HOLD,
                reason=f"AI filtered: win probability {probability:.2f} < {min_win_probability:.2f} threshold.",
            )
        return signal

    return signal_filter


def _metric_delta(before: Optional[Decimal], after: Optional[Decimal]) -> Optional[Decimal]:
    """``after - before``, or ``None`` if either side is undefined (e.g. a
    profit factor with zero losing trades on either side) -- never silently
    coerced to 0, which would fabricate an improvement/regression that
    isn't real."""
    if before is None or after is None:
        return None
    return after - before


def run_ai_backtest_comparison(req: BacktestRunRequest, ml_model_id: str, threshold: float = 0.5) -> dict:
    """Runs the identical backtest twice -- once with `ml_model_id` unset
    (baseline strategy performance), once with it set at `threshold` (AI
    filtered performance) -- and diffs the results. Also what populates a
    model's cached `derived_backtest_metrics` for Model Comparison.

    Classification metrics (accuracy/precision/recall/F1/ROC AUC) already
    live on the model row itself (`ml_models.metrics`, from `training.py`'s
    `_binary_metrics`) -- those judge the classifier in isolation. This
    comparison judges the thing that actually matters: what filtering by
    that classifier does to the *strategy's* performance. A model with
    great ROC AUC that trims profit factor or blows out drawdown is not an
    improvement, which is why these five are computed explicitly rather
    than left for a caller to infer from two raw run dumps."""
    without_req = req.model_copy(update={"ml_model_id": None})
    with_req = req.model_copy(update={"ml_model_id": ml_model_id, "ml_min_win_probability": threshold})

    without_result = run_backtest_job(without_req)
    with_result = run_backtest_job(with_req)

    without_trades = without_result.trade_count or 0
    with_trades = with_result.trade_count or 0
    trades_filtered = without_trades - with_trades
    trade_count_retained_pct = float(with_trades / without_trades * 100) if without_trades else None

    pnl_improvement = _metric_delta(without_result.net_pnl, with_result.net_pnl)
    improvement_pct = None
    if without_result.net_pnl:
        improvement_pct = float(pnl_improvement / abs(without_result.net_pnl) * 100)

    return {
        "without_ai": without_result.model_dump(mode="json"),
        "with_ai": with_result.model_dump(mode="json"),
        "trades_filtered": trades_filtered,
        "trade_count_retained": with_trades,
        "trade_count_retained_pct": trade_count_retained_pct,
        "improvement_pct": improvement_pct,
        # The five "does this actually help" metrics -- positive is always
        # an improvement (drawdown_reduction is pre-negated: max_drawdown
        # itself is a non-negative magnitude, so "reduction" = before - after).
        "pnl_improvement": pnl_improvement,
        "profit_factor_improvement": _metric_delta(without_result.profit_factor, with_result.profit_factor),
        "expectancy_improvement": _metric_delta(without_result.expectancy, with_result.expectancy),
        "drawdown_reduction": _metric_delta(with_result.max_drawdown, without_result.max_drawdown),
    }


def submit_ai_backtest_comparison_job(req: AiBacktestComparisonRequest) -> str:
    def work(progress: "jobs.JobProgress"):
        progress.update(0, 2, "Running backtest without AI filter...")
        comparison = run_ai_backtest_comparison(req.backtest, req.ml_model_id, req.ml_min_win_probability)
        progress.update(2, 2, "Done")
        return None, comparison

    request_payload = {
        **req.backtest.model_dump(mode="json"),
        "ml_model_id": req.ml_model_id, "ml_min_win_probability": req.ml_min_win_probability,
    }
    return jobs.submit("ai_backtest_compare", request_payload, work)


def compute_model_backtest_metrics(model_id: str, dataset: str, config_path: Path = Path("config.yaml")) -> dict:
    store = get_store()
    model_row = store.fetch_model(model_id)
    if model_row is None:
        raise ApiError(f"No such model: {model_id!r}")
    req = BacktestRunRequest(strategy_name=model_row["strategy"], dataset=dataset)
    comparison = run_ai_backtest_comparison(req, model_id, threshold=0.5)
    store.update_model_backtest_metrics(model_id, comparison)
    return comparison


# --- Universal client trade importer (Phase 10.1) ---

#: Where uploaded files sit between "upload" (preview) and "confirm"
#: (commit) -- gitignored, same "artifact on disk, row in DB" pattern
#: `research/ml/predict.py`'s `ml_models/` directory already established.
IMPORT_STAGING_DIR = Path("import_staging")


def create_client_profile(req: ClientProfileCreateRequest) -> ClientProfileOut:
    store = get_store()
    if any(p["name"] == req.name for p in store.fetch_client_profiles()):
        raise ApiError(f"A client profile named {req.name!r} already exists.")
    profile_id = uuid.uuid4().hex[:12]
    store.insert_client_profile(profile_id=profile_id, name=req.name, notes=req.notes)
    return ClientProfileOut(**store.fetch_client_profile(profile_id))


def list_client_profiles() -> list[ClientProfileOut]:
    return [ClientProfileOut(**p) for p in get_store().fetch_client_profiles()]


def _fill_preview_row(fill) -> dict:
    return {
        "row": fill.row_number, "timestamp": fill.timestamp.isoformat(), "symbol": fill.symbol, "side": fill.side,
        "quantity": str(fill.quantity), "price": str(fill.price), "commission": str(fill.commission),
        "realized_pnl": str(fill.realized_pnl) if fill.realized_pnl is not None else None,
    }


def stage_import_upload(profile_id: str, filename: str, content: bytes) -> ImportUploadResponse:
    """Synchronous: parsing + detection + a read-only FIFO dry-run is fast
    enough not to need a job. Writes the file to `IMPORT_STAGING_DIR` and a
    row to `import_staging` so `submit_import_confirmation` can find it
    again by the same `import_id` this returns."""
    from ..research import trade_import as ti

    store = get_store()
    if store.fetch_client_profile(profile_id) is None:
        raise ApiError(f"No such client profile: {profile_id!r}")

    is_excel = filename.lower().endswith((".xlsx", ".xlsm"))
    try:
        headers, raw_rows = ti.read_rows(content, is_excel)
    except Exception as exc:  # noqa: BLE001 -- a bad/unreadable file is a client error, not a 500
        raise ApiError(f"Could not read {filename!r}: {exc}") from exc
    if not headers:
        raise ApiError(f"No headers detected in {filename!r} -- is the file empty?")

    detected_format = ti.detect_format(headers)
    mapping = ti.suggest_mapping(headers, detected_format)

    staging_id = uuid.uuid4().hex[:12]
    IMPORT_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    stored_path = IMPORT_STAGING_DIR / f"{staging_id}{'.xlsx' if is_excel else '.csv'}"
    stored_path.write_bytes(content)
    store.insert_import_staging(
        staging_id=staging_id, profile_id=profile_id, filename=filename, stored_path=str(stored_path),
        detected_format=detected_format, suggested_mapping=mapping,
    )

    plan = ti.plan_import(store, profile_id, raw_rows, mapping)

    return ImportUploadResponse(
        import_id=staging_id, profile_id=profile_id, filename=filename, detected_format=detected_format,
        raw_headers=headers, suggested_mapping=mapping,
        total_rows=len(raw_rows), duplicate_count=plan.duplicate_count, error_count=len(plan.errors),
        matched_trade_count=len(plan.matches), errors=plan.errors, warnings=plan.warnings,
        preview_fill_rows=[_fill_preview_row(f) for f in plan.unique_fills[:20]],
        preview_trades=plan.trade_records_preview[:20],
    )


def submit_import_confirmation(staging_id: str, mapping_overrides: dict) -> str:
    """Re-runs the same `plan_import` pipeline the preview used (now with
    whatever mapping the wizard settled on) and submits the actual write
    through the job system -- returns a job id. `result_id` on that job is
    this same `staging_id`, reused as the `import_history.id` too, so there
    is exactly one id for this batch end to end."""
    from ..research import trade_import as ti

    store = get_store()
    staging = store.fetch_import_staging(staging_id)
    if staging is None:
        raise ApiError(f"No such staged import: {staging_id!r} (already confirmed, cancelled, or never uploaded).")
    profile = store.fetch_client_profile(staging["profile_id"])
    if profile is None:
        raise ApiError(f"No such client profile: {staging['profile_id']!r}")

    mapping = {**staging["suggested_mapping"], **{k: v for k, v in mapping_overrides.items() if v}}

    def work(progress: "jobs.JobProgress"):
        worker_store = get_store()
        progress.update(0, 3, "Reading staged file...")
        content = Path(staging["stored_path"]).read_bytes()
        is_excel = staging["filename"].lower().endswith((".xlsx", ".xlsm"))
        headers, raw_rows = ti.read_rows(content, is_excel)

        progress.update(1, 3, f"Matching {len(raw_rows)} fill(s) against open positions...")
        plan = ti.plan_import(worker_store, staging["profile_id"], raw_rows, mapping)
        trade_records = [ti.build_trade_record(m, staging_id, profile["name"]) for m in plan.matches]

        progress.update(2, 3, f"Writing {len(trade_records)} trade(s)...")
        try:
            worker_store.commit_client_import(
                import_id=staging_id, profile_id=staging["profile_id"], filename=staging["filename"],
                detected_format=staging["detected_format"],
                new_fill_rows=[
                    {
                        "fingerprint": f.fingerprint, "symbol": f.symbol, "fill_time": f.timestamp.isoformat(),
                        "side": f.side, "quantity": f.quantity, "price": f.price, "raw_row": f.raw_row,
                    }
                    for f in plan.unique_fills
                ],
                consumed_lot_ids=plan.consumed_lot_ids, updated_lots=plan.updated_lots,
                new_open_lots=[
                    {
                        "symbol": lot.symbol, "side": lot.side, "quantity_remaining": lot.quantity_remaining,
                        "entry_price": lot.entry_price, "entry_time": lot.entry_time,
                        "entry_fingerprint": lot.entry_fingerprint, "raw_entry_row": lot.raw_entry_row,
                    }
                    for lot in plan.new_open_lots
                ],
                trade_records=trade_records, total_fill_rows=len(raw_rows),
                imported_fill_count=len(plan.unique_fills), duplicate_fill_count=plan.duplicate_count,
                error_count=len(plan.errors), errors=plan.errors, warnings=plan.warnings, job_id=progress.job_id,
            )
        except Exception as exc:  # noqa: BLE001 -- record the failure, then re-raise for the job to show 'failed'
            worker_store.fail_client_import(
                import_id=staging_id, profile_id=staging["profile_id"], filename=staging["filename"],
                detected_format=staging["detected_format"], error_message=str(exc), job_id=progress.job_id,
            )
            raise

        worker_store.delete_import_staging(staging_id)
        stored_path = Path(staging["stored_path"])
        if stored_path.exists():
            stored_path.unlink()

        progress.update(3, 3, "Done")
        return staging_id, None

    return jobs.submit("client_import", {"staging_id": staging_id, "mapping": mapping}, work)


def cancel_import_staging(staging_id: str) -> None:
    store = get_store()
    stored_path = store.delete_import_staging(staging_id)
    if stored_path:
        path = Path(stored_path)
        if path.exists():
            path.unlink()


def list_import_history(profile_id: Optional[str] = None, limit: int = 100) -> list[ImportHistoryOut]:
    return [ImportHistoryOut(**h) for h in get_store().fetch_import_history(profile_id=profile_id, limit=limit)]


# --- Research-server "recommendation" finding: test before deploy (Phase 10.2) ---

def run_params_comparison(
    strategy: str, dataset: str, recommended_params: dict, config_path: Path = Path("config.yaml"),
) -> dict:
    """Runs the same backtest twice -- once with whatever's currently
    configured for `strategy` (its own `strategy_params` if it's
    config.yaml's active strategy, else that strategy class's own
    defaults, matching how the research server itself falls back -- see
    `research_server/paper_trader.py`), once with `recommended_params` --
    and diffs the results. This is what "Test More" on a recommendation
    finding runs before anyone commits to `deploy_strategy_params`."""
    settings = load_settings(config_path)
    current_params = settings.strategy_params if strategy == settings.strategy_name else {}

    current_result = run_backtest_job(BacktestRunRequest(strategy_name=strategy, dataset=dataset, strategy_params=current_params), config_path)
    recommended_result = run_backtest_job(BacktestRunRequest(strategy_name=strategy, dataset=dataset, strategy_params=recommended_params), config_path)

    current_trades = current_result.trade_count or 0
    recommended_trades = recommended_result.trade_count or 0
    trade_count_retained_pct = float(recommended_trades / current_trades * 100) if current_trades else None

    pnl_improvement = _metric_delta(current_result.net_pnl, recommended_result.net_pnl)
    improvement_pct = None
    if current_result.net_pnl:
        improvement_pct = float(pnl_improvement / abs(current_result.net_pnl) * 100)

    return {
        "current": current_result.model_dump(mode="json"),
        "recommended": recommended_result.model_dump(mode="json"),
        "improvement_pct": improvement_pct,
        # Same "does this actually help" diff as `run_ai_backtest_comparison`
        # -- net P&L alone can look better while quietly trading a thinner
        # sample, a worse profit factor, or a bigger drawdown.
        "trade_count_retained": recommended_trades,
        "trade_count_retained_pct": trade_count_retained_pct,
        "pnl_improvement": pnl_improvement,
        "profit_factor_improvement": _metric_delta(current_result.profit_factor, recommended_result.profit_factor),
        "expectancy_improvement": _metric_delta(current_result.expectancy, recommended_result.expectancy),
        "drawdown_reduction": _metric_delta(recommended_result.max_drawdown, current_result.max_drawdown),
    }


def submit_params_comparison_job(strategy: str, dataset: str, recommended_params: dict) -> str:
    def work(progress: "jobs.JobProgress"):
        progress.update(0, 2, f"Running backtest with current {strategy} params...")
        comparison = run_params_comparison(strategy, dataset, recommended_params)
        progress.update(2, 2, "Done")
        return None, comparison

    request_payload = {"strategy": strategy, "dataset": dataset, "recommended_params": recommended_params}
    return jobs.submit("params_comparison", request_payload, work)


# --- Dashboard intelligence / insights (Phase 6B) ---

#: Thresholds below which an insight isn't generated at all -- a strategy
#: with only a handful of trades doesn't get a "best day of week" insight,
#: because that claim would be noise dressed up as a finding. Mirrors
#: `backtest.metrics.MIN_TRADES_FOR_SIGNIFICANCE`'s own reasoning.
_MIN_TRADES_FOR_INSIGHT = 20
#: A day-of-week (or session) skew has to beat the strategy's overall
#: average by at least this fraction to be worth surfacing -- otherwise
#: every strategy would get a "best day" insight purely from sampling noise.
_SKEW_THRESHOLD_PCT = Decimal("30")


def generate_insights() -> list[InsightOut]:
    """Rule-based, data-derived research summaries for the dashboard --
    deliberately NOT a language model or anything that could phrase a
    guess as a finding. Every insight here is a direct, mechanical read of
    already-computed data (`backtest.metrics`, stored trades, the latest
    optimizer safety report), the same figures the rest of this API already
    surfaces -- this function's only job is deciding which of them are
    worth a sentence on the homepage. An insight that can't be traced back
    to a specific number is a bug, not a feature.
    """
    store = get_store()
    insights: list[InsightOut] = []

    for strategy in StrategyRegistry.names():
        trades = store.fetch_trades(strategy=strategy)
        if len(trades) < _MIN_TRADES_FOR_INSIGHT:
            continue

        insights.extend(_commission_drag_insight(strategy, trades))
        insights.extend(_weekday_skew_insight(strategy, trades))
        insights.extend(_session_skew_insight(strategy, trades))

    insights.extend(_overfit_insight())

    return insights


def _commission_drag_insight(strategy: str, trades: list[dict]) -> list[InsightOut]:
    gross = sum((t["gross_pnl"] for t in trades), Decimal("0"))
    commission = sum((t["commission"] for t in trades), Decimal("0"))
    net = sum((t["net_pnl"] for t in trades), Decimal("0"))
    if gross <= 0:
        return []
    drag_pct = commission / gross * 100
    if net <= 0 and drag_pct > 20:
        return [InsightOut(
            strategy=strategy, category="costs", severity="warning",
            message=(
                f"{strategy}: net P&L is negative (${net:.2f}) while commission alone is "
                f"{drag_pct:.0f}% of gross profit (${commission:.2f} of ${gross:.2f}) across "
                f"{len(trades)} trades -- the edge, if any, does not survive real costs."
            ),
        )]
    if drag_pct > 30:
        return [InsightOut(
            strategy=strategy, category="costs", severity="warning",
            message=(
                f"{strategy}: commission consumes {drag_pct:.0f}% of gross profit across "
                f"{len(trades)} trades -- costs this large mean small edges will not survive live."
            ),
        )]
    return []


def _weekday_skew_insight(strategy: str, trades: list[dict]) -> list[InsightOut]:
    by_day: dict[str, list[Decimal]] = {}
    for t in trades:
        by_day.setdefault(t["day_of_week"], []).append(t["net_pnl"])
    if len(by_day) < 2:
        return []

    total_net = sum((t["net_pnl"] for t in trades), Decimal("0"))
    if total_net <= 0:
        return []

    best_day = max(by_day.items(), key=lambda kv: sum(kv[1]))
    best_day_net = sum(best_day[1])
    share = best_day_net / total_net * 100 if total_net else Decimal("0")
    if share >= _SKEW_THRESHOLD_PCT and len(best_day[1]) >= 5:
        return [InsightOut(
            strategy=strategy, category="timing", severity="info",
            message=(
                f"{strategy}: {best_day[0]} accounts for {share:.0f}% of total net P&L "
                f"(${best_day_net:.2f} of ${total_net:.2f}) across {len(best_day[1])} trades on that day."
            ),
        )]
    return []


def _session_skew_insight(strategy: str, trades: list[dict]) -> list[InsightOut]:
    by_session: dict[str, list[Decimal]] = {}
    for t in trades:
        session = t.get("regime_session")
        if session is None:
            continue
        by_session.setdefault(session, []).append(t["net_pnl"])
    if len(by_session) < 2:
        return []

    losers = {s: vals for s, vals in by_session.items() if sum(vals) < 0 and len(vals) >= 5}
    if not losers:
        return []
    worst_session, worst_vals = min(losers.items(), key=lambda kv: sum(kv[1]))
    return [InsightOut(
        strategy=strategy, category="regime", severity="warning",
        message=(
            f"{strategy}: the '{worst_session}' session is net negative "
            f"(${sum(worst_vals):.2f} across {len(worst_vals)} trades) -- see the Market Regime "
            f"page before trading this strategy during that window."
        ),
    )]


def _overfit_insight() -> list[InsightOut]:
    store = get_store()
    optimizer_runs = [r for r in store.fetch_runs(kind="optimizer", limit=20) if r["status"] == "completed"]
    if not optimizer_runs:
        return []
    latest = optimizer_runs[0]
    # Confidence isn't stored directly on `runs` (it's a `SafetyReport`
    # produced fresh each optimizer run, not persisted) -- the closest
    # proxy already on the row is a training-to-validation collapse, the
    # same signature `research.safety.check_degradation` flags.
    if latest["net_pnl"] is not None and latest["validation_net_pnl"] is not None:
        if latest["net_pnl"] > 0 and latest["validation_net_pnl"] <= 0:
            return [InsightOut(
                strategy=latest["strategy"], category="overfitting", severity="warning",
                message=(
                    f"{latest['strategy']}: the most recent optimization run was profitable in "
                    f"training (${latest['net_pnl']:.2f}) but not out-of-sample "
                    f"(${latest['validation_net_pnl']:.2f}) -- this result appears overfit. "
                    f"See GET /api/optimizer/results/{latest['id']} for the full trial list."
                ),
            )]
    return []


# --- System overview ---

def system_overview(config_path: Path = Path("config.yaml")) -> SystemOverview:
    store = get_store()
    all_runs = store.fetch_runs(limit=10_000)
    backtests = [r for r in all_runs if r["kind"] in ("backtest", "walk_forward")]
    optimizer_runs = [r for r in all_runs if r["kind"] == "optimizer"]
    reports = store.fetch_reports()

    last_opt = optimizer_runs[0]["created_at"] if optimizer_runs else None
    last_report = reports[0]["created_at"] if reports else None

    # `.path` is SQLite-only (see TradeStore.location's docstring) --
    # `hasattr` branches rather than calling `.location` unconditionally so
    # the SQLite "not yet created" distinction (a fresh checkout with no
    # research.db written yet) is preserved exactly; a Postgres-backed store
    # has already proven itself reachable by the successful queries above.
    if hasattr(store, "path"):
        db_status = "ok" if store.path.exists() else "not yet created"
    else:
        db_status = "ok"

    return SystemOverview(
        version=__version__,
        strategies_available=StrategyRegistry.names(),
        total_backtests=len(backtests),
        total_optimizer_runs=len(optimizer_runs),
        total_trades_analyzed=store.trade_count(),
        total_reports_generated=len(reports),
        last_optimization_run=last_opt,
        last_report_generated=last_report,
        database_path=store.location,
        database_status=db_status,
    )


# --- Logs ---

#: How far back (in bytes) `_read_tail_lines` starts looking before growing
#: its window -- generously larger than 2000 lines need at any realistic
#: line length, so the common case never needs a second read.
_TAIL_INITIAL_CHUNK_BYTES = 4 * 1024 * 1024
_TAIL_MAX_CHUNK_GROWTHS = 4


def _read_tail_lines(path: Path, max_lines: int) -> list[str]:
    """Returns up to the last `max_lines` complete lines of `path` without
    ever reading the whole file into memory. `decisions.jsonl` is
    append-only with no rotation (see journal.py's `DecisionJournal`) and
    can grow without bound over a long-running deployment -- confirmed the
    hard way during a Stabilization Mode API sweep (2026-07-28): one real
    `decisions.jsonl` had reached 9.2 GB / 34.2M lines, and the previous
    `path.read_text().splitlines()` here read the entire file into memory
    on every single `GET /api/logs` call just to discard all but the last
    2000 lines -- a real crash-risk (multi-GB allocation) and multi-second-
    to-multi-minute latency hit, not a hypothetical one. Seeks backward
    from the end in a bounded, geometrically-growing window instead; only
    pathologically long individual lines would ever need more than one
    read."""
    size = path.stat().st_size
    chunk_size = min(_TAIL_INITIAL_CHUNK_BYTES, size) or 1
    with path.open("rb") as fh:
        for _ in range(_TAIL_MAX_CHUNK_GROWTHS):
            read_size = min(chunk_size, size)
            fh.seek(size - read_size)
            data = fh.read(read_size)
            lines = data.decode("utf-8", errors="replace").splitlines()
            if read_size < size:
                # The first line may be a partial line straddling our seek
                # point -- drop it rather than risk a truncated JSON object.
                lines = lines[1:]
            if len(lines) >= max_lines or read_size >= size:
                return lines[-max_lines:]
            chunk_size *= 4
    return lines[-max_lines:]


def read_logs(directory: Path = Path("logs"), limit: int = 200, kind: Optional[str] = None) -> list[dict]:
    """Reads `decisions.jsonl` event entries (not every hold/decision --
    those are the trading journal's own bulk detail; this surfaces the
    'event'-typed rows: strategy_error, halt, flatten_failed) plus a
    synthetic entry per run in the research store, newest first. A real
    aggregated app log was out of scope for this pass -- see
    docs/RESEARCH_INTERFACE.md's Phase 6B notes."""
    entries: list[dict] = []

    path = directory / "decisions.jsonl"
    if path.exists():
        try:
            lines = _read_tail_lines(path, 2000)
        except OSError:
            lines = []
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "event":
                entries.append({
                    "timestamp": obj.get("timestamp", ""), "level": "WARNING", "kind": obj.get("kind", "event"),
                    "message": obj.get("message", ""),
                })

    store = get_store()
    for run in store.fetch_runs(limit=500):
        level = "ERROR" if run["status"] == "failed" else "INFO"
        message = run.get("error_message") or f"{run['kind']} run for {run['strategy']} on {run['contract']}"
        entries.append({
            "timestamp": run["created_at"], "level": level, "kind": f"run:{run['kind']}", "message": message,
        })

    if kind is not None:
        entries = [e for e in entries if e["kind"] == kind]
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries[:limit]
