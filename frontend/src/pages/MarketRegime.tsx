import { useEffect, useState } from 'react'
import { getRegimePerformance, listStrategies } from '../api'
import { useApi } from '../useApi'
import { LoadingState, ErrorState, EmptyState } from '../components/UI'
import { money, pct, int, tone } from '../format'
import type { RegimeBucket } from '../types'

export default function MarketRegime() {
  const strategies = useApi(listStrategies)
  const [strategy, setStrategy] = useState('')

  useEffect(() => {
    if (!strategy && strategies.data && strategies.data.length > 0) setStrategy(strategies.data[0].name)
  }, [strategies.data, strategy])

  const perf = useApi(() => getRegimePerformance(strategy || undefined), [strategy])

  return (
    <div>
      <div className="page-header">
        <h1>Market Regime Analysis</h1>
        <p>When does this strategy actually work? Performance grouped by trend, volatility, and session at entry time.</p>
      </div>

      {strategies.loading && <LoadingState label="Loading strategies…" />}
      {strategies.data && (
        <div className="panel">
          <div className="field" style={{ maxWidth: 260 }}>
            <label htmlFor="regime-strategy">Strategy</label>
            <select id="regime-strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {strategies.data.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>
        </div>
      )}

      {perf.loading && <LoadingState label="Loading regime performance…" />}
      {perf.error && <ErrorState message={perf.error} onRetry={perf.refetch} />}

      {perf.data && (
        <div className="grid grid-2">
          <RegimeTable title="By Trend" buckets={perf.data.trend} />
          <RegimeTable title="By Volatility" buckets={perf.data.volatility} />
          <div style={{ gridColumn: '1 / -1' }}>
            <RegimeTable title="By Session" buckets={perf.data.session} />
          </div>
        </div>
      )}
    </div>
  )
}

function RegimeTable({ title, buckets }: { title: string; buckets: RegimeBucket[] }) {
  return (
    <div className="panel">
      <h3>{title}</h3>
      {buckets.length === 0 ? (
        <EmptyState label="Not enough trades recorded yet for this strategy." />
      ) : (
        <table>
          <thead>
            <tr>
              <th>Condition</th>
              <th>Trades</th>
              <th>Net P&amp;L</th>
              <th>Win Rate</th>
              <th>Avg Efficiency</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((b) => (
              <tr key={b.value}>
                <td className="text-col">{b.value}</td>
                <td>{int(b.trade_count)}</td>
                <td className={`tone-${tone(b.net_pnl)}`}>{money(b.net_pnl)}</td>
                <td>{pct(b.win_rate)}</td>
                <td>{b.average_efficiency !== null ? `${(Number(b.average_efficiency) * 100).toFixed(0)}%` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
