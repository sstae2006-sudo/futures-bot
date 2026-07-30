import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { getMarketDataOverview, getSystemOverview, listExperiments } from '../../api'
import { useApi } from '../../useApi'
import { dateTime, money, num } from '../../format'

// KNOWN_ISSUES.md ISSUE-040 -- every card in this file used to render a
// hardcoded placeholder from missionControlData.ts (a fake avg profit
// factor of 1.42, a fake "187 completed backtests", etc.) with no
// relationship to any real backtest or database ever run. Both cards
// below now fetch real data. `MarketContextSummaryCard`/`PerformanceCard`
// (also previously fake) were removed outright rather than fixed:
// Context Engine has no live "current regime" reading to report (it's
// OFF by default, see docs/ARCHITECTURE.md; MarketRegime.tsx already
// covers real historical regime performance), and `PerformanceCard` was
// a redundant fake duplicate of the already-real `InfrastructurePanel`
// rendered right next to it on this same page.

function SummaryCard({ title, to, toLabel, children }: { title: string; to?: string; toLabel?: string; children: ReactNode }) {
  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>{title}</h3>
        {to && <Link to={to}>{toLabel ?? 'View →'}</Link>}
      </div>
      {children}
    </div>
  )
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="mc-summary-row">
      <span className="k">{label}</span>
      <span className="v">{value}</span>
    </div>
  )
}

export function ResearchSummaryCard() {
  const { data: overview } = useApi(getSystemOverview, [])
  const { data: experiments } = useApi(listExperiments, [])

  const latest = overview?.latest_backtest_strategy
    ? `${overview.latest_backtest_strategy} -- ${money(overview.latest_backtest_net_pnl)} (${dateTime(overview.latest_backtest_completed_at)})`
    : '—'

  return (
    <SummaryCard title="Research Summary" to="/experiments" toLabel="Experiments →">
      <Row label="Experiments" value={experiments?.length ?? '—'} />
      <Row label="Strategies" value={overview?.strategies_available.length ?? '—'} />
      <Row label="Completed Backtests" value={overview?.total_backtests ?? '—'} />
      <Row label="Optimization Jobs" value={overview?.total_optimizer_runs ?? '—'} />
      <Row label="Best Strategy" value={overview?.best_strategy ?? '—'} />
      <Row label="Latest Result" value={latest} />
      <Row label="Avg. Profit Factor" value={overview ? num(overview.avg_profit_factor) : '—'} />
      <Row label="Avg. Expectancy" value={overview ? money(overview.avg_expectancy) : '—'} />
    </SummaryCard>
  )
}

export function DatabaseSummaryCard() {
  const { data: overview } = useApi(getMarketDataOverview, [])
  const sizeGb = overview ? (overview.database_size_bytes / 1e9).toFixed(2) : null

  return (
    <SummaryCard title="Database Summary" to="/market-data" toLabel="Market Data →">
      <Row label="Bars Stored" value={overview ? overview.total_bars.toLocaleString() : '—'} />
      <Row label="Products" value={overview?.products.length ?? '—'} />
      <Row label="Open Gaps" value={overview?.total_open_gaps ?? '—'} />
      <Row label="Database Size" value={sizeGb ? `${sizeGb} GB` : '—'} />
      <Row label="Scheduler" value={overview ? (overview.scheduler_running ? 'Running' : 'Stopped') : '—'} />
      <Row label="Last Sync" value={overview ? dateTime(overview.last_sync_at) : '—'} />
      <Row label="Last Sync Status" value={overview?.last_sync_status ?? '—'} />
    </SummaryCard>
  )
}
