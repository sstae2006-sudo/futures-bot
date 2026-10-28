import {
  Area, Bar, BarChart, ComposedChart, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart as RechartsScatterChart,
  Tooltip, XAxis, YAxis, Legend, ZAxis,
} from 'recharts'
import type { DrawdownPoint, EquityPoint } from '../types'
import { SERIES_COLOR } from '../chartColors'

// 2026-10 design audit F10C: no background grid. Charts read levels from the
// axis ticks, a single $0 reference line marks breakeven where the y-axis is
// money, and single-series P&L curves get a soft area fill. F2B: colours come
// from the --series-* tokens via chartColors.ts, never hex.
const AXIS_COLOR = 'var(--text-faint)'
const ZERO_COLOR = 'var(--text-faint)'
const TICK = { fontSize: 12 }

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
      <ComposedChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <XAxis dataKey="trade_number" stroke={AXIS_COLOR} tick={TICK} label={{ value: 'Trade #', position: 'insideBottom', offset: -4, fontSize: 12, fill: AXIS_COLOR }} />
        <YAxis stroke={AXIS_COLOR} tick={TICK} domain={['auto', 'auto']} tickFormatter={(v) => `$${Number(v).toLocaleString()}`} />
        <Tooltip
          contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }}
          formatter={(value) => `$${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
        />
        {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
        <ReferenceLine y={0} stroke={ZERO_COLOR} strokeDasharray="2 3" />
        {series.length === 1 && (
          <Area type="stepAfter" dataKey={series[0].name} stroke="none" fill={series[0].color} fillOpacity={0.12}
            isAnimationActive={false} legendType="none" tooltipType="none" />
        )}
        {series.map((s) => (
          <Line key={s.name} type="stepAfter" dataKey={s.name} stroke={s.color} dot={false} strokeWidth={1.75} isAnimationActive={false} />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

export function DrawdownChart({ points }: { points: DrawdownPoint[] }) {
  const rows = points.map((p) => ({ trade_number: p.trade_number, drawdown: Number(p.drawdown) }))
  return (
    <ResponsiveContainer width="100%" height={180}>
      <ComposedChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
        <XAxis dataKey="trade_number" stroke={AXIS_COLOR} tick={TICK} />
        <YAxis stroke={AXIS_COLOR} tick={TICK} tickFormatter={(v) => `$${Number(v).toLocaleString()}`} />
        <Tooltip
          contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }}
          formatter={(value) => `$${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
        />
        <ReferenceLine y={0} stroke={ZERO_COLOR} strokeDasharray="2 3" />
        <Area type="stepAfter" dataKey="drawdown" stroke="var(--bad)" strokeWidth={1.5} fill="var(--bad)" fillOpacity={0.12} isAnimationActive={false} />
      </ComposedChart>
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
        <XAxis dataKey="range" stroke={AXIS_COLOR} tick={TICK} interval={0} angle={-35} textAnchor="end" height={40} />
        <YAxis stroke={AXIS_COLOR} tick={TICK} allowDecimals={false} />
        <Tooltip contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }} />
        {showSplit ? (
          <>
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="wins" stackId="a" fill="var(--good)" name="Wins" isAnimationActive={false} />
            <Bar dataKey="losses" stackId="a" fill="var(--bad)" name="Losses" isAnimationActive={false} />
          </>
        ) : (
          <Bar dataKey="count" fill="var(--series-1)" isAnimationActive={false} />
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
        <XAxis type="number" dataKey="value" name="Feature value" stroke={AXIS_COLOR} tick={TICK} />
        <YAxis type="number" dataKey="net_pnl" name="Net P&L" stroke={AXIS_COLOR} tick={TICK} tickFormatter={(v) => `$${v}`} />
        <ZAxis range={[28, 28]} />
        <ReferenceLine y={0} stroke={ZERO_COLOR} strokeDasharray="2 3" />
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
        <XAxis dataKey="fpr" type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={TICK}
          label={{ value: 'False Positive Rate', position: 'insideBottom', offset: -4, fontSize: 12, fill: AXIS_COLOR }} />
        <YAxis type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={TICK}
          label={{ value: 'True Positive Rate', angle: -90, position: 'insideLeft', fontSize: 12, fill: AXIS_COLOR }} />
        <Tooltip contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }} />
        <Line type="monotone" dataKey="chance" stroke={AXIS_COLOR} strokeDasharray="4 4" dot={false} isAnimationActive={false} name="Chance" />
        <Line type="monotone" dataKey="tpr" stroke="var(--series-1)" dot={false} strokeWidth={1.75} isAnimationActive={false} name="ROC" />
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
        <XAxis dataKey="recall" type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={TICK}
          label={{ value: 'Recall', position: 'insideBottom', offset: -4, fontSize: 12, fill: AXIS_COLOR }} />
        <YAxis type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={TICK}
          label={{ value: 'Precision', angle: -90, position: 'insideLeft', fontSize: 12, fill: AXIS_COLOR }} />
        <Tooltip contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }} />
        <Line type="monotone" dataKey="precision" stroke={SERIES_COLOR} dot={false} strokeWidth={1.75} isAnimationActive={false} name="Precision" />
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
        <XAxis dataKey="predicted" type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={TICK}
          label={{ value: 'Predicted probability', position: 'insideBottom', offset: -4, fontSize: 12, fill: AXIS_COLOR }} />
        <YAxis type="number" domain={[0, 1]} stroke={AXIS_COLOR} tick={TICK}
          label={{ value: 'Actual win rate', angle: -90, position: 'insideLeft', fontSize: 12, fill: AXIS_COLOR }} />
        <Tooltip contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }} />
        <Line type="monotone" dataKey="perfect" stroke={AXIS_COLOR} strokeDasharray="4 4" dot={false} isAnimationActive={false} name="Perfectly calibrated" />
        <Line type="monotone" dataKey="actual" stroke="var(--series-1)" dot={{ r: 3 }} strokeWidth={1.75} isAnimationActive={false} name="Observed" />
      </LineChart>
    </ResponsiveContainer>
  )
}
