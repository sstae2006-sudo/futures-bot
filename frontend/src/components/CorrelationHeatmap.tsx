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
  // Single-hue brass ramp by strength |r| (2026-10 design audit F3C); the
  // number in the cell carries the sign.
  return `color-mix(in srgb, var(--accent-solid) ${Math.round(6 + Math.abs(clamped) * 74)}%, var(--bg-panel))`
}

export function CorrelationHeatmap({ rows }: { rows: CorrelationRow[] }) {
  if (rows.length === 0) {
    return <p className="text-xs text-faint">No features to correlate yet.</p>
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
      <p className="text-xs text-faint mt-2">
        Color: red = negative correlation, yellow = none, green = positive. R multiple is an
        approximation (net P&amp;L ÷ this strategy&apos;s average loss size) since initial risk
        isn&apos;t logged per trade.
      </p>
    </div>
  )
}
