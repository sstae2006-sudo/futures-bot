import { useState } from 'react'
import {
  getMarketDataOverview, syncMarketDataNow, backfillMarketData, verifyMarketData, repairMarketDataGaps,
  startMarketDataScheduler, stopMarketDataScheduler, ApiRequestError,
} from '../api'
import { useApi } from '../useApi'
import { LoadingState, ErrorState, EmptyState, StatTile, Badge } from '../components/UI'
import { dateTime, int } from '../format'

function bytesToMb(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function MarketData() {
  const overview = useApi(getMarketDataOverview)

  const [product, setProduct] = useState('MES')
  const [resolution, setResolution] = useState('5min')
  const [backfillStart, setBackfillStart] = useState('')
  const [backfillEnd, setBackfillEnd] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function run<T>(label: string, action: () => Promise<T>, describe: (r: T) => string) {
    setBusy(label)
    setError(null)
    setMessage(null)
    try {
      const result = await action()
      setMessage(describe(result))
      overview.refetch()
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : `${label} failed.`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Market Data</h1>
        <p>
          The local historical market database (Phase 8A) -- backtests, the optimizer, research
          tools, and paper trading all read from this when a dataset is named <code>db:PRODUCT:RESOLUTION</code>
          (e.g. <code>db:MES:5min</code>). See <code>market_data/sync.py</code> for how contracts are
          auto-detected and rolled.
        </p>
      </div>

      {overview.loading && <LoadingState label="Loading market data overview…" />}
      {overview.error && <ErrorState message={overview.error} onRetry={overview.refetch} />}

      {overview.data && (
        <>
          <div className="panel">
            <div className="stat-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
              <StatTile label="Total bars" value={int(overview.data.total_bars)} />
              <StatTile label="Contracts stored" value={overview.data.products.reduce((n, p) => n + p.contracts_stored.length, 0)} />
              <StatTile label="Open gaps" value={overview.data.total_open_gaps} tone={overview.data.total_open_gaps > 0 ? 'bad' : 'good'} />
              <StatTile label="Database size" value={bytesToMb(overview.data.database_size_bytes)} />
              <StatTile label="Last sync" value={dateTime(overview.data.last_sync_at)} sub={overview.data.last_sync_status ?? '—'} />
              <StatTile
                label="Scheduler"
                value={<Badge tone={overview.data.scheduler_running ? 'good' : 'neutral'}>{overview.data.scheduler_running ? 'running' : 'stopped'}</Badge>}
              />
            </div>
          </div>

          <div className="panel">
            <h3>Coverage by product</h3>
            {overview.data.products.length === 0 && <EmptyState label="No data synced yet -- use the controls below to backfill or sync." />}
            {overview.data.products.length > 0 && (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Product</th><th>Contracts stored</th><th>Bars</th><th>Earliest</th><th>Latest</th><th>Open gaps</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.data.products.map((p) => (
                      <tr key={p.product_code}>
                        <td className="text-col">{p.product_code}</td>
                        <td className="text-col">{p.contracts_stored.join(', ')}</td>
                        <td>{int(p.bars_stored)}</td>
                        <td>{dateTime(p.earliest)}</td>
                        <td>{dateTime(p.latest)}</td>
                        <td><Badge tone={p.open_gaps > 0 ? 'bad' : 'good'}>{p.open_gaps}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {overview.data.recent_rolls.length > 0 && (
            <div className="panel">
              <h3>Recent contract rolls</h3>
              <ul>
                {overview.data.recent_rolls.map((r, i) => (
                  <li key={i}>{r.product_code}: {r.from_contract ?? '(none)'} → {r.to_contract} ({dateTime(r.rolled_at)})</li>
                ))}
              </ul>
            </div>
          )}

          <div className="panel">
            <h3>Pipeline controls</h3>
            <div className="field-row">
              <div className="field">
                <label htmlFor="md-product">Product</label>
                <input id="md-product" value={product} onChange={(e) => setProduct(e.target.value.toUpperCase())} placeholder="MES" />
              </div>
              <div className="field">
                <label htmlFor="md-resolution">Resolution</label>
                <input id="md-resolution" value={resolution} onChange={(e) => setResolution(e.target.value)} placeholder="5min" />
              </div>
              <div className="field">
                <label htmlFor="md-start">Backfill start</label>
                <input id="md-start" type="date" value={backfillStart} onChange={(e) => setBackfillStart(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="md-end">Backfill end</label>
                <input id="md-end" type="date" value={backfillEnd} onChange={(e) => setBackfillEnd(e.target.value)} />
              </div>
            </div>

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
              <button
                type="button" className="btn btn-primary" disabled={busy !== null}
                onClick={() => run('sync', () => syncMarketDataNow({ product_code: product, resolution }), (r) => `Synced ${r.product_code} ${r.resolution}: ${r.bars_fetched} new bar(s).`)}
              >
                {busy === 'sync' ? 'Syncing…' : 'Sync now'}
              </button>
              <button
                type="button" className="btn btn-secondary" disabled={busy !== null || !backfillStart || !backfillEnd}
                onClick={() => run('backfill', () => backfillMarketData({ product_code: product, resolution, start: backfillStart, end: backfillEnd }), (r) => `Backfilled ${r.product_code} ${r.resolution}: ${r.bars_fetched} new bar(s).`)}
              >
                {busy === 'backfill' ? 'Backfilling…' : 'Backfill range'}
              </button>
              <button
                type="button" className="btn btn-secondary" disabled={busy !== null}
                onClick={() => run('verify', () => verifyMarketData({ product_code: product, resolution }), (r) => `Verified ${product} ${resolution}: ${r.new_gaps} new gap(s), ${r.total_open_gaps} total open.`)}
              >
                {busy === 'verify' ? 'Verifying…' : 'Verify (scan for gaps)'}
              </button>
              <button
                type="button" className="btn btn-secondary" disabled={busy !== null}
                onClick={() => run('repair', () => repairMarketDataGaps({ product_code: product, resolution }), (r) => `Repaired ${product} ${resolution}: ${r.gaps_resolved}/${r.gaps_attempted} gap(s) resolved, ${r.bars_recovered} bar(s) recovered.`)}
              >
                {busy === 'repair' ? 'Repairing…' : 'Repair gaps'}
              </button>
              {overview.data.scheduler_running ? (
                <button
                  type="button" className="btn btn-secondary" disabled={busy !== null}
                  onClick={() => run('scheduler', stopMarketDataScheduler, () => 'Scheduler stopped.')}
                >
                  Stop scheduler
                </button>
              ) : (
                <button
                  type="button" className="btn btn-secondary" disabled={busy !== null}
                  onClick={() => run(
                    'scheduler',
                    () => startMarketDataScheduler({ targets: [{ product_code: product, resolution }], interval_seconds: 300 }),
                    () => 'Scheduler started -- syncs automatically every 5 minutes while the market is open.',
                  )}
                >
                  Start scheduler
                </button>
              )}
            </div>

            {message && <p style={{ marginTop: 12 }}>{message}</p>}
            {error && <p role="alert" style={{ marginTop: 12 }}>{error}</p>}
          </div>
        </>
      )}
    </div>
  )
}
