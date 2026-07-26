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
          background: good ? 'hsla(120, 60%, 45%, 0.30)' : 'hsla(0, 60%, 45%, 0.30)',
          flexDirection: 'column', height: 56,
        }}
        title={`${label}: ${value} (${pct(value)})`}
      >
        <div style={{ fontWeight: 600, fontSize: 15 }}>{value}</div>
        <div style={{ fontSize: 10, color: 'var(--text-faint)' }}>{label} · {pct(value)}</div>
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
