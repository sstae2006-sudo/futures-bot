import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { databaseSummary, marketContextSummary, performance, researchSummary } from './missionControlData'

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
  return (
    <SummaryCard title="Research Summary" to="/experiments" toLabel="Experiments →">
      <Row label="Experiments" value={researchSummary.experiments} />
      <Row label="Strategies" value={researchSummary.strategies} />
      <Row label="Completed Backtests" value={researchSummary.completedBacktests} />
      <Row label="Optimization Jobs" value={researchSummary.optimizationJobs} />
      <Row label="Best Strategy" value={researchSummary.bestStrategy} />
      <Row label="Latest Result" value={researchSummary.latestResult} />
      <Row label="Avg. Profit Factor" value={researchSummary.avgProfitFactor} />
      <Row label="Avg. Expectancy" value={`$${researchSummary.avgExpectancy}`} />
    </SummaryCard>
  )
}

export function DatabaseSummaryCard() {
  return (
    <SummaryCard title="Database Summary" to="/market-data" toLabel="Market Data →">
      <Row label="Bars Stored" value={databaseSummary.barsStored} />
      <Row label="Symbols" value={databaseSummary.symbols} />
      <Row label="Timeframes" value={databaseSummary.timeframes} />
      <Row label="Database Size" value={databaseSummary.databaseSize} />
      <Row label="Validation Status" value={databaseSummary.validationStatus} />
      <Row label="Duplicates" value={databaseSummary.duplicates} />
      <Row label="Missing Data" value={databaseSummary.missingData} />
      <Row label="Last Sync" value={databaseSummary.lastSync} />
    </SummaryCard>
  )
}

export function MarketContextSummaryCard() {
  const c = marketContextSummary
  return (
    <SummaryCard title="Market Context Summary" to="/regime" toLabel="Market Regime →">
      <Row label="Regime" value={c.regime} />
      <Row label="Session" value={c.session} />
      <Row label="Trend" value={c.trend} />
      <Row label="Liquidity" value={c.liquidity} />
      <Row label="Volatility" value={c.volatility} />
      <Row label="Environment Score" value={`${c.environmentScore} / 100`} />
      <div className="mc-context-distribution">
        {c.distribution.map((seg) => (
          <span
            key={seg.label}
            style={{
              width: `${seg.pct}%`,
              background: `var(--${seg.tone === 'neutral' ? 'text-faint' : seg.tone})`,
            }}
            title={`${seg.label} ${seg.pct}%`}
          />
        ))}
      </div>
      <div className="mc-context-legend">
        {c.distribution.map((seg) => (
          <span className="mc-context-legend-item" key={seg.label}>
            <span
              className="mc-context-legend-dot"
              style={{ background: `var(--${seg.tone === 'neutral' ? 'text-faint' : seg.tone})` }}
            />
            {seg.label} {seg.pct}%
          </span>
        ))}
      </div>
    </SummaryCard>
  )
}

export function PerformanceCard() {
  return (
    <SummaryCard title="Performance">
      <Row label="CPU" value={performance.cpu} />
      <Row label="Memory" value={performance.memory} />
      <Row label="API Latency" value={performance.apiLatency} />
      <Row label="DB Query Time" value={performance.dbQueryTime} />
      <Row label="Running Jobs" value={performance.runningJobs} />
      <Row label="Background Tasks" value={performance.backgroundTasks} />
    </SummaryCard>
  )
}
