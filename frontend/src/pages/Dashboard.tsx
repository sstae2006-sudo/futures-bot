import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { getInsights, getSystemOverview, listBacktests } from '../api'
import { useApi } from '../useApi'
import { LoadingState, ErrorState, StatTile, EmptyState } from '../components/UI'
import { money, num, pct, int, dateTime, tone } from '../format'
import type { RunSummary } from '../types'

type SortKey = 'strategy' | 'contract' | 'net_pnl' | 'profit_factor' | 'win_rate' | 'sharpe_ratio' | 'max_drawdown' | 'trade_count'

export default function Dashboard() {
  const overview = useApi(getSystemOverview)
  const backtests = useApi(() => listBacktests({ limit: 500 }))
  const insights = useApi(getInsights)

  const [sortKey, setSortKey] = useState<SortKey>('net_pnl')
  const [sortDir, setSortDir] = useState<1 | -1>(-1)

  const leaderboard = useMemo(() => {
    if (!backtests.data) return []
    const bestByStrategy = new Map<string, RunSummary>()
    for (const run of backtests.data) {
      if (run.status !== 'completed') continue
      const existing = bestByStrategy.get(run.strategy)
      const runPnl = Number(run.net_pnl ?? '-Infinity')
      const existingPnl = existing ? Number(existing.net_pnl ?? '-Infinity') : -Infinity
      if (!existing || runPnl > existingPnl) bestByStrategy.set(run.strategy, run)
    }
    const rows = Array.from(bestByStrategy.values())
    rows.sort((a, b) => {
      const av = sortKey === 'strategy' || sortKey === 'contract' ? a[sortKey] : Number(a[sortKey] ?? 0)
      const bv = sortKey === 'strategy' || sortKey === 'contract' ? b[sortKey] : Number(b[sortKey] ?? 0)
      if (av < bv) return -1 * sortDir
      if (av > bv) return 1 * sortDir
      return 0
    })
    return rows
  }, [backtests.data, sortKey, sortDir])

  function sortBy(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 1 ? -1 : 1))
    } else {
      setSortKey(key)
      setSortDir(-1)
    }
  }

  const columns: { key: SortKey; label: string }[] = [
    { key: 'strategy', label: 'Strategy' },
    { key: 'contract', label: 'Contract' },
    { key: 'net_pnl', label: 'Net P&L' },
    { key: 'profit_factor', label: 'Profit Factor' },
    { key: 'win_rate', label: 'Win Rate' },
    { key: 'sharpe_ratio', label: 'Sharpe' },
    { key: 'max_drawdown', label: 'Max DD' },
    { key: 'trade_count', label: 'Trades' },
  ]

  return (
    <div>
      <div className="page-header">
        <h1>Research Dashboard</h1>
        <p>System overview and best backtest per strategy so far.</p>
      </div>

      {overview.loading && <LoadingState label="Loading system overview…" />}
      {overview.error && <ErrorState message={overview.error} onRetry={overview.refetch} />}
      {overview.data && (
        <div className="grid grid-stats" style={{ marginBottom: 20 }}>
          <StatTile label="Version" value={overview.data.version} />
          <StatTile label="Strategies Available" value={overview.data.strategies_available.length}
            sub={overview.data.strategies_available.join(', ')} />
          <StatTile label="Backtests Run" value={int(overview.data.total_backtests)} />
          <StatTile label="Optimizer Runs" value={int(overview.data.total_optimizer_runs)} />
          <StatTile label="Trades Analyzed" value={int(overview.data.total_trades_analyzed)} />
          <StatTile label="Reports Generated" value={int(overview.data.total_reports_generated)} />
          <StatTile label="Last Optimization" value={overview.data.last_optimization_run ? dateTime(overview.data.last_optimization_run) : '—'} />
          <StatTile label="Database" value={overview.data.database_status} sub={overview.data.database_path} />
        </div>
      )}

      {insights.data && insights.data.length > 0 && (
        <>
          <div className="page-header">
            <h2>Research Insights</h2>
            <p>Data-derived summaries, not guesses — each one traces back to a specific number below.</p>
          </div>
          {insights.data.map((insight, i) => (
            <div key={i} className={`insight-card ${insight.severity}`}>
              <span style={{ flexShrink: 0 }}>{insight.severity === 'warning' ? '⚠' : 'ℹ'}</span>
              <span>{insight.message}</span>
            </div>
          ))}
        </>
      )}

      <div className="page-header">
        <h2>Strategy Leaderboard</h2>
        <Link to="/backtest" className="btn btn-secondary" style={{ textDecoration: 'none', display: 'inline-block' }}>
          Run a backtest
        </Link>
      </div>

      {backtests.loading && <LoadingState label="Loading backtest history…" />}
      {backtests.error && <ErrorState message={backtests.error} onRetry={backtests.refetch} />}
      {backtests.data && leaderboard.length === 0 && (
        <EmptyState label="No completed backtests yet. Run one from the Backtest Launcher." />
      )}
      {backtests.data && leaderboard.length > 0 && (
        <div className="panel">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {columns.map((c) => (
                    <th key={c.key} onClick={() => sortBy(c.key)}>
                      {c.label} {sortKey === c.key ? (sortDir === 1 ? '▲' : '▼') : ''}
                    </th>
                  ))}
                  <th>Validation</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {leaderboard.map((run) => (
                  <tr key={run.id}>
                    <td className="text-col">{run.strategy}</td>
                    <td>{run.contract}</td>
                    <td className={`tone-${tone(run.net_pnl)}`}>{money(run.net_pnl)}</td>
                    <td>{num(run.profit_factor)}</td>
                    <td>{pct(run.win_rate)}</td>
                    <td>{num(run.sharpe_ratio)}</td>
                    <td>{money(run.max_drawdown)}</td>
                    <td>{int(run.trade_count)}</td>
                    <td>{run.validation_net_pnl !== null ? money(run.validation_net_pnl) : '—'}</td>
                    <td>
                      <Link to={`/backtest/${run.id}`}>View</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
