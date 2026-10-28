/** A 2x2 confusion matrix -- hand-built CSS grid, same convention as
 * `ParameterHeatmap`/`CorrelationHeatmap` rather than a charting-library
 * component. */
export function ConfusionMatrix({ tn, fp, fn, tp }: { tn: number; fp: number; fn: number; tp: number }) {
  const total = tn + fp + fn + tp
  const pct = (v: number) => (total > 0 ? `${((v / total) * 100).toFixed(1)}%` : '—')

  function Cell({ label, value, good }: { label: string; value: number; good: boolean }) {
    return (
      <div
        className="heatmap-cell"
        style={{
          // Brass for correct predictions, neutral for errors (design audit F3C).
          background: good ? 'color-mix(in srgb, var(--accent-solid) 38%, var(--bg-panel))' : 'var(--bg-panel-2)',
          flexDirection: 'column', height: 56,
        }}
        title={`${label}: ${value} (${pct(value)})`}
      >
        <div style={{ fontWeight: 600, fontSize: 'var(--text-base)' }}>{value}</div>
        <div className="text-faint" style={{ fontSize: 'var(--text-xs)' }}>{label} · {pct(value)}</div>
      </div>
    )
  }

  return (
    <div className="heatmap-grid" style={{ gridTemplateColumns: 'auto 1fr 1fr', maxWidth: 340 }}>
      <div />
      <div className="heatmap-axis-label">Predicted Win</div>
      <div className="heatmap-axis-label">Predicted Loss</div>

      <div className="heatmap-axis-label">Actual Win</div>
      <Cell label="TP" value={tp} good />
      <Cell label="FN" value={fn} good={false} />

      <div className="heatmap-axis-label">Actual Loss</div>
      <Cell label="FP" value={fp} good={false} />
      <Cell label="TN" value={tn} good />
    </div>
  )
}
