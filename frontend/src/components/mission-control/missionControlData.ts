// Mission Control -- PLACEHOLDER DATA ONLY.
//
// This page is a design/scaffold pass (see the approved plan): every
// value below is a realistic mock, not a live reading. There is no
// backend endpoint for platform health/activity/alerts yet -- when one
// exists, this file is the single place to swap these constants for
// real `useApi(...)` calls (see ../../api.ts / ../../useApi.ts for the
// existing pattern every other page follows). Types are kept local to
// this directory rather than added to ../../types.ts because they don't
// correspond to a real API response yet; promote them once they do.
//
// The boot-sequence activity entries below are NOT invented -- they are
// the verbatim Write-Step/Write-Ok/Write-WarnLine messages from
// scripts/start.ps1, in the order that script actually emits them, so
// this page reflects how the platform really boots.

export type HealthStatus = 'operational' | 'degraded' | 'down' | 'unknown'

export interface ComponentHealth {
  name: string
  status: HealthStatus
  detail: string
  lastUpdate: string
}

export const overallHealth: { status: HealthStatus; score: number; breakdown: string } = {
  status: 'operational',
  score: 94,
  breakdown: '9 / 10 systems operational, 1 degraded',
}

export const componentHealth: ComponentHealth[] = [
  { name: 'Backend', status: 'operational', detail: 'FastAPI responding, 200 OK', lastUpdate: '4m ago' },
  { name: 'Frontend', status: 'operational', detail: 'Vite dev server responding', lastUpdate: '4m ago' },
  { name: 'API', status: 'operational', detail: 'p50 latency 42ms', lastUpdate: '1m ago' },
  { name: 'Database', status: 'operational', detail: 'market_data.db, 1.2 GB', lastUpdate: '4m ago' },
  { name: 'Research Database', status: 'operational', detail: 'research.db, schema current', lastUpdate: '4m ago' },
  { name: 'Context Engine', status: 'operational', detail: 'OFF mode -- no active caller yet', lastUpdate: '18m ago' },
  { name: 'Risk Engine', status: 'operational', detail: 'Kill switch armed, 0 halts today', lastUpdate: '1m ago' },
  { name: 'Paper Trading', status: 'unknown', detail: 'No session running', lastUpdate: '—' },
  { name: 'Live Trading', status: 'unknown', detail: 'No session running', lastUpdate: '—' },
  { name: 'AI Services', status: 'degraded', detail: '1 model archived, 0 deployed', lastUpdate: '2h ago' },
]

export type ActivityTone = 'good' | 'bad' | 'warn' | 'neutral'

export interface ActivityEvent {
  title: string
  detail?: string
  time: string
  tone: ActivityTone
}

// Verbatim from scripts/start.ps1's Write-Step/Write-Ok sequence --
// oldest entries in this session (the feed below prepends more recent
// research activity ahead of these, newest-first).
const bootSequence: ActivityEvent[] = [
  { title: 'futures-bot startup: repository located', detail: 'Repository root: C:\\Users\\sstae\\Downloads\\futures-bot', time: '18m ago', tone: 'neutral' },
  { title: 'Python virtual environment activated', detail: 'Virtual environment active (.venv\\Scripts\\python.exe)', time: '18m ago', tone: 'neutral' },
  { title: 'Python dependencies up to date', detail: 'pip install -e . --quiet', time: '18m ago', tone: 'good' },
  { title: 'npm dependencies up to date', detail: 'npm install --no-fund --no-audit (frontend/)', time: '18m ago', tone: 'good' },
  { title: 'market_data.db found (1.2 GB)', time: '18m ago', tone: 'good' },
  { title: 'Ports 8000 / 5173 freed of old processes', detail: 'Stop-ProcessOnPort -> Wait-ForPortFree', time: '17m ago', tone: 'neutral' },
  { title: 'Backend process started', detail: 'python -m futures_bot.api', time: '17m ago', tone: 'good' },
  { title: 'Backend is responding', detail: 'GET /api/system/overview -> 200 OK', time: '17m ago', tone: 'good' },
  { title: 'Frontend launcher started', detail: 'vite.cmd --host 127.0.0.1', time: '17m ago', tone: 'good' },
  { title: 'Frontend is responding', detail: 'http://127.0.0.1:5173', time: '17m ago', tone: 'good' },
  { title: 'Browser opened', time: '17m ago', tone: 'neutral' },
  { title: 'futures-bot is running', detail: 'Backend: http://127.0.0.1:8000\nFrontend: http://127.0.0.1:5173\nDatabase: market_data.db found (1.2 GB)\nAPI: responding (200 OK)', time: '17m ago', tone: 'good' },
]

export const activityFeed: ActivityEvent[] = [
  { title: 'Database validated', detail: 'python -m futures_bot.cli --validate-db -- 2 known findings (ISSUE-004, ISSUE-005), no new issues', time: '3m ago', tone: 'warn' },
  { title: 'Backtest completed', detail: 'ema_crossover on MES, 2024-01-01..2024-06-30 -- net P&L +$1,240.50', time: '22m ago', tone: 'good' },
  { title: 'Research job finished', detail: 'Feature distribution build for vwap_reversion', time: '41m ago', tone: 'good' },
  { title: 'Optimization finished', detail: 'trend_pullback grid search, 48 trials, best profit factor 1.84', time: '1h ago', tone: 'good' },
  { title: 'Import completed', detail: 'Client trade import: 214 rows staged, 214 confirmed', time: '2h ago', tone: 'good' },
  { title: 'AI model archived', detail: 'vwap_reversion classifier v3 -- superseded by v4 candidate (not yet deployed)', time: '2h ago', tone: 'warn' },
  ...bootSequence,
]

export type AlertSeverity = 'critical' | 'warning' | 'info'

export interface AlertItem {
  message: string
  time: string
}

export const alerts: Record<AlertSeverity, AlertItem[]> = {
  critical: [],
  warning: [
    { message: 'ISSUE-004: bars schema drift -- needs an explicit migration, approval pending', time: '—' },
    { message: 'AI Services degraded: 0 models currently deployed to any strategy', time: '2h ago' },
  ],
  info: [
    { message: 'Database validated with 2 known, previously-diagnosed findings (no new issues)', time: '3m ago' },
    { message: 'Context Engine running in OFF mode -- no strategy has opted in yet', time: '18m ago' },
  ],
}

export const researchSummary = {
  experiments: 12,
  strategies: 4,
  completedBacktests: 187,
  optimizationJobs: 23,
  bestStrategy: 'trend_pullback',
  latestResult: 'ema_crossover -- +$1,240.50 (22m ago)',
  avgProfitFactor: 1.42,
  avgExpectancy: 18.6,
}

export const databaseSummary = {
  barsStored: '4.8M',
  symbols: 6,
  timeframes: 4,
  databaseSize: '1.2 GB',
  validationStatus: '2 known findings',
  duplicates: 0,
  missingData: '2 gaps (US80Z)',
  lastSync: '6h ago',
}

export const marketContextSummary = {
  regime: 'RANGING',
  session: 'MORNING_SESSION',
  trend: 'NEUTRAL',
  liquidity: 'NORMAL',
  volatility: 'NORMAL',
  environmentScore: 58,
  distribution: [
    { label: 'Ranging', pct: 46, tone: 'neutral' as const },
    { label: 'Trending', pct: 31, tone: 'good' as const },
    { label: 'High Vol', pct: 14, tone: 'warn' as const },
    { label: 'Low Vol', pct: 9, tone: 'neutral' as const },
  ],
}

export const performance = {
  cpu: '12%',
  memory: '840 MB',
  apiLatency: '42ms',
  dbQueryTime: '6ms',
  runningJobs: 0,
  backgroundTasks: 1,
}

export const roadmap = {
  currentMilestone: 'Market Context Engine -- Platform Verification Phase 2 complete',
  nextPriority: 'Per-strategy Market Context adoption (uses_context opt-in, evaluated via backtest/context_comparison.py)',
  upcoming: ['Walk-forward testing', 'Monte Carlo simulation', 'Parameter robustness analysis'],
  completionPct: 72,
}
