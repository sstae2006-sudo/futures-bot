import type { CorrelationRow } from '../types'

/** Feature-vs-{win/loss, net P&L, R multiple} heatmap -- a hand-built CSS
 * grid, mirroring `ParameterHeatmap.tsx`'s own approach (this project
 * deliberately avoids adding a charting-library heatmap dependency). */
const METRICS: { key: 'corr_vs_win' | 'corr_vs_pnl' | 'corr_vs_r'; label: string }[] = [
  { key: 'corr_vs_win', label: 'Win/Loss' },
  { key: 'corr_vs_pnl', label: 'Net P&L' },
  { key: 'corr_vs_r', label: 'R Multiple' },
]

function colorFor(value: number | null): string {
  if (value === null) return 'transparent'
  const clamped = Math.max(-1, Math.min(1, value))
  // -1 -> red (hue 0), 0 -> yellow (hue 60), +1 -> green (hue 120), matching
  // the red/yellow/green convention ParameterHeatmap/Badge already use.
  const hue = (clamped + 1) * 60
  const alpha = 0.2 + Math.abs(clamped) * 0.6
  return `hsla(${hue}, 70%, 50%, ${alpha})`
}

export function CorrelationHeatmap({ rows }: { rows: CorrelationRow[] }) {
  if (rows.length === 0) {
    return <p style={{ color: 'var(--text-faint)', fontSize: 12.5 }}>No features to correlate yet.</p>
  }

  return (
    <div>
      <div className="heatmap-grid" style={{ gridTemplateColumns: `auto repeat(${METRICS.length}, 1fr)` }}>
        <div />
        {METRICS.map((m) => <div key={m.key} className="heatmap-axis-label">{m.label}</div>)}
        {rows.map((row) => (
          <div key={row.feature} style={{ display: 'contents' }}>
            <div className="heatmap-axis-label" style={{ textAlign: 'right', paddingRight: 8 }}>{row.feature}</div>
            {METRICS.map((m) => {
              const value = row[m.key]
              return (
                <div
                  key={`${row.feature}-${m.key}`}
                  className="heatmap-cell"
                  style={{ background: colorFor(value) }}
                  title={value !== null ? `${row.feature} vs ${m.label}: ${value.toFixed(3)}` : 'no data'}
                >
                  {value !== null ? value.toFixed(2) : '—'}
                </div>
              )
            })}
          </div>
        ))}
      </div>
      <p style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 8 }}>
        Color: red = negative correlation, yellow = none, green = positive. R multiple is an
        approximation (net P&amp;L ÷ this strategy&apos;s average loss size) since initial risk
        isn&apos;t logged per trade.
      </p>
    </div>
  )
}
