// Mirrors futures_bot/api/schemas.py. Kept as plain interfaces (no runtime
// validation library) -- the backend already validates every response
// shape via Pydantic; duplicating that validation here would be a second
// place for the two to drift out of sync for no real safety benefit, since
// this frontend only ever talks to its own backend, not third-party input.

export interface StrategyParam {
  name: string
  type: 'int' | 'number' | 'boolean' | 'string'
  default: unknown
  description: string | null
}

export interface StrategyInfo {
  name: string
  parameters: StrategyParam[]
}

export interface DatasetInfo {
  filename: string
  size_bytes: number
  bars_hint: number | null
}

export interface RunSummary {
  id: string
  kind: 'backtest' | 'walk_forward' | 'optimizer' | 'compare'
  status: 'running' | 'completed' | 'failed'
  strategy: string
  contract: string
  trade_count: number | null
  net_pnl: string | null
  profit_factor: string | null
  win_rate: string | null
  sharpe_ratio: string | null
  max_drawdown: string | null
  validation_net_pnl: string | null
  walk_forward: boolean
  created_at: string
  completed_at: string | null
}

export interface RunDetail extends RunSummary {
  strategy_params: Record<string, unknown>
  csv_path: string | null
  expectancy: string | null
  sortino_ratio: string | null
  max_drawdown_pct: string | null
  validation_trade_count: number | null
  validation_profit_factor: string | null
  caveats: string[]
  first_bar: string | null
  last_bar: string | null
  error_message: string | null
}

export interface TradeOut {
  id: number
  run_id: string
  contract: string
  strategy: string
  strategy_params: Record<string, unknown>
  entry_time: string
  exit_time: string
  side: string
  entry_price: string
  exit_price: string
  gross_pnl: string
  commission: string
  net_pnl: string
  holding_minutes: number
  exit_reason: string
  session_date: string
  day_of_week: string
  hour: number
  entry_reason: string
  entry_metadata: Record<string, unknown>
  outcome: string
  mfe_points: string | null
  mae_points: string | null
  efficiency: string | null
  regime_trend: string | null
  regime_volatility: string | null
  regime_session: string | null
  trade_id: string | null
  stop_loss: string | null
  take_profit: string | null
  entry_slippage: string
  exit_slippage: string
}

export interface EquityPoint {
  trade_number: number
  timestamp: string | null
  equity: string
}

export interface DrawdownPoint {
  trade_number: number
  timestamp: string | null
  drawdown: string
}

export interface PerformanceOut {
  run_id: string
  equity_curve: EquityPoint[]
  drawdown_curve: DrawdownPoint[]
  max_drawdown: string
  max_drawdown_pct: string | null
  longest_drawdown_trades: number
  max_consecutive_losses: number
  max_consecutive_wins: number
  wins: number
  losses: number
  average_win: string | null
  average_loss: string | null
  expectancy: string | null
  average_r_multiple: string | null
  average_holding_minutes: number | null
}

export interface CompareEntryOut {
  strategy: string
  net_pnl: string
  profit_factor: string | null
  win_rate: string | null
  sharpe_ratio: string | null
  max_drawdown: string
  trade_count: number
  equity_curve: EquityPoint[]
}

export interface CompareResult {
  entries: CompareEntryOut[]
}

export interface TrialOut {
  rank: number | null
  params: Record<string, unknown>
  train_trades: number
  train_net_pnl: string
  train_profit_factor: string | null
  train_max_drawdown: string
  validation_trades: number | null
  validation_net_pnl: string | null
  validation_profit_factor: string | null
  validation_max_drawdown: string | null
}

export interface OptimizerResultOut {
  batch_id: string
  strategy: string
  combos_tried: number
  ranked_trials: TrialOut[]
  confidence: string | null
  warnings: string[]
}

export interface OverfitVerdict {
  level: 'green' | 'yellow' | 'red'
  label: string
  reasons: string[]
}

export interface ReportOut {
  id: string
  run_id: string
  format: string
  path: string
  created_at: string
}

export interface SystemOverview {
  version: string
  strategies_available: string[]
  total_backtests: number
  total_optimizer_runs: number
  total_trades_analyzed: number
  total_reports_generated: number
  last_optimization_run: string | null
  last_report_generated: string | null
  database_path: string
  database_status: string
}

export interface DatabaseHealth {
  configured: boolean
  ok: boolean
  latency_ms: number | null
  error: string | null
}

/** `/api/system/health` -- see api/schemas.py::SystemHealthOut. Only
 * StatusBar/HealthGrid's health-related fields consume this (per the
 * team-deployment plan's scope); everything else on Mission Control stays
 * mock data from missionControlData.ts. */
export interface SystemHealth {
  status: 'ok'
  version: string
  environment: 'development' | 'team' | 'production'
  uptime_seconds: number
  database: DatabaseHealth
  last_backup_at: string | null
  connected_users: number
}

export interface LogEntry {
  timestamp: string
  level: string
  kind: string
  message: string
}

export interface MlDatasetInfo {
  trade_count: number
  feature_columns: string[]
  labels: Record<string, Record<string, number>>
  export_status: string
}

export interface ApiErrorBody {
  detail: string
}

// --- Phase 6B ---

export interface JobOut {
  id: string
  kind:
    | 'backtest' | 'walk_forward' | 'optimizer' | 'compare' | 'report'
    | 'model_training' | 'ai_backtest_compare' | 'client_import' | 'params_comparison'
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress_current: number
  progress_total: number
  progress_message: string | null
  request: Record<string, unknown>
  result_id: string | null
  result_payload: Record<string, unknown> | null
  error_message: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface LivePosition {
  side: string
  quantity: number
  entry_price: string
  stop_loss: string | null
  take_profit: string | null
  unrealized_pnl: string
}

export interface LiveSessionStatus {
  status: 'stopped' | 'starting' | 'running' | 'stopping' | 'error'
  run_id: string | null
  strategy: string | null
  contract: string | null
  broker: string | null
  live_symbol: string | null
  resolution: string | null
  poll_seconds: number | null
  position: LivePosition | null
  session_pnl: string | null
  trade_count_today: number | null
  halted: boolean
  halt_reason: string | null
  last_bar_time: string | null
  last_bar_close: string | null
  last_feed_error: string | null
  error_message: string | null
  started_at: string | null
  stopped_at: string | null
  warnings: string[]
}

// --- Phase 8A: market-data pipeline ---

export interface ProductCoverage {
  product_code: string
  contracts_stored: string[]
  bars_stored: number
  earliest: string | null
  latest: string | null
  open_gaps: number
}

export interface ContractRoll {
  product_code: string
  from_contract: string | null
  to_contract: string
  rolled_at: string
}

export interface MarketDataOverview {
  total_bars: number
  products: ProductCoverage[]
  total_open_gaps: number
  database_path: string
  database_size_bytes: number
  last_sync_at: string | null
  last_sync_status: string | null
  recent_rolls: ContractRoll[]
  scheduler_running: boolean
}

export interface SyncRunOut {
  id: string
  product_code: string
  resolution: string
  kind: 'backfill' | 'incremental' | 'repair'
  status: 'running' | 'completed' | 'failed'
  bars_fetched: number
  error_message: string | null
  started_at: string
  completed_at: string | null
}

export interface GapOut {
  id: number
  product_code: string
  resolution: string
  gap_start: string
  gap_end: string
  detected_at: string
  resolved_at: string | null
}

export interface SchedulerStatus {
  running: boolean
  targets: string[]
  last_cycle_at: string | null
  last_result: string | null
  last_error: string | null
  cycles_completed: number
}

// --- Phase 8B: autonomous research server ---

export interface PaperStrategyStatus {
  status: 'starting' | 'running' | 'stopping' | 'stopped' | 'error'
  run_id: string | null
  strategy: string
  position: LivePosition | null
  session_pnl: string | null
  trade_count_today: number | null
  halted: boolean
  halt_reason: string | null
  error_message: string | null
}

export interface PaperTraderStatus {
  running: boolean
  live_symbol: string | null
  last_feed_error: string | null
  strategies: Record<string, PaperStrategyStatus>
}

export interface NightlyJobsStatus {
  running: boolean
  last_run_date: string | null
  last_run_summary: string | null
  last_error: string | null
}

export interface ResearchServerStatus {
  running: boolean
  started_at: string | null
  uptime_seconds: number | null
  data_scheduler: SchedulerStatus
  paper_trader: PaperTraderStatus
  nightly_jobs: NightlyJobsStatus
}

export interface TradeAnalyticsSummary {
  best_entries: TradeOut[]
  poor_exits: TradeOut[]
  missed_opportunities: TradeOut[]
}

export interface RegimeBucket {
  value: string
  trade_count: number
  net_pnl: string
  win_rate: string | null
  average_efficiency: string | null
}

export interface RegimePerformanceOut {
  strategy: string | null
  trend: RegimeBucket[]
  volatility: RegimeBucket[]
  session: RegimeBucket[]
}

export interface ExperimentOut {
  id: string
  name: string
  hypothesis: string
  strategy: string
  dataset: string | null
  parameters: Record<string, unknown>
  run_id: string | null
  notes: string | null
  model_id: string | null
  dataset_version: string | null
  metrics: Record<string, unknown> | null
  created_at: string
}

export interface InsightOut {
  strategy: string | null
  category: string
  message: string
  severity: 'info' | 'warning'
  /** Structured payload behind the finding -- e.g. a 'recommendation'
   * carries run_id/current_params/recommended_params/train_net_pnl/
   * is_deployable (Phase 10.2). Shape varies by category. */
  details: Record<string, unknown> | null
}

// --- Phase 10.2: research-server finding deploy/rollback/test ---

export interface ConfigDeploymentOut {
  id: string
  strategy: string
  action: 'deploy' | 'rollback'
  params: Record<string, unknown>
  backup_path: string | null
  created_at: string
}

export interface ParamsComparisonResult {
  current: RunSummary
  recommended: RunSummary
  improvement_pct: number | null
  trade_count_retained: number
  trade_count_retained_pct: number | null
  pnl_improvement: string | null
  profit_factor_improvement: string | null
  expectancy_improvement: string | null
  drawdown_reduction: string | null
}

// --- Phase 9: ML research workstation ---

export type ModelType = 'logistic_regression' | 'random_forest' | 'xgboost' | 'neural_network'
export type EvaluationMode = 'chronological_split' | 'walk_forward'
export type ModelStatus = 'queued' | 'training' | 'finished' | 'failed' | 'stopped'

export interface ClassificationMetrics {
  accuracy: number
  precision: number
  recall: number
  f1: number
  roc_auc: number | null
  trade_count: number
  fold_count?: number
}

export interface ModelDiagnostics {
  confusion_matrix: { tn: number; fp: number; fn: number; tp: number }
  roc_curve: { fpr: number[]; tpr: number[] } | null
  pr_curve: { precision: number[]; recall: number[] } | null
  calibration_curve: { predicted: number[]; actual: number[] } | null
}

/** Namespaced deliberately (never a flat merged dict) so in-sample and
 * out-of-sample numbers can never be displayed interchangeably -- mirrors
 * `ml_models.metrics`'s shape on the backend exactly. */
export interface ModelMetrics {
  train: ClassificationMetrics
  validation?: ClassificationMetrics
  test?: ClassificationMetrics | null
  walk_forward_out_of_sample?: ClassificationMetrics
  diagnostics?: ModelDiagnostics
  r_multiple_baseline?: { avg_win_r: number | null; avg_loss_r: number | null }
  training_seconds?: number
}

export interface FeatureImportanceRow {
  feature: string
  importance: number
}

export interface DerivedBacktestMetrics {
  without_ai: RunSummary
  with_ai: RunSummary
  trades_filtered: number
  trade_count_retained: number
  trade_count_retained_pct: number | null
  improvement_pct: number | null
  pnl_improvement: string | null
  profit_factor_improvement: string | null
  expectancy_improvement: string | null
  drawdown_reduction: string | null
}

export interface MlModelOut {
  id: string
  model_family: string
  version: number
  strategy: string
  model_type: ModelType
  status: ModelStatus
  job_id: string | null
  feature_columns: string[]
  hyperparameters: Record<string, unknown>
  evaluation_mode: EvaluationMode
  dataset_size: number
  dataset_version: string
  metrics: ModelMetrics | null
  feature_importance: FeatureImportanceRow[] | null
  overfit_warning: boolean
  overfit_note: string | null
  derived_backtest_metrics: DerivedBacktestMetrics | null
  artifact_path: string | null
  app_version: string | null
  git_commit: string | null
  notes: string | null
  archived: boolean
  error_message: string | null
  created_at: string
  completed_at: string | null
}

export interface ModelTrainResponse {
  job_id: string
  model_id: string
}

export interface CorrelationRow {
  feature: string
  corr_vs_win: number | null
  corr_vs_pnl: number | null
  corr_vs_r: number | null
}

export interface DatasetHealth {
  status: 'READY' | 'WARNING' | 'NOT_ENOUGH_DATA'
  reasons: string[]
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: number | null
  date_range: [string, string] | null
  feature_count: number
  missing_value_count: number
  missing_value_ratio: number
  total_rows: number
  unique_timestamps: number
  duplicate_market_events: number
}

export interface FeatureDistribution {
  feature: string
  bins: number[]
  counts: number[]
  win_counts: number[]
  loss_counts: number[]
  mean: number | null
  std: number | null
  min: number | null
  max: number | null
  win_average: number | null
  loss_average: number | null
}

export interface PredictionResult {
  model_id: string
  probability: number
  confidence: number
  expected_win_probability: number
  expected_value_r: number | null
  similar_trade_count: number
  similar_trade_win_rate: number | null
  calibration_bucket: { predicted: number; actual_win_rate: number } | null
  top_reasons: { feature: string; value: number; contribution: number }[]
}

export interface DeploymentOut {
  id: string
  strategy: string
  model_id: string | null
  action: 'deploy' | 'rollback' | 'undeploy'
  created_at: string
}

export interface DeploymentStatus {
  strategy: string
  current: DeploymentOut | null
  history: DeploymentOut[]
}

// --- Phase 10.1: universal client trade importer ---

export interface ClientProfile {
  id: string
  name: string
  notes: string | null
  created_at: string
}

export interface ImportFillPreviewRow {
  row: number
  timestamp: string
  symbol: string
  side: string
  quantity: string
  price: string
  commission: string
  realized_pnl: string | null
}

export interface ImportTradePreviewRow {
  entry_time: string
  exit_time: string
  symbol: string
  side: string
  quantity: string
  entry_price: string
  exit_price: string
}

export interface ImportError {
  row: number
  message: string
}

export type DetectedImportFormat = 'tradovate' | 'ninjatrader' | 'generic'

export interface ImportUploadResponse {
  import_id: string
  profile_id: string
  filename: string
  detected_format: DetectedImportFormat
  raw_headers: string[]
  suggested_mapping: Record<string, string | null>
  total_rows: number
  duplicate_count: number
  error_count: number
  matched_trade_count: number
  errors: ImportError[]
  warnings: string[]
  preview_fill_rows: ImportFillPreviewRow[]
  preview_trades: ImportTradePreviewRow[]
}

export interface ImportHistoryOut {
  id: string
  profile_id: string
  filename: string
  detected_format: string
  status: 'completed' | 'failed'
  total_fill_rows: number
  imported_fill_count: number
  duplicate_fill_count: number
  error_count: number
  trades_created: number
  errors: ImportError[]
  warnings: string[]
  job_id: string | null
  error_message: string | null
  created_at: string
}

// --- Lightweight user/organization accounts (Team Collaboration MVP) ---
// See src/futures_bot/accounts/store.py's docstring: a data model plus
// basic CRUD, deliberately not an authentication system.

export type UserRole = 'owner' | 'admin' | 'member' | 'viewer'

export interface Organization {
  id: string
  name: string
  created_at: string
}

export interface User {
  id: string
  display_name: string
  username: string
  email: string | null
  avatar_url: string | null
  org_id: string
  role: UserRole
  created_at: string
  last_active_at: string | null
}

export interface Infrastructure {
  cpu_percent: number
  memory_used_mb: number
  memory_total_mb: number
  memory_percent: number
  disk_used_gb: number
  disk_total_gb: number
  disk_percent: number
  jobs_queued: number
  jobs_running: number
}
