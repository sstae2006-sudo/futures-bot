import { Fragment, useMemo, useState } from 'react'
import type { TrialOut } from '../types'

/** A 2D grid of a chosen pair of swept parameters, colored by average
 * training score. A hand-built CSS grid rather than a charting-library
 * heatmap -- this project's dependency list stays deliberately small (see
 * docs/RESEARCH_WORKSTATION.md), and a colored grid of divs is simple
 * enough not to need one.
 *
 * "Robust vs. overfit" reading: a wide patch of similar color = many
 * nearby parameter combinations score similarly (a robust region). A
 * single bright cell surrounded by dull ones = an isolated spike --
 * exactly what `research.safety.check_parameter_fitting` already flags in
 * the optimizer's own confidence rating, shown here visually instead of as
 * one sentence.
 */
export function ParameterHeatmap({ trials }: { trials: TrialOut[] }) {
  const paramNames = useMemo(() => {
    const names = new Set<string>()
    for (const t of trials) Object.keys(t.params).forEach((k) => names.add(k))
    // Only parameters that actually varied are worth an axis.
    return Array.from(names).filter((name) => {
      const values = new Set(trials.map((t) => String(t.params[name])))
      return values.size > 1
    })
  }, [trials])

  const [xParam, setXParam] = useState(paramNames[0] ?? '')
  const [yParam, setYParam] = useState(paramNames[1] ?? paramNames[0] ?? '')

  if (paramNames.length < 1) {
    return <p style={{ color: 'var(--text-faint)', fontSize: 12.5 }}>No swept parameter varied enough to plot.</p>
  }

  const xValues = Array.from(new Set(trials.map((t) => String(t.params[xParam])))).sort()
  const yValues = paramNames.length > 1
    ? Array.from(new Set(trials.map((t) => String(t.params[yParam])))).sort()
    : ['(all)']

  const cellFor = (x: string, y: string) => {
    const matches = trials.filter((t) => {
      const xMatch = String(t.params[xParam]) === x
      const yMatch = paramNames.length > 1 ? String(t.params[yParam]) === y : true
      return xMatch && yMatch
    })
    if (matches.length === 0) return null
    const avg = matches.reduce((sum, t) => sum + Number(t.train_net_pnl), 0) / matches.length
    return avg
  }

  const allScores = trials.map((t) => Number(t.train_net_pnl));
  const minScore = Math.min(...allScores);
  const maxScore = Math.max(...allScores);

  function colorFor(score: number | null): string {
    if (score === null) return 'transparent'
    if (maxScore === minScore) return '#5b9dff'
    const t = (score - minScore) / (maxScore - minScore) // 0..1
    // Red (worst) -> yellow (mid) -> green (best), matching the badge palette.
    const hue = t * 120 // 0=red, 120=green
    return `hsl(${hue}, 65%, 50%)`
  }

  return (
    <div>
      <div className="field-row" style={{ maxWidth: 400, marginBottom: 8 }}>
        <div className="field">
          <label htmlFor="heatmap-x">X axis</label>
          <select id="heatmap-x" value={xParam} onChange={(e) => setXParam(e.target.value)}>
            {paramNames.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        {paramNames.length > 1 && (
          <div className="field">
            <label htmlFor="heatmap-y">Y axis</label>
            <select id="heatmap-y" value={yParam} onChange={(e) => setYParam(e.target.value)}>
              {paramNames.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
        )}
      </div>

      <div
        className="heatmap-grid"
        style={{ gridTemplateColumns: `auto repeat(${xValues.length}, 1fr)` }}
      >
        <div />
        {xValues.map((x) => <div key={x} className="heatmap-axis-label">{xParam}={x}</div>)}
        {yValues.map((y) => (
          <Fragment key={y}>
            <div className="heatmap-axis-label">
              {paramNames.length > 1 ? `${yParam}=${y}` : ''}
            </div>
            {xValues.map((x) => {
              const score = cellFor(x, y)
              return (
                <div
                  key={`${x}-${y}`}
                  className="heatmap-cell"
                  style={{ background: colorFor(score) }}
                  title={score !== null ? `${xParam}=${x}${paramNames.length > 1 ? `, ${yParam}=${y}` : ''}: avg train net P&L $${score.toFixed(2)}` : 'no data'}
                >
                  {score !== null ? `$${score.toFixed(0)}` : ''}
                </div>
              )
            })}
          </Fragment>
        ))}
      </div>
      <p style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 8 }}>
        Color: red = worst training net P&L in this grid, green = best. A wide patch of similar
        color is a robust region; one bright cell surrounded by dull ones is an isolated spike —
        treat it as likely overfit, not a real edge.
      </p>
    </div>
  )
}
