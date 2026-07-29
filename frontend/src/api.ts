// Thin fetch wrapper over the research API. Every function here mirrors one
// backend route one-to-one (see futures_bot/api/routes/) -- no business
// logic lives here, matching the project-wide rule that the frontend only
// ever consumes the API, never reimplements trading logic.

import type {
  AutomationStatus,
  ClientProfile,
  CompareResult,
  ConfigDeploymentOut,
  CorrelationRow,
  DatasetHealth,
  DatasetInfo,
  DeploymentStatus,
  DeploymentOut,
  EvaluationMode,
  ExperimentOut,
  FeatureDistribution,
  GapOut,
  ImportHistoryOut,
  ImportUploadResponse,
  Infrastructure,
  InsightOut,
  JobOut,
  LiveSessionStatus,
  LogEntry,
  MarketDataOverview,
  MlDatasetInfo,
  MlModelOut,
  ModelTrainResponse,
  BranchInfo,
  ConflictPair,
  ManualWorkItemStatus,
  ModelType,
  OptimizerResultOut,
  Organization,
  OverfitVerdict,
  OverlapWarning,
  OverlapWarningV2,
  OwnerType,
  PerformanceOut,
  PredictionResult,
  RegimePerformanceOut,
  ReportOut,
  ResearchServerStatus,
  RunDetail,
  RunSummary,
  SchedulerStatus,
  StrategyInfo,
  SyncRunOut,
  SystemHealth,
  SystemOverview,
  TimelineEntry,
  TradeAnalyticsSummary,
  TradeOut,
  TrialOut,
  NotificationPreferences,
  User,
  UserMe,
  UserRole,
  WorkItem,
  WorkItemActivity,
  WorkItemCreated,
  WorkItemPriority,
} from './types'

// Local Mode (scripts/start.ps1) runs the frontend as a separate Vite dev
// server on :5173 with the API on :8000 -- a genuinely different origin,
// so it needs an absolute fallback. Team Mode (scripts/start-team.ps1)
// builds the frontend once and serves it as static files from the same
// FastAPI process/origin as the API, so a relative path is always
// correct there and must be the default for a production build -- it
// must NOT depend on VITE_API_BASE_URL having been set to an empty
// string at build time. That was the previous approach (`??
// 'http://127.0.0.1:8000'`, relying on start-team.ps1 setting
// `$env:VITE_API_BASE_URL = ""` before `npm run build`) and it doesn't
// reliably survive the PowerShell -> npm.cmd -> cmd.exe -> vite child-
// process chain on Windows: an empty-string env var can come out the
// other end looking unset, silently falling back to the loopback
// default in a production build and breaking every teammate's API calls
// (confirmed directly -- inspecting the built bundle showed the literal
// `http://127.0.0.1:8000` baked in despite the empty-string env var
// having been set). `import.meta.env.DEV`/`PROD` are compiled in by
// Vite itself (true booleans, not a round-tripped string), so basing the
// default on that instead is immune to the same class of bug. An
// explicit `VITE_API_BASE_URL` still overrides either default when
// actually present. Extracted as a pure function (rather than inlined
// into the constant below) purely so a test can exercise both branches
// directly instead of fighting with re-stubbing `import.meta.env` and
// re-importing this module for each case.
export function resolveApiBase(explicitBaseUrl: string | undefined, isDev: boolean): string {
  return explicitBaseUrl ?? (isDev ? 'http://127.0.0.1:8000' : '')
}

export const API_BASE = resolveApiBase(import.meta.env.VITE_API_BASE_URL, import.meta.env.DEV)

export class ApiRequestError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new ApiRequestError(0, `Could not reach the research API at ${API_BASE}. Is it running?`)
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      // response body wasn't JSON -- fall back to statusText
    }
    throw new ApiRequestError(res.status, detail)
  }
  return res.json() as Promise<T>
}

// --- System ---
export const getSystemOverview = () => request<SystemOverview>('/api/system/overview')
export const getSystemHealth = () => request<SystemHealth>('/api/system/health')
export const getInfrastructure = () => request<Infrastructure>('/api/system/infrastructure')
export const getLogs = (params: { limit?: number; kind?: string } = {}) => {
  const qs = new URLSearchParams()
  if (params.limit) qs.set('limit', String(params.limit))
  if (params.kind) qs.set('kind', params.kind)
  return request<LogEntry[]>(`/api/logs?${qs.toString()}`)
}

// --- Strategies / datasets ---
export const listStrategies = () => request<StrategyInfo[]>('/api/strategies')
export const getStrategy = (name: string) => request<StrategyInfo>(`/api/strategies/${encodeURIComponent(name)}`)
export const listDatasets = () => request<DatasetInfo[]>('/api/datasets')

// --- Backtests ---
export interface BacktestRunRequest {
  strategy_name: string
  dataset: string
  contract?: string
  strategy_params?: Record<string, unknown>
  walk_forward?: boolean
  start?: string
  end?: string
  starting_cash?: number
  stop_loss_points?: number
  take_profit_points?: number
  contracts_per_trade?: number
  daily_max_loss?: number
  max_trades_per_session?: number
  ml_model_id?: string | null
  ml_min_win_probability?: number
}

export const runBacktest = (body: BacktestRunRequest) =>
  request<RunDetail>('/api/backtest/run', { method: 'POST', body: JSON.stringify(body) })

export const listBacktests = (params: { strategy?: string; limit?: number } = {}) => {
  const qs = new URLSearchParams()
  if (params.strategy) qs.set('strategy', params.strategy)
  if (params.limit) qs.set('limit', String(params.limit))
  return request<RunSummary[]>(`/api/backtests?${qs.toString()}`)
}

export const getBacktest = (runId: string) => request<RunDetail>(`/api/backtests/${runId}`)

// --- Trades / performance ---
export const listTrades = (
  params: { run_id?: string; strategy?: string; side?: string; outcome?: string } = {},
) => {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => v && qs.set(k, v))
  return request<TradeOut[]>(`/api/trades?${qs.toString()}`)
}

export const getPerformance = (runId: string) => request<PerformanceOut>(`/api/performance/${runId}`)

// --- Compare ---
export interface CompareRequest {
  dataset: string
  contract?: string
  strategy_names?: string[]
  start?: string
  end?: string
}

export const runCompare = (body: CompareRequest) =>
  request<CompareResult>('/api/compare/run', { method: 'POST', body: JSON.stringify(body) })

// --- Optimizer ---
export interface OptimizerRunRequest {
  strategy_name: string
  dataset: string
  contract?: string
  param_grid: Record<string, unknown>
  top_n?: number
  rolling?: boolean
}

export const runOptimizer = (body: OptimizerRunRequest) =>
  request<OptimizerResultOut>('/api/optimizer/run', { method: 'POST', body: JSON.stringify(body) })

export const getOptimizerResults = (batchId: string) => request<TrialOut[]>(`/api/optimizer/results/${batchId}`)

export const getOverfitVerdict = (runId: string) => request<OverfitVerdict>(`/api/walk-forward/${runId}/verdict`)

// --- Reports ---
export const generateReport = (runId: string) =>
  request<ReportOut>('/api/report/generate', { method: 'POST', body: JSON.stringify({ run_id: runId }) })

export const listReports = (runId?: string) =>
  request<ReportOut[]>(`/api/reports${runId ? `?run_id=${runId}` : ''}`)

export const reportViewUrl = (reportId: string) => `${API_BASE}/api/reports/${reportId}/view`

// --- ML ---
export const getMlDatasetInfo = () => request<MlDatasetInfo>('/api/ml/dataset')

// --- Phase 6B: trade analytics / regime ---
export const getTradeAnalytics = (params: { run_id?: string; strategy?: string; top_n?: number } = {}) => {
  const qs = new URLSearchParams()
  if (params.run_id) qs.set('run_id', params.run_id)
  if (params.strategy) qs.set('strategy', params.strategy)
  if (params.top_n) qs.set('top_n', String(params.top_n))
  return request<TradeAnalyticsSummary>(`/api/trades/analytics?${qs.toString()}`)
}

export const getRegimePerformance = (strategy?: string) =>
  request<RegimePerformanceOut>(`/api/regime/performance${strategy ? `?strategy=${strategy}` : ''}`)

// --- Phase 6B: background jobs ---
export const submitBacktestJob = (body: BacktestRunRequest) =>
  request<JobOut>('/api/jobs/backtest', { method: 'POST', body: JSON.stringify(body) })

export const submitOptimizerJob = (body: OptimizerRunRequest) =>
  request<JobOut>('/api/jobs/optimizer', { method: 'POST', body: JSON.stringify(body) })

export const submitCompareJob = (body: CompareRequest) =>
  request<JobOut>('/api/jobs/compare', { method: 'POST', body: JSON.stringify(body) })

export const submitReportJob = (runId: string) =>
  request<JobOut>('/api/jobs/report', { method: 'POST', body: JSON.stringify({ run_id: runId }) })

export const listJobs = (params: { status?: string; limit?: number } = {}) => {
  const qs = new URLSearchParams()
  if (params.status) qs.set('status', params.status)
  if (params.limit) qs.set('limit', String(params.limit))
  return request<JobOut[]>(`/api/jobs?${qs.toString()}`)
}

export const getJob = (jobId: string) => request<JobOut>(`/api/jobs/${jobId}`)

/** Opens an SSE stream for one job's progress. Returns the `EventSource` so
 * the caller can close it (e.g. on component unmount) -- `onUpdate` fires
 * once per frame, `onDone` once the job reaches a terminal state (the
 * server closes the stream at that point too). */
export function streamJob(
  jobId: string, onUpdate: (job: JobOut) => void, onDone?: (job: JobOut) => void,
): EventSource {
  const source = new EventSource(`${API_BASE}/api/jobs/${jobId}/stream`)
  source.onmessage = (event) => {
    const job = JSON.parse(event.data) as JobOut
    onUpdate(job)
    if (job.status === 'completed' || job.status === 'failed') {
      onDone?.(job)
      source.close()
    }
  }
  source.onerror = () => {
    // The server closes the connection normally once the job finishes,
    // which reports `readyState === CLOSED` here -- nothing to reconnect
    // to, so closing (a no-op at that point) is fine. Any other error
    // (a transient network blip, a proxy hiccup) leaves `readyState` at
    // `CONNECTING`: the browser's built-in EventSource retry is already
    // under way, and calling `close()` unconditionally here would kill
    // that recovery path for a job that's still running server-side,
    // leaving the UI stuck on stale progress with no way back short of
    // navigating away.
    if (source.readyState === EventSource.CLOSED) {
      source.close()
    }
  }
  return source
}

// --- Phase 6B: experiments ---
export interface ExperimentCreateRequest {
  name: string
  hypothesis: string
  strategy: string
  dataset?: string
  parameters?: Record<string, unknown>
  run_id?: string
  notes?: string
  model_id?: string
  dataset_version?: string
  metrics?: Record<string, unknown>
}

export const createExperiment = (body: ExperimentCreateRequest) =>
  request<ExperimentOut>('/api/experiments', { method: 'POST', body: JSON.stringify(body) })

export const listExperiments = (strategy?: string) =>
  request<ExperimentOut[]>(`/api/experiments${strategy ? `?strategy=${strategy}` : ''}`)

export const getExperiment = (experimentId: string) => request<ExperimentOut>(`/api/experiments/${experimentId}`)

export const updateExperimentNotes = (experimentId: string, notes: string) =>
  request<ExperimentOut>(`/api/experiments/${experimentId}/notes`, {
    method: 'PATCH', body: JSON.stringify({ notes }),
  })

// --- Phase 6B: dashboard insights ---
export const getInsights = () => request<InsightOut[]>('/api/insights')

// --- Phase 8A: market-data pipeline ---
export interface SyncRequestBody {
  product_code: string
  resolution: string
}

export const getMarketDataOverview = () => request<MarketDataOverview>('/api/market-data/overview')

export const listSyncRuns = (params: { product_code?: string; limit?: number } = {}) => {
  const qs = new URLSearchParams()
  if (params.product_code) qs.set('product_code', params.product_code)
  if (params.limit) qs.set('limit', String(params.limit))
  return request<SyncRunOut[]>(`/api/market-data/runs?${qs.toString()}`)
}

export const listGaps = (productCode?: string) =>
  request<GapOut[]>(`/api/market-data/gaps${productCode ? `?product_code=${productCode}` : ''}`)

export const syncMarketDataNow = (body: SyncRequestBody) =>
  request<SyncRunOut>('/api/market-data/sync', { method: 'POST', body: JSON.stringify(body) })

export const backfillMarketData = (body: SyncRequestBody & { start: string; end: string }) =>
  request<SyncRunOut>('/api/market-data/backfill', { method: 'POST', body: JSON.stringify(body) })

export const verifyMarketData = (body: SyncRequestBody) =>
  request<Record<string, unknown>>('/api/market-data/verify', { method: 'POST', body: JSON.stringify(body) })

export const repairMarketDataGaps = (body: SyncRequestBody) =>
  request<Record<string, unknown>>('/api/market-data/repair', { method: 'POST', body: JSON.stringify(body) })

export const startMarketDataScheduler = (body: { targets: SyncRequestBody[]; interval_seconds: number }) =>
  request<SchedulerStatus>('/api/market-data/scheduler/start', { method: 'POST', body: JSON.stringify(body) })

export const stopMarketDataScheduler = () =>
  request<SchedulerStatus>('/api/market-data/scheduler/stop', { method: 'POST' })

export const getMarketDataSchedulerStatus = () => request<SchedulerStatus>('/api/market-data/scheduler/status')

// --- Phase 7: dashboard-controlled paper live session ---
export interface LiveSessionStartRequest {
  live_symbol: string
  resolution?: string
  poll_seconds?: number
}

export const startLiveSession = (body: LiveSessionStartRequest) =>
  request<LiveSessionStatus>('/api/live/start', { method: 'POST', body: JSON.stringify(body) })

export const stopLiveSession = () => request<LiveSessionStatus>('/api/live/stop', { method: 'POST' })

export const getLiveStatus = () => request<LiveSessionStatus>('/api/live/status')

/** Opens an SSE stream of live-session status, one frame per change --
 * unlike `streamJob`, this never reaches a guaranteed terminal state, so
 * the caller (not this function) decides when to close it. */
export function streamLiveStatus(onUpdate: (status: LiveSessionStatus) => void): EventSource {
  const source = new EventSource(`${API_BASE}/api/live/stream`)
  source.onmessage = (event) => {
    onUpdate(JSON.parse(event.data) as LiveSessionStatus)
  }
  return source
}

// --- Phase 8B: autonomous research server ---
export const getResearchServerStatus = () => request<ResearchServerStatus>('/api/research-server/status')

export const startResearchServer = () =>
  request<ResearchServerStatus>('/api/research-server/start', { method: 'POST' })

export const stopResearchServer = () =>
  request<ResearchServerStatus>('/api/research-server/stop', { method: 'POST' })

export const runNightlyBatchNow = () =>
  request<{ summary: string }>('/api/research-server/nightly/run-now', { method: 'POST' })

export const getResearchServerFindings = () => request<InsightOut[]>('/api/research-server/findings')

// --- Phase 10.2: deploy/rollback/test a research-server finding ---

export const deployFinding = (body: { strategy: string; params: Record<string, unknown>; run_id?: string }) =>
  request<ConfigDeploymentOut>('/api/research-server/insights/deploy', { method: 'POST', body: JSON.stringify(body) })

export const rollbackConfigDeployment = (deploymentId: string) =>
  request<ConfigDeploymentOut>(`/api/research-server/insights/config-deployments/${deploymentId}/rollback`, { method: 'POST' })

export const listConfigDeployments = (strategy?: string) =>
  request<ConfigDeploymentOut[]>(`/api/research-server/insights/config-deployments${strategy ? `?strategy=${encodeURIComponent(strategy)}` : ''}`)

export const testFindingParams = (body: { strategy: string; dataset: string; recommended_params: Record<string, unknown> }) =>
  request<JobOut>('/api/research-server/insights/test-params', { method: 'POST', body: JSON.stringify(body) })

// --- Phase 10.1: universal client trade importer ---

export const createClientProfile = (body: { name: string; notes?: string }) =>
  request<ClientProfile>('/api/imports/profiles', { method: 'POST', body: JSON.stringify(body) })

export const listClientProfiles = () => request<ClientProfile[]>('/api/imports/profiles')

/** The one place this project needs a non-JSON request body -- `request()`
 * always sends `Content-Type: application/json`, which would break a
 * multipart upload (the browser has to set its own boundary), so this
 * bypasses it with its own small fetch call following the same
 * `ApiRequestError` conventions. */
export async function uploadImportFile(profileId: string, file: File): Promise<ImportUploadResponse> {
  const form = new FormData()
  form.append('profile_id', profileId)
  form.append('file', file)

  let res: Response
  try {
    res = await fetch(`${API_BASE}/api/imports/upload`, { method: 'POST', body: form })
  } catch {
    throw new ApiRequestError(0, `Could not reach the research API at ${API_BASE}. Is it running?`)
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      // response body wasn't JSON -- fall back to statusText
    }
    throw new ApiRequestError(res.status, detail)
  }
  return res.json() as Promise<ImportUploadResponse>
}

export const confirmImport = (importId: string, mapping: Record<string, string | null | undefined> = {}) =>
  request<JobOut>(`/api/imports/${importId}/confirm`, { method: 'POST', body: JSON.stringify({ mapping }) })

export const cancelImportStaging = (importId: string) =>
  request<{ cancelled: boolean }>(`/api/imports/staging/${importId}`, { method: 'DELETE' })

export const listImportHistory = (params: { profile_id?: string; limit?: number } = {}) => {
  const qs = new URLSearchParams()
  if (params.profile_id) qs.set('profile_id', params.profile_id)
  if (params.limit) qs.set('limit', String(params.limit))
  return request<ImportHistoryOut[]>(`/api/imports/history?${qs.toString()}`)
}

// --- Phase 9: ML research workstation ---

export const getMlDatasetHealth = (strategy: string) =>
  request<DatasetHealth>(`/api/ml/dataset-health?strategy=${encodeURIComponent(strategy)}`)

export const getFeatureDistribution = (strategy: string, feature: string) =>
  request<FeatureDistribution>(
    `/api/ml/features/${encodeURIComponent(feature)}/distribution?strategy=${encodeURIComponent(strategy)}`,
  )

export const getCorrelation = (strategy: string) =>
  request<CorrelationRow[]>(`/api/ml/correlation?strategy=${encodeURIComponent(strategy)}`)

export const mlDatasetExportUrl = (strategy: string) =>
  `${API_BASE}/api/ml/dataset/export?strategy=${encodeURIComponent(strategy)}`

export interface ModelTrainRequest {
  strategy: string
  model_type: ModelType
  hyperparameters?: Record<string, unknown>
  evaluation_mode?: EvaluationMode
}

export const submitModelTraining = (body: ModelTrainRequest) =>
  request<ModelTrainResponse>('/api/ml/models', { method: 'POST', body: JSON.stringify(body) })

export const listModels = (params: { strategy?: string; model_family?: string; include_archived?: boolean } = {}) => {
  const qs = new URLSearchParams()
  if (params.strategy) qs.set('strategy', params.strategy)
  if (params.model_family) qs.set('model_family', params.model_family)
  if (params.include_archived) qs.set('include_archived', 'true')
  return request<MlModelOut[]>(`/api/ml/models?${qs.toString()}`)
}

export const getModel = (modelId: string) => request<MlModelOut>(`/api/ml/models/${modelId}`)

export const getModelVersions = (modelFamily: string) =>
  request<MlModelOut[]>(`/api/ml/models/family/${encodeURIComponent(modelFamily)}/versions`)

export const stopModel = (modelId: string) => request<MlModelOut>(`/api/ml/models/${modelId}/stop`, { method: 'POST' })

export const archiveModel = (modelId: string) => request<MlModelOut>(`/api/ml/models/${modelId}/archive`, { method: 'POST' })

export const unarchiveModel = (modelId: string) => request<MlModelOut>(`/api/ml/models/${modelId}/unarchive`, { method: 'POST' })

export const deleteModel = (modelId: string) => request<{ deleted: boolean }>(`/api/ml/models/${modelId}`, { method: 'DELETE' })

export const updateModelNotes = (modelId: string, notes: string) =>
  request<MlModelOut>(`/api/ml/models/${modelId}/notes`, { method: 'PATCH', body: JSON.stringify({ notes }) })

export const deployModel = (modelId: string) => request<DeploymentOut>(`/api/ml/models/${modelId}/deploy`, { method: 'POST' })

export const rollbackModel = (modelId: string, strategy: string) =>
  request<DeploymentOut>(`/api/ml/models/${modelId}/rollback?strategy=${encodeURIComponent(strategy)}`, { method: 'POST' })

export const getDeployment = (strategy: string) => request<DeploymentStatus>(`/api/strategies/${encodeURIComponent(strategy)}/deployment`)

export const computeModelBacktestMetrics = (modelId: string, dataset: string) =>
  request<Record<string, unknown>>(
    `/api/ml/models/${modelId}/backtest-metrics?dataset=${encodeURIComponent(dataset)}`, { method: 'POST' },
  )

export const predictTrade = (modelId: string, tradeId: number) =>
  request<PredictionResult>('/api/ml/predict', { method: 'POST', body: JSON.stringify({ model_id: modelId, trade_id: tradeId }) })

export interface AiBacktestComparisonRequest {
  backtest: BacktestRunRequest
  ml_model_id: string
  ml_min_win_probability?: number
}

export const submitAiBacktestComparison = (body: AiBacktestComparisonRequest) =>
  request<JobOut>('/api/jobs/ai-backtest-compare', { method: 'POST', body: JSON.stringify(body) })

// --- Lightweight user/organization accounts (Team Collaboration Platform) ---

export const createOrganization = (name: string) =>
  request<Organization>('/api/organizations', { method: 'POST', body: JSON.stringify({ name }) })

export const getOrganizations = () => request<Organization[]>('/api/organizations')

export const getOrganization = (orgId: string) => request<Organization>(`/api/organizations/${orgId}`)

export const updateOrganization = (orgId: string, name: string) =>
  request<Organization>(`/api/organizations/${orgId}`, { method: 'PATCH', body: JSON.stringify({ name }) })

export interface UserCreateRequest {
  display_name: string
  username: string
  org_id: string
  role: UserRole
  email?: string
  avatar_url?: string
}

export const createUser = (body: UserCreateRequest) =>
  request<UserMe>('/api/users', { method: 'POST', body: JSON.stringify(body) })

export const getUsers = (orgId?: string) =>
  request<User[]>(orgId ? `/api/users?org_id=${encodeURIComponent(orgId)}` : '/api/users')

export const getUser = (userId: string) => request<User>(`/api/users/${userId}`)

export const getUserMe = (userId: string) => request<UserMe>(`/api/users/${userId}/me`)

export interface UserUpdateRequest {
  display_name?: string
  email?: string
  avatar_url?: string
  role?: UserRole
  timezone?: string
  preferred_ai_model?: string
  default_branch_prefix?: string
  notification_preferences?: NotificationPreferences
}

export const updateUser = (userId: string, body: UserUpdateRequest) =>
  request<User>(`/api/users/${userId}`, { method: 'PATCH', body: JSON.stringify(body) })

export const regenerateApiKey = (userId: string) =>
  request<UserMe>(`/api/users/${userId}/regenerate-api-key`, { method: 'POST' })

export const sendUserHeartbeat = (userId: string) =>
  request<User>(`/api/users/${userId}/heartbeat`, { method: 'POST' })

// --- Active Work Registry (Team Collaboration MVP) ---

export interface WorkItemCreateRequest {
  title: string
  description?: string
  owner_user_id?: string
  owner_type?: OwnerType
  branch?: string
  estimated_files?: string[]
  priority?: WorkItemPriority
  org_id?: string
}

export const createWorkItem = (body: WorkItemCreateRequest) =>
  request<WorkItemCreated>('/api/work-items', { method: 'POST', body: JSON.stringify(body) })

export const getWorkItems = (status?: string, orgId?: string) => {
  const qs = new URLSearchParams()
  if (status) qs.set('status', status)
  if (orgId) qs.set('org_id', orgId)
  const query = qs.toString()
  return request<WorkItem[]>(`/api/work-items${query ? `?${query}` : ''}`)
}

export const getWorkItem = (itemId: string) => request<WorkItem>(`/api/work-items/${itemId}`)

export const getWorkItemOverlap = (itemId: string) =>
  request<OverlapWarning[]>(`/api/work-items/${itemId}/overlap`)

export const claimWorkItem = (itemId: string, userId: string) =>
  request<WorkItem>(`/api/work-items/${itemId}/claim`, { method: 'POST', body: JSON.stringify({ user_id: userId }) })

export const releaseWorkItem = (itemId: string) =>
  request<WorkItem>(`/api/work-items/${itemId}/release`, { method: 'POST' })

export const completeWorkItem = (itemId: string) =>
  request<WorkItem>(`/api/work-items/${itemId}/complete`, { method: 'POST' })

export const reassignWorkItem = (itemId: string, userId: string) =>
  request<WorkItem>(`/api/work-items/${itemId}/reassign`, { method: 'POST', body: JSON.stringify({ user_id: userId }) })

export const getWorkItemActivity = (workItemId?: string, limit = 100) => {
  const qs = new URLSearchParams()
  if (workItemId) qs.set('work_item_id', workItemId)
  qs.set('limit', String(limit))
  return request<WorkItemActivity[]>(`/api/work-items-activity?${qs.toString()}`)
}

export const updateWorkItemStatus = (itemId: string, status: ManualWorkItemStatus) =>
  request<WorkItem>(`/api/work-items/${itemId}/status`, { method: 'POST', body: JSON.stringify({ status }) })

export const getWorkItemOverlapV2 = (itemId: string) =>
  request<OverlapWarningV2[]>(`/api/work-items/${itemId}/overlap-v2`)

export const getWorkItemConflicts = (orgId?: string) =>
  request<ConflictPair[]>(orgId ? `/api/work-items/conflicts?org_id=${encodeURIComponent(orgId)}` : '/api/work-items/conflicts')

export const getBranchInfo = (branch?: string) =>
  request<BranchInfo>(branch ? `/api/git/branch-info?branch=${encodeURIComponent(branch)}` : '/api/git/branch-info')

export interface TimelineFilters {
  workItemId?: string
  eventType?: string
  q?: string
  since?: string
  until?: string
  includeCommits?: boolean
  limit?: number
}

export const getTimeline = (filters: TimelineFilters = {}) => {
  const qs = new URLSearchParams()
  if (filters.workItemId) qs.set('work_item_id', filters.workItemId)
  if (filters.eventType) qs.set('event_type', filters.eventType)
  if (filters.q) qs.set('q', filters.q)
  if (filters.since) qs.set('since', filters.since)
  if (filters.until) qs.set('until', filters.until)
  if (filters.includeCommits !== undefined) qs.set('include_commits', String(filters.includeCommits))
  qs.set('limit', String(filters.limit ?? 50))
  return request<TimelineEntry[]>(`/api/activity/timeline?${qs.toString()}`)
}

// SIL Phase 4: draft work items (background git-watcher output) + automation status.

export const getDraftWorkItems = (orgId?: string) =>
  request<WorkItem[]>(orgId ? `/api/work-items/drafts?org_id=${encodeURIComponent(orgId)}` : '/api/work-items/drafts')

export const approveDraftWorkItem = (itemId: string) =>
  request<WorkItem>(`/api/work-items/${itemId}/approve-draft`, { method: 'POST' })

export const discardDraftWorkItem = (itemId: string) =>
  request<{ discarded: boolean }>(`/api/work-items/${itemId}/draft`, { method: 'DELETE' })

export const getAutomationStatus = () => request<AutomationStatus>('/api/automation/status')
