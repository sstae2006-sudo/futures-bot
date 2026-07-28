"""Pydantic request/response models for the research API.

Deliberately separate from `futures_bot.config.Settings`/`futures_bot.models`:
those are the trading domain's own types, tuned for validating a config file
and carrying Decimal-exact money through the engine. Coupling the API's wire
format to them directly would mean an internal refactor of either could
silently break every API client. Request bodies are strict (real input
validation); several response bodies use `dict[str, Any]` for payloads that
already come out of `TradeStore` as plain, well-tested dicts (see
`store.py`) -- re-modeling every field a second time here would be a second
place for that mapping to drift out of sync, for no real safety benefit
since nothing external ever supplies that data.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class StrategyParamOut(BaseModel):
    name: str
    type: str
    default: Any
    description: Optional[str] = None


class StrategyInfo(BaseModel):
    name: str
    parameters: list[StrategyParamOut]


class DatasetInfo(BaseModel):
    filename: str
    size_bytes: int
    bars_hint: Optional[int] = None


class BacktestRunRequest(BaseModel):
    strategy_name: str
    dataset: str = Field(description="CSV filename, as returned by GET /api/datasets")
    contract: str = "MES"
    strategy_params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Full parameter set for strategy_name, not a partial override merged onto "
            "config.yaml's own strategy_params -- that file's values belong to whatever strategy "
            "it names as its default, which may not be this request's strategy_name. Omitted keys "
            "fall back to the strategy class's own constructor defaults (see GET /api/strategies)."
        ),
    )
    walk_forward: bool = False
    start: Optional[str] = Field(default=None, description="ISO date, inclusive. Omit for the full file.")
    end: Optional[str] = Field(default=None, description="ISO date, inclusive. Omit for the full file.")
    starting_cash: Optional[Decimal] = None
    stop_loss_points: Optional[Decimal] = None
    take_profit_points: Optional[Decimal] = None
    contracts_per_trade: Optional[int] = None
    daily_max_loss: Optional[Decimal] = None
    max_trades_per_session: Optional[int] = None
    ml_model_id: Optional[str] = Field(
        default=None,
        description=(
            "Phase 9: when set, entry signals are scored by this trained model and converted to a "
            "HOLD if predicted win probability falls below ml_min_win_probability. Omitted (the "
            "default) means every existing backtest behaves exactly as before -- see "
            "engine.TradingEngine's signal_filter hook."
        ),
    )
    ml_min_win_probability: float = 0.5


class RunSummary(BaseModel):
    id: str
    kind: str
    status: str
    strategy: str
    contract: str
    trade_count: Optional[int] = None
    net_pnl: Optional[Decimal] = None
    profit_factor: Optional[Decimal] = None
    win_rate: Optional[Decimal] = None
    sharpe_ratio: Optional[Decimal] = None
    max_drawdown: Optional[Decimal] = None
    validation_net_pnl: Optional[Decimal] = None
    walk_forward: bool
    created_at: str
    completed_at: Optional[str] = None


class RunDetail(RunSummary):
    strategy_params: dict[str, Any]
    csv_path: Optional[str] = None
    expectancy: Optional[Decimal] = None
    sortino_ratio: Optional[Decimal] = None
    max_drawdown_pct: Optional[Decimal] = None
    validation_trade_count: Optional[int] = None
    validation_profit_factor: Optional[Decimal] = None
    caveats: list[str] = Field(default_factory=list)
    first_bar: Optional[str] = None
    last_bar: Optional[str] = None
    error_message: Optional[str] = None


class TradeOut(BaseModel):
    """Mirrors `TradeStore.fetch_trades`'s row shape -- see that module's
    docstring for why trades are denormalized rather than joined."""
    id: int
    run_id: str
    contract: str
    strategy: str
    strategy_params: dict[str, Any]
    entry_time: str
    exit_time: str
    side: str
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    commission: Decimal
    net_pnl: Decimal
    holding_minutes: float
    exit_reason: str
    session_date: str
    day_of_week: str
    hour: int
    entry_reason: str
    entry_metadata: dict[str, Any]
    outcome: str
    mfe_points: Optional[Decimal] = None
    mae_points: Optional[Decimal] = None
    efficiency: Optional[Decimal] = None
    regime_trend: Optional[str] = None
    regime_volatility: Optional[str] = None
    regime_session: Optional[str] = None
    #: Phase 10.3: stable id independent of the DB row's own `id`, plus the
    #: bracket levels and simulated slippage this trade actually carried --
    #: see `models.Trade`/`research.features.TradeRecord`.
    trade_id: Optional[str] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    entry_slippage: Decimal = Decimal("0")
    exit_slippage: Decimal = Decimal("0")


class TradeAnalyticsSummary(BaseModel):
    """Phase 6B: 'best entries, poor exits, missed opportunities' -- see
    `services.trade_analytics_summary`'s docstring for exactly what each
    bucket means."""
    best_entries: list[TradeOut]
    poor_exits: list[TradeOut]
    missed_opportunities: list[TradeOut]


class RegimeBucket(BaseModel):
    value: str
    trade_count: int
    net_pnl: Decimal
    win_rate: Optional[Decimal]
    average_efficiency: Optional[Decimal]


class RegimePerformanceOut(BaseModel):
    strategy: Optional[str] = None
    trend: list[RegimeBucket]
    volatility: list[RegimeBucket]
    session: list[RegimeBucket]


class EquityPoint(BaseModel):
    trade_number: int
    timestamp: Optional[str] = None
    equity: Decimal


class DrawdownPoint(BaseModel):
    trade_number: int
    timestamp: Optional[str] = None
    drawdown: Decimal


class PerformanceOut(BaseModel):
    run_id: str
    equity_curve: list[EquityPoint]
    drawdown_curve: list[DrawdownPoint]
    max_drawdown: Decimal
    max_drawdown_pct: Optional[Decimal]
    longest_drawdown_trades: int
    max_consecutive_losses: int
    max_consecutive_wins: int
    wins: int
    losses: int
    average_win: Optional[Decimal]
    average_loss: Optional[Decimal]
    expectancy: Optional[Decimal]
    average_r_multiple: Optional[Decimal]
    average_holding_minutes: Optional[float]


class CompareRequest(BaseModel):
    dataset: str
    contract: str = "MES"
    strategy_names: Optional[list[str]] = Field(
        default=None, description="Omit for every registered strategy."
    )
    start: Optional[str] = None
    end: Optional[str] = None


class CompareEntryOut(BaseModel):
    strategy: str
    net_pnl: Decimal
    profit_factor: Optional[Decimal]
    win_rate: Optional[Decimal]
    sharpe_ratio: Optional[Decimal]
    max_drawdown: Decimal
    trade_count: int
    equity_curve: list[EquityPoint]


class CompareResult(BaseModel):
    entries: list[CompareEntryOut]


class OptimizerRunRequest(BaseModel):
    strategy_name: str
    dataset: str
    contract: str = "MES"
    param_grid: dict[str, Any] = Field(description="Any list-valued entry is swept; scalars stay fixed.")
    top_n: int = 10
    rolling: bool = False
    start: Optional[str] = None
    end: Optional[str] = None


class TrialOut(BaseModel):
    rank: Optional[int]
    params: dict[str, Any]
    train_trades: int
    train_net_pnl: Decimal
    train_profit_factor: Optional[Decimal]
    train_max_drawdown: Decimal
    validation_trades: Optional[int]
    validation_net_pnl: Optional[Decimal]
    validation_profit_factor: Optional[Decimal]
    validation_max_drawdown: Optional[Decimal]


class OptimizerResultOut(BaseModel):
    batch_id: str
    strategy: str
    combos_tried: int
    ranked_trials: list[TrialOut]
    confidence: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class JobOut(BaseModel):
    """Mirrors `TradeStore.fetch_job`'s row shape. `result_id` points at an
    existing resource (a `runs.id` for backtest/optimizer, a `reports.id`
    for report) once the job completes; `result_payload` carries the full
    result inline for job kinds (chiefly `compare`) with no such table --
    see `api/jobs.py::submit`'s docstring."""
    id: str
    kind: str
    status: str
    progress_current: int
    progress_total: int
    progress_message: Optional[str] = None
    request: dict[str, Any]
    result_id: Optional[str] = None
    result_payload: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class ReportGenerateRequest(BaseModel):
    run_id: str


class ReportOut(BaseModel):
    id: str
    run_id: str
    format: str
    path: str
    created_at: str


class OverfitVerdict(BaseModel):
    """Traffic-light read on a walk-forward/optimizer result -- see
    `services.overfit_verdict`'s docstring for the exact thresholds."""
    level: Literal["green", "yellow", "red"]
    label: str
    reasons: list[str]


class SystemOverview(BaseModel):
    version: str
    strategies_available: list[str]
    total_backtests: int
    total_optimizer_runs: int
    total_trades_analyzed: int
    total_reports_generated: int
    last_optimization_run: Optional[str] = None
    last_report_generated: Optional[str] = None
    database_path: str
    database_status: str


class DatabaseHealthOut(BaseModel):
    """Mirrors `db.health.DatabaseHealth` -- see that dataclass's own
    docstring for what `configured` vs. `ok` each mean."""

    configured: bool
    ok: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None


class SystemHealthOut(BaseModel):
    """`/api/system/health` -- team-deployment mode's real-data source for
    Mission Control's `StatusBar`/`HealthGrid` (see plan item #7,
    `PROJECT_STATE.md`). `status` is always `"ok"` when this route actually
    responds -- the response itself is the backend-liveness proof; a
    process that can't serve requests can't return this payload at all,
    so there is no `"down"` value to represent here."""

    status: Literal["ok"] = "ok"
    version: str
    environment: Literal["development", "team", "production"]
    uptime_seconds: float
    database: DatabaseHealthOut
    last_backup_at: Optional[str] = Field(
        default=None, description="ISO 8601 timestamp of the most recent tools/backup_timescaledb.py run, if any."
    )
    connected_users: int = Field(
        description="Distinct client IPs seen in the last 15 minutes -- see connected_users.py for exactly what this can and can't mean without a real auth system."
    )


class ExperimentCreateRequest(BaseModel):
    name: str
    hypothesis: str = Field(description="What you expect and why, e.g. 'VWAP performs better in high volatility.'")
    strategy: str
    dataset: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    run_id: Optional[str] = Field(default=None, description="Link to a backtest run that tested this, if any.")
    notes: Optional[str] = None
    model_id: Optional[str] = Field(default=None, description="Phase 9: link to the ml_models row this experiment tested, if any.")
    dataset_version: Optional[str] = None
    metrics: Optional[dict[str, Any]] = None


class ExperimentOut(BaseModel):
    id: str
    name: str
    hypothesis: str
    strategy: str
    dataset: Optional[str] = None
    parameters: dict[str, Any]
    run_id: Optional[str] = None
    notes: Optional[str] = None
    model_id: Optional[str] = None
    dataset_version: Optional[str] = None
    metrics: Optional[dict[str, Any]] = None
    created_at: str


class ExperimentNotesUpdate(BaseModel):
    notes: str


class InsightOut(BaseModel):
    """One data-derived research summary for the dashboard -- see
    `services.generate_insights`'s docstring for what can and can't appear
    here (never a fabricated finding, only ones with data behind them).

    `details` is optional structured data behind the finding -- e.g. a
    `research_server.insights.recommendation_findings` entry carries
    `run_id`/`current_params`/`recommended_params`/`train_net_pnl` here, so
    the dashboard's detail window has real numbers to render, not just the
    same message string re-shown."""
    strategy: Optional[str] = None
    category: str
    message: str
    severity: Literal["info", "warning"]
    details: Optional[dict[str, Any]] = None


class ConfigDeployRequest(BaseModel):
    strategy: str
    params: dict[str, Any]
    run_id: Optional[str] = Field(default=None, description="The optimizer run this recommendation came from, for traceability.")


class ConfigDeploymentOut(BaseModel):
    id: str
    strategy: str
    action: Literal["deploy", "rollback"]
    params: dict[str, Any]
    backup_path: Optional[str] = None
    created_at: str


class ParamsComparisonRequest(BaseModel):
    strategy: str
    dataset: str
    recommended_params: dict[str, Any]


class ParamsComparisonOut(BaseModel):
    current: RunSummary
    recommended: RunSummary
    improvement_pct: Optional[float] = None
    trade_count_retained: int = 0
    trade_count_retained_pct: Optional[float] = None
    pnl_improvement: Optional[Decimal] = None
    profit_factor_improvement: Optional[Decimal] = None
    expectancy_improvement: Optional[Decimal] = None
    drawdown_reduction: Optional[Decimal] = None


class ModelTrainRequest(BaseModel):
    strategy: str
    model_type: Literal["logistic_regression", "random_forest", "xgboost", "neural_network"]
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    evaluation_mode: Literal["chronological_split", "walk_forward"] = "chronological_split"


class ModelTrainResponse(BaseModel):
    job_id: str
    model_id: str


class MlModelOut(BaseModel):
    """Mirrors `TradeStore.fetch_model`'s row shape -- see trade_store.py's
    `ml_models` table docstring. `metrics` is always namespaced
    (`{"train": ..., "validation": ..., "test": ...}` or
    `{"train": ..., "walk_forward_out_of_sample": ...}`) so in-sample and
    out-of-sample numbers can never be displayed interchangeably."""
    id: str
    model_family: str
    version: int
    strategy: str
    model_type: str
    status: str
    job_id: Optional[str] = None
    feature_columns: list[str]
    hyperparameters: dict[str, Any]
    evaluation_mode: str
    dataset_size: int
    dataset_version: str
    metrics: Optional[dict[str, Any]] = None
    feature_importance: Optional[list[dict[str, Any]]] = None
    overfit_warning: bool
    overfit_note: Optional[str] = None
    derived_backtest_metrics: Optional[dict[str, Any]] = None
    artifact_path: Optional[str] = None
    app_version: Optional[str] = None
    git_commit: Optional[str] = None
    notes: Optional[str] = None
    archived: bool
    error_message: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class ModelNotesUpdate(BaseModel):
    notes: str


class PredictRequest(BaseModel):
    model_id: str
    trade_id: int


class PredictionOut(BaseModel):
    model_id: str
    probability: float
    confidence: float
    expected_win_probability: float
    expected_value_r: Optional[float] = None
    similar_trade_count: int
    similar_trade_win_rate: Optional[float] = None
    calibration_bucket: Optional[dict[str, Any]] = None
    top_reasons: list[dict[str, Any]]


class CorrelationRowOut(BaseModel):
    feature: str
    corr_vs_win: Optional[float] = None
    corr_vs_pnl: Optional[float] = None
    corr_vs_r: Optional[float] = None


class DatasetHealthOut(BaseModel):
    status: Literal["READY", "WARNING", "NOT_ENOUGH_DATA"]
    reasons: list[str]
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: Optional[float] = None
    date_range: Optional[list[str]] = None
    feature_count: int
    missing_value_count: int
    missing_value_ratio: float
    total_rows: int = 0
    unique_timestamps: int = 0
    duplicate_market_events: int = 0


class FeatureDistributionOut(BaseModel):
    feature: str
    bins: list[float]
    counts: list[int]
    win_counts: list[int]
    loss_counts: list[int]
    mean: Optional[float] = None
    std: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    win_average: Optional[float] = None
    loss_average: Optional[float] = None


class DeploymentActionRequest(BaseModel):
    threshold: float = 0.5


class DeploymentOut(BaseModel):
    id: str
    strategy: str
    model_id: Optional[str] = None
    action: str
    created_at: str


class DeploymentStatusOut(BaseModel):
    strategy: str
    current: Optional[DeploymentOut] = None
    history: list[DeploymentOut] = Field(default_factory=list)


class AiBacktestComparisonRequest(BaseModel):
    backtest: BacktestRunRequest
    ml_model_id: str
    ml_min_win_probability: float = 0.5


class AiBacktestComparisonOut(BaseModel):
    without_ai: RunSummary
    with_ai: RunSummary
    trades_filtered: int
    trade_count_retained: int
    trade_count_retained_pct: Optional[float] = None
    improvement_pct: Optional[float] = None
    pnl_improvement: Optional[Decimal] = None
    profit_factor_improvement: Optional[Decimal] = None
    expectancy_improvement: Optional[Decimal] = None
    drawdown_reduction: Optional[Decimal] = None


class ClientProfileCreateRequest(BaseModel):
    name: str
    notes: Optional[str] = None


class ClientProfileOut(BaseModel):
    id: str
    name: str
    notes: Optional[str] = None
    created_at: str


class ImportUploadResponse(BaseModel):
    """Everything the column-mapping wizard needs, computed synchronously
    at upload time (no job needed just to preview) -- and everything the
    confirm step needs to find the staged file again by `import_id`."""
    import_id: str
    profile_id: str
    filename: str
    detected_format: Literal["tradovate", "ninjatrader", "generic"]
    raw_headers: list[str]
    suggested_mapping: dict[str, Optional[str]]
    total_rows: int
    duplicate_count: int
    error_count: int
    matched_trade_count: int
    errors: list[dict[str, Any]]
    warnings: list[str]
    preview_fill_rows: list[dict[str, Any]]
    preview_trades: list[dict[str, Any]]


class ImportConfirmRequest(BaseModel):
    mapping: dict[str, Optional[str]] = Field(
        default_factory=dict,
        description="Overrides onto the staged suggested_mapping -- only include fields the wizard changed.",
    )


class ImportHistoryOut(BaseModel):
    id: str
    profile_id: str
    filename: str
    detected_format: str
    status: Literal["completed", "failed"]
    total_fill_rows: int
    imported_fill_count: int
    duplicate_fill_count: int
    error_count: int
    trades_created: int
    errors: list[dict[str, Any]]
    warnings: list[str]
    job_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str


class LogEntry(BaseModel):
    timestamp: str
    level: str
    kind: str
    message: str


class LiveSessionStartRequest(BaseModel):
    live_symbol: str = Field(description="The data vendor's contract symbol, e.g. 'MESH6'.")
    resolution: str = "5min"
    poll_seconds: int = 30


class LivePositionOut(BaseModel):
    side: str
    quantity: int
    entry_price: str
    stop_loss: Optional[str] = None
    take_profit: Optional[str] = None
    unrealized_pnl: str


class LiveSessionStatusOut(BaseModel):
    """Mirrors `LiveSessionManager._Snapshot` -- see `api/live_session.py`
    for the (paper-only, enforced at runtime) engine this reports on."""
    status: Literal["stopped", "starting", "running", "stopping", "error"]
    run_id: Optional[str] = None
    strategy: Optional[str] = None
    contract: Optional[str] = None
    broker: Optional[str] = None
    live_symbol: Optional[str] = None
    resolution: Optional[str] = None
    poll_seconds: Optional[int] = None
    position: Optional[LivePositionOut] = None
    session_pnl: Optional[str] = None
    trade_count_today: Optional[int] = None
    halted: bool = False
    halt_reason: Optional[str] = None
    last_bar_time: Optional[str] = None
    last_bar_close: Optional[str] = None
    last_feed_error: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


# --- Phase 8A: market-data pipeline ---

class ProductCoverageOut(BaseModel):
    product_code: str
    contracts_stored: list[str]
    bars_stored: int
    earliest: Optional[str] = None
    latest: Optional[str] = None
    open_gaps: int


class MarketDataOverviewOut(BaseModel):
    total_bars: int
    products: list[ProductCoverageOut]
    total_open_gaps: int
    database_path: str
    database_size_bytes: int
    last_sync_at: Optional[str] = None
    last_sync_status: Optional[str] = None
    recent_rolls: list[dict[str, Any]] = Field(default_factory=list)
    scheduler_running: bool = False


class SyncRunOut(BaseModel):
    id: str
    product_code: str
    resolution: str
    kind: str
    status: str
    bars_fetched: int
    error_message: Optional[str] = None
    started_at: str
    completed_at: Optional[str] = None


class GapOut(BaseModel):
    id: int
    product_code: str
    resolution: str
    gap_start: str
    gap_end: str
    detected_at: str
    resolved_at: Optional[str] = None


class SyncRequest(BaseModel):
    product_code: str = "MES"
    resolution: str = "5min"


class BackfillRequest(BaseModel):
    product_code: str = "MES"
    resolution: str = "5min"
    start: str = Field(description="YYYY-MM-DD")
    end: str = Field(description="YYYY-MM-DD")


class SchedulerStartRequest(BaseModel):
    targets: list[SyncRequest] = Field(default_factory=lambda: [SyncRequest()])
    interval_seconds: int = 300


class SchedulerStatusOut(BaseModel):
    running: bool
    targets: list[str] = Field(default_factory=list)
    last_cycle_at: Optional[str] = None
    last_result: Optional[str] = None
    last_error: Optional[str] = None
    cycles_completed: int = 0


# --- Phase 8B: autonomous research server ---

class ResearchServerStatusOut(BaseModel):
    """Nested sub-statuses are `dict[str, Any]` rather than fully modeled
    -- each one is already produced by its own owning module
    (`MarketDataScheduler.status()`, `AutonomousPaperTrader.status()`,
    `NightlyJobScheduler.status()`), the same "don't re-model an
    already-well-tested internal dict a second time" convention this
    file's own docstring states for `TradeStore` payloads."""
    running: bool
    started_at: Optional[str] = None
    uptime_seconds: Optional[float] = None
    data_scheduler: dict[str, Any]
    paper_trader: dict[str, Any]
    nightly_jobs: dict[str, Any]


# --- Lightweight user/organization accounts (Team Collaboration MVP) ---
# See accounts/store.py's module docstring: a data model plus basic CRUD,
# deliberately not an authentication system.

#: Fixed role vocabulary -- kept in sync with `accounts.ROLES` by hand
#: (four short string literals, not worth importing across the api/
#: boundary just to avoid repeating them).
RoleLiteral = Literal["owner", "admin", "member", "viewer"]


class OrganizationOut(BaseModel):
    id: str
    name: str
    created_at: str


class OrganizationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    id: str
    display_name: str
    username: str
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    org_id: str
    role: RoleLiteral
    created_at: str
    last_active_at: Optional[str] = None


class UserCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=100)
    org_id: str
    role: RoleLiteral
    email: Optional[str] = None
    avatar_url: Optional[str] = None


class UserUpdateRequest(BaseModel):
    """Every field optional -- a PATCH only touches what's actually
    supplied (see `AccountStore.update_user`'s identical "None means
    leave unchanged" contract)."""
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[RoleLiteral] = None
