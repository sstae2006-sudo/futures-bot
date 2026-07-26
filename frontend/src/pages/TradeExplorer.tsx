import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getTradeAnalytics, listTrades } from '../api'
import { useApi } from '../useApi'
import { LoadingState, ErrorState, EmptyState, Badge } from '../components/UI'
import { money, num, dateTime, tone } from '../format'
import type { TradeOut } from '../types'

export default function TradeExplorer() {
  // Deep-linkable from e.g. Import Trades' "view in Trade Explorer" link
  // (`/trades?strategy=import:<profile>`) -- read once on mount, same as
  // any other initial filter state; the dropdown remains the source of
  // truth afterward.
  const [searchParams] = useSearchParams()
  const [strategy, setStrategy] = useState(() => searchParams.get('strategy') ?? '')
  const [side, setSide] = useState('')
  const [outcome, setOutcome] = useState('')
  const [view, setView] = useState<'all' | 'best_entries' | 'poor_exits' | 'missed_opportunities'>('all')
  const trades = useApi(
    () => listTrades({ strategy: strategy || undefined, side: side || undefined, outcome: outcome || undefined }),
    [strategy, side, outcome],
  )
  const analytics = useApi(() => getTradeAnalytics({ strategy: strategy || undefined, top_n: 20 }), [strategy])
  const [selected, setSelected] = useState<TradeOut | null>(null)

  const strategies = useMemo(() => Array.from(new Set((trades.data ?? []).map((t) => t.strategy))), [trades.data])

  const displayedTrades = useMemo(() => {
    if (view === 'all') return trades.data ?? []
    if (!analytics.data) return []
    return analytics.data[view]
  }, [view, trades.data, analytics.data])

  return (
    <div>
      <div className="page-header">
        <h1>Trade Explorer</h1>
        <p>Search and inspect every recorded trade, with entry market context, MAE/MFE, and exit efficiency.</p>
      </div>

      <div className="panel">
        <div className="field-row">
          <div className="field">
            <label htmlFor="f-strategy">Strategy</label>
            <select id="f-strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="">All</option>
              {strategies.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="f-side">Direction</label>
            <select id="f-side" value={side} onChange={(e) => setSide(e.target.value)}>
              <option value="">All</option>
              <option value="long">Long</option>
              <option value="short">Short</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="f-outcome">Result</label>
            <select id="f-outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
              <option value="">All</option>
              <option value="win">Win</option>
              <option value="loss">Loss</option>
              <option value="scratch">Scratch</option>
            </select>
          </div>
        </div>

        <div className="pill-row" style={{ marginTop: 8, marginBottom: 0 }}>
          <button type="button" className={`pill${view === 'all' ? ' active' : ''}`} onClick={() => setView('all')}>
            All trades
          </button>
          <button type="button" className={`pill${view === 'best_entries' ? ' active' : ''}`} onClick={() => setView('best_entries')}>
            Best entries
          </button>
          <button type="button" className={`pill${view === 'poor_exits' ? ' active' : ''}`} onClick={() => setView('poor_exits')}>
            Poor exits
          </button>
          <button type="button" className={`pill${view === 'missed_opportunities' ? ' active' : ''}`} onClick={() => setView('missed_opportunities')}>
            Missed opportunities
          </button>
        </div>
        {view !== 'all' && (
          <p style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 8 }}>
            {view === 'best_entries' && 'Highest efficiency: exits that captured most of the favorable move available.'}
            {view === 'poor_exits' && 'Lowest efficiency among trades with a real favorable move available — the entry was fine, the exit left money on the table.'}
            {view === 'missed_opportunities' && 'Losing or scratch trades where a meaningful favorable move (2+ points) was available before it reversed.'}
          </p>
        )}
      </div>

      {(trades.loading || (view !== 'all' && analytics.loading)) && <LoadingState label="Loading trades…" />}
      {trades.error && <ErrorState message={trades.error} onRetry={trades.refetch} />}
      {analytics.error && view !== 'all' && <ErrorState message={analytics.error} onRetry={analytics.refetch} />}
      {!trades.loading && displayedTrades.length === 0 && <EmptyState label="No trades match these filters." />}

      {displayedTrades.length > 0 && (
        <div className="grid grid-2">
          <div className="panel">
            <h3>{displayedTrades.length} trade(s)</h3>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Entry Time</th>
                    <th>Strategy</th>
                    <th>Side</th>
                    <th>Net P&amp;L</th>
                    <th>MFE</th>
                    <th>MAE</th>
                    <th>Efficiency</th>
                    <th>Regime</th>
                    <th>Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {displayedTrades.map((t) => (
                    <tr key={t.id} onClick={() => setSelected(t)} style={{ cursor: 'pointer', background: selected?.id === t.id ? 'var(--accent-dim)' : undefined }}>
                      <td>{dateTime(t.entry_time)}</td>
                      <td className="text-col">{t.strategy}</td>
                      <td>{t.side}</td>
                      <td className={`tone-${tone(t.net_pnl)}`}>{money(t.net_pnl)}</td>
                      <td>{t.mfe_points !== null ? num(t.mfe_points, 2) : '—'}</td>
                      <td>{t.mae_points !== null ? num(t.mae_points, 2) : '—'}</td>
                      <td>{t.efficiency !== null ? `${(Number(t.efficiency) * 100).toFixed(0)}%` : '—'}</td>
                      <td className="text-col">{t.regime_trend ?? '—'} / {t.regime_volatility ?? '—'}</td>
                      <td>
                        <Badge tone={t.outcome === 'win' ? 'good' : t.outcome === 'loss' ? 'bad' : 'neutral'}>{t.outcome}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <h3>Trade Detail</h3>
            {!selected && <EmptyState label="Select a trade to inspect its entry context and result." />}
            {selected && <TradeDetail trade={selected} />}
          </div>
        </div>
      )}
    </div>
  )
}

function TradeDetail({ trade }: { trade: TradeOut }) {
  const metadataEntries = Object.entries(trade.entry_metadata)
  return (
    <div>
      <h3 style={{ marginTop: 0 }}>Entry</h3>
      <dl className="detail-list">
        <Row label="Trade ID" value={trade.trade_id ?? String(trade.id)} />
        <Row label="Price" value={String(trade.entry_price)} />
        <Row label="Timestamp" value={dateTime(trade.entry_time)} />
        <Row label="Strategy" value={trade.strategy} />
        <Row label="Signal Reason" value={trade.entry_reason} />
        <Row label="Stop Loss" value={trade.stop_loss !== null ? String(trade.stop_loss) : '—'} />
        <Row label="Target" value={trade.take_profit !== null ? String(trade.take_profit) : '—'} />
        <Row label="Entry Slippage" value={`${trade.entry_slippage} pts`} />
      </dl>

      <h3>Market Context</h3>
      {metadataEntries.length === 0 && <p style={{ color: 'var(--text-faint)' }}>No indicator snapshot recorded for this entry.</p>}
      <dl className="detail-list">
        {metadataEntries.map(([k, v]) => (
          <Row key={k} label={k} value={typeof v === 'number' ? num(String(v), 4) : String(v)} />
        ))}
      </dl>

      <h3>Market Regime</h3>
      <dl className="detail-list">
        <Row label="Trend" value={trade.regime_trend ?? '—'} />
        <Row label="Volatility" value={trade.regime_volatility ?? '—'} />
        <Row label="Session" value={trade.regime_session ?? '—'} />
      </dl>

      <h3>Result</h3>
      <dl className="detail-list">
        <Row label="Exit Price" value={String(trade.exit_price)} />
        <Row label="Net P&L" value={money(trade.net_pnl)} />
        <Row label="Exit Reason" value={trade.exit_reason} />
        <Row label="Exit Slippage" value={`${trade.exit_slippage} pts`} />
        <Row label="Commission" value={money(trade.commission)} />
        <Row label="Duration" value={`${trade.holding_minutes.toFixed(0)} min`} />
        <Row label="Session Date" value={`${trade.session_date} (${trade.day_of_week})`} />
        <Row label="MFE (available favorable move)" value={trade.mfe_points !== null ? `${num(trade.mfe_points, 2)} pts` : '—'} />
        <Row label="MAE (worst adverse move)" value={trade.mae_points !== null ? `${num(trade.mae_points, 2)} pts` : '—'} />
        <Row label="Efficiency (realized / available)" value={trade.efficiency !== null ? `${(Number(trade.efficiency) * 100).toFixed(0)}%` : '—'} />
      </dl>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid var(--border)', fontSize: 12.5 }}>
      <span style={{ color: 'var(--text-faint)' }}>{label}</span>
      <span className="mono">{value}</span>
    </div>
  )
}
