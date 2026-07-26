import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart as RechartsScatterChart,
  Tooltip, XAxis, YAxis, Legend, ZAxis,
} from 'recharts'
import type { DrawdownPoint, EquityPoint } from '../types'

const AXIS_COLOR = 'var(--text-faint)'
const GRID_COLOR = 'var(--border)'

export function EquityCurveChart({
  series,
}: {
  series: { name: string; color: string; points: EquityPoint[] }[]
}) {
  const maxLen = Math.max(...series.map((s) => s.points.length), 0)
  const rows = Array.from({ length: maxLen }, (_, i) => {
    const row: Record<string, number> = { trade_number: i }
    for (const s of series) {
      const point = s.points[i]
      if (point) row[s.name] = Number(point.equity)
    }
    return row
  })

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
        <XAxis dataKey="trade_number" stroke={AXIS_COLOR} tick={{ fontSize: 11 }} label={{ value: 'Trade #', position: 'insideBottom', offset: -4, fontSize: 11, fill: AXIS_COLOR }} />
        <YAxis stroke={AXIS_COLOR} tick={{ fontSize: 11 }} domain={['auto', 'auto']} tickFormatter={(v) => `$${Number(v).toLocaleString()}`} />
        <Tooltip
          contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }}
          formatter={(value) => `$${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
        />
        {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
        {series.map((s) => (
          <Line key={s.name} type="stepAfter" dataKey={s.name} stroke={s.color} dot={false} strokeWidth={1.75} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

export function DrawdownChart({ points }: { points: DrawdownPoint[] }) {
  const rows = points.map((p) => ({ trade_number: p.trade_number, drawdown: Number(p.drawdown) }))
  return (
    <ResponsiveContainer width="100%" height={180}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
        <XAxis dataKey="trade_number" stroke={AXIS_COLOR} tick={{ fontSize: 11 }} />
        <YAxis stroke={AXIS_COLOR} tick={{ fontSize: 11 }} tickFormatter={(v) => `$${Number(v).toLocaleString()}`} />
        <Tooltip
          contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }}
          formatter={(value) => `$${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
        />
        <Line type="stepAfter" dataKey="drawdown" stroke="var(--bad)" dot={false} strokeWidth={1.5} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}

/** Phase 9: a feature's distribution -- plain counts per bin, or a
 * win/loss-stacked overlay when `winCounts`/`lossCounts` are given (Feature
 * Explorer and Feature Effects both use this, the latter in overlay mode). */
export function HistogramChart({
  bins, counts, winCounts, lossCounts,
}: {
  bins: number[]
  counts: number[]
  winCounts?: number[]
  lossCounts?: number[]
}) {
  const showSplit = !!winCounts && !!lossCounts
  const rows = counts.map((c, i) => ({
    range: bins[i] !== undefined && bins[i + 1] !== undefined ? `${bins[i].toFixed(1)}–${bins[i + 1].toFixed(1)}` : String(i),
    count: c,
    wins: winCounts?.[i] ?? 0,
    losses: lossCounts?.[i] ?? 0,
  }))

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 24 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
        <XAxis dataKey="range" stroke={AXIS_COLOR} tick={{ fontSize: 9.5 }} interval={0} angle={-35} textAnchor="end" height={40} />
        <YAxis stroke={AXIS_COLOR} tick={{ fontSize: 11 }} allowDecimals={false} />
        <Tooltip contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }} />
        {showSplit ? (
          <>
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="wins" stackId="a" fill="var(--good)" name="Wins" isAnimationActive={false} />
            <Bar dataKey="losses" stackId="a" fill="var(--bad)" name="Losses" isAnimationActive={false} />
          </>
        ) : (
          <Bar dataKey="count" fill="var(--accent, #5b9dff)" isAnimationActive={false} />
        )}
      </BarChart>
    </ResponsiveContainer>
  )
}

/** Phase 9: one feature's value vs. net P&L across recorded trades,
 * colored by outcome -- the "relationship to profitability" view. */
export function FeatureScatterChart({
  points,
}: {
  points: { value: number; net_pnl: number; outcome: string }[]
}) {
  const wins = points.filter((p) => p.outcome === 'win')
  const losses = points.filter((p) => p.outcome !== 'win')
  return (
    <ResponsiveContainer width="100%" height={260}>
      <RechartsScatterChart margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
        <XAxis type="number" dataKey="value" name="Feature value" stroke={AXIS_COLOR} tick={{ fontSize: 11 }} />
        <YAxis type="number" dataKey="net_pnl" name="Net P&L" stroke={AXIS_COLOR} tick={{ fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
        <ZAxis range={[28, 28]} />
        <ReferenceLine y={0} stroke={AXIS_COLOR} strokeDasharray="3 3" />
        <Tooltip
          cursor={{ strokeDasharray: '3 3' }}
          contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }}
          formatter={(value, name) => (name === 'Net P&L' ? `$${Number(value).toFixed(2)}` : Number(value).toFixed(2))}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Scatter name="Wins" data={wins} fill="var(--good)" isAnimationActive={false} />
        <Scatter name="Losses" data={losses} fill="var(--bad)" isAnimationActive={false} />
      </RechartsScatterChart>
    </ResponsiveContainer>
  )
}

/** Phase 9: ROC curve (from `ModelDiagnostics.roc_curve`) with the
 * random-chance diagonal for reference. */
export function ROCCurveChart({ fpr, tpr }: { fpr: number[]; tpr: number[] }) {
  const rows = fpr.map((f, i) => ({ fpr: f, tpr: tpr[i], chance: f }))
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
        <XAxis dataKey="fpr" type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={{ fontSize: 11 }}
          label={{ value: 'False Positive Rate', position: 'insideBottom', offset: -4, fontSize: 11, fill: AXIS_COLOR }} />
        <YAxis type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={{ fontSize: 11 }}
          label={{ value: 'True Positive Rate', angle: -90, position: 'insideLeft', fontSize: 11, fill: AXIS_COLOR }} />
        <Tooltip contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }} />
        <Line type="monotone" dataKey="chance" stroke={AXIS_COLOR} strokeDasharray="4 4" dot={false} isAnimationActive={false} name="Chance" />
        <Line type="monotone" dataKey="tpr" stroke="var(--accent, #5b9dff)" dot={false} strokeWidth={1.75} isAnimationActive={false} name="ROC" />
      </LineChart>
    </ResponsiveContainer>
  )
}

/** Phase 9: Precision-Recall curve (from `ModelDiagnostics.pr_curve`). */
export function PRCurveChart({ precision, recall }: { precision: number[]; recall: number[] }) {
  const rows = recall.map((r, i) => ({ recall: r, precision: precision[i] }))
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
        <XAxis dataKey="recall" type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={{ fontSize: 11 }}
          label={{ value: 'Recall', position: 'insideBottom', offset: -4, fontSize: 11, fill: AXIS_COLOR }} />
        <YAxis type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={{ fontSize: 11 }}
          label={{ value: 'Precision', angle: -90, position: 'insideLeft', fontSize: 11, fill: AXIS_COLOR }} />
        <Tooltip contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }} />
        <Line type="monotone" dataKey="precision" stroke="var(--good)" dot={false} strokeWidth={1.75} isAnimationActive={false} name="Precision" />
      </LineChart>
    </ResponsiveContainer>
  )
}

/** Phase 9: reliability/calibration curve (from `ModelDiagnostics
 * .calibration_curve`) -- predicted probability vs. actual observed win
 * rate per bucket, with the perfectly-calibrated diagonal for reference. */
export function CalibrationChart({ predicted, actual }: { predicted: number[]; actual: number[] }) {
  const rows = predicted.map((p, i) => ({ predicted: p, actual: actual[i], perfect: p }))
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
        <XAxis dataKey="predicted" type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={{ fontSize: 11 }}
          label={{ value: 'Predicted probability', position: 'insideBottom', offset: -4, fontSize: 11, fill: AXIS_COLOR }} />
        <YAxis type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={{ fontSize: 11 }}
          label={{ value: 'Actual win rate', angle: -90, position: 'insideLeft', fontSize: 11, fill: AXIS_COLOR }} />
        <Tooltip contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }} />
        <Line type="monotone" dataKey="perfect" stroke={AXIS_COLOR} strokeDasharray="4 4" dot={false} isAnimationActive={false} name="Perfectly calibrated" />
        <Line type="monotone" dataKey="actual" stroke="var(--accent, #5b9dff)" dot={{ r: 3 }} strokeWidth={1.75} isAnimationActive={false} name="Observed" />
      </LineChart>
    </ResponsiveContainer>
  )
}
