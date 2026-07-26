import { useEffect, useState } from 'react'
import { listDatasets, listStrategies, runCompare } from '../api'
import { useApi } from '../useApi'
import { LoadingState, ErrorState, StatTile } from '../components/UI'
import { EquityCurveChart } from '../components/Charts'
import { money, num, pct, int, tone } from '../format'
import type { CompareResult } from '../types'

const COLORS = ['#5b9dff', '#2ecc71', '#e2b93b', '#ff5c5c', '#c084fc', '#40c4d0']

export default function Compare() {
  const strategies = useApi(listStrategies)
  const datasets = useApi(listDatasets)

  const [selected, setSelected] = useState<string[]>([])
  const [dataset, setDataset] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<CompareResult | null>(null)

  useEffect(() => {
    if (!dataset && datasets.data && datasets.data.length > 0) setDataset(datasets.data[0].filename)
  }, [datasets.data, dataset])

  function toggle(name: string) {
    setSelected((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]))
  }

  async function handleRun() {
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      const res = await runCompare({ dataset, strategy_names: selected.length > 0 ? selected : undefined })
      setResult(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Comparison failed to run.')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Strategy Comparison</h1>
        <p>Run every registered strategy (or a chosen subset) on the same data and risk settings.</p>
      </div>

      {(strategies.loading || datasets.loading) && <LoadingState label="Loading strategies and datasets…" />}
      {strategies.error && <ErrorState message={strategies.error} onRetry={strategies.refetch} />}

      {strategies.data && datasets.data && (
        <div className="panel">
          <div className="field">
            <label htmlFor="cmp-dataset">Dataset</label>
            <select id="cmp-dataset" value={dataset} onChange={(e) => setDataset(e.target.value)}>
              {datasets.data.map((d) => <option key={d.filename} value={d.filename}>{d.filename}</option>)}
            </select>
          </div>

          <div className="field">
            <label>Strategies (none selected = all)</label>
            <div className="pill-row">
              {strategies.data.map((s) => (
                <button
                  type="button"
                  key={s.name}
                  className={`pill${selected.includes(s.name) ? ' active' : ''}`}
                  onClick={() => toggle(s.name)}
                >
                  {s.name}
                </button>
              ))}
            </div>
          </div>

          <button className="btn" onClick={handleRun} disabled={running || !dataset}>
            {running ? 'Running…' : 'Run Comparison'}
          </button>
        </div>
      )}

      {error && <ErrorState message={error} />}

      {result && (
        <>
          <div className="panel">
            <h3>Equity Curves</h3>
            <EquityCurveChart
              series={result.entries.map((e, i) => ({ name: e.strategy, color: COLORS[i % COLORS.length], points: e.equity_curve }))}
            />
          </div>

          <div className="panel">
            <h3>Performance</h3>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Net P&amp;L</th>
                    <th>Profit Factor</th>
                    <th>Win Rate</th>
                    <th>Sharpe</th>
                    <th>Max Drawdown</th>
                    <th>Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {result.entries
                    .slice()
                    .sort((a, b) => Number(b.net_pnl) - Number(a.net_pnl))
                    .map((e) => (
                      <tr key={e.strategy}>
                        <td className="text-col">{e.strategy}</td>
                        <td className={`tone-${tone(e.net_pnl)}`}>{money(e.net_pnl)}</td>
                        <td>{num(e.profit_factor)}</td>
                        <td>{pct(e.win_rate)}</td>
                        <td>{num(e.sharpe_ratio)}</td>
                        <td>{money(e.max_drawdown)}</td>
                        <td>{int(e.trade_count)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="grid grid-stats">
            {result.entries.map((e) => (
              <StatTile key={e.strategy} label={`${e.strategy} — stability`} value={num(e.sharpe_ratio)} sub="Sharpe (risk-adjusted return)" />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
