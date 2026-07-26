import { useEffect, useState } from 'react'
import { getOptimizerResults, listDatasets, listStrategies, runOptimizer, submitOptimizerJob } from '../api'
import { useApi } from '../useApi'
import { useJobStream } from '../useJobStream'
import { LoadingState, ErrorState, Badge } from '../components/UI'
import { JobProgressBar } from '../components/JobProgress'
import { ParameterHeatmap } from '../components/ParameterHeatmap'
import { money, num, int } from '../format'
import type { OptimizerResultOut, StrategyParam, TrialOut } from '../types'

/** Parses "5, 9, 13" -> [5, 9, 13], respecting the param's declared type. A
 * single value with no comma is still wrapped in a list -- the backend's
 * `expand_param_grid` treats any list-valued entry as a sweep dimension
 * (see research/optimizer.py), so a one-value "sweep" is how a param stays
 * fixed at something other than its default without also being swept. */
function parseSweepValues(raw: string, type: StrategyParam['type']): unknown[] {
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map((s) => {
      if (type === 'boolean') return s === 'true'
      if (type === 'int') return parseInt(s, 10)
      if (type === 'number') return parseFloat(s)
      return s
    })
}

export default function Optimizer() {
  const strategies = useApi(listStrategies)
  const datasets = useApi(listDatasets)

  const [strategyName, setStrategyName] = useState('')
  const [dataset, setDataset] = useState('')
  const [topN, setTopN] = useState('10')
  const [rolling, setRolling] = useState(false)
  const [runInBackground, setRunInBackground] = useState(true)
  const [sweeps, setSweeps] = useState<Record<string, string>>({})

  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<OptimizerResultOut | null>(null)
  const [allTrials, setAllTrials] = useState<TrialOut[] | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)

  const streamedJob = useJobStream(jobId)

  useEffect(() => {
    if (!strategyName && strategies.data && strategies.data.length > 0) setStrategyName(strategies.data[0].name)
  }, [strategies.data, strategyName])
  useEffect(() => {
    if (!dataset && datasets.data && datasets.data.length > 0) setDataset(datasets.data[0].filename)
  }, [datasets.data, dataset])

  // When a background job finishes, fetch the full trial set for the
  // heatmap the same way the synchronous path does below.
  useEffect(() => {
    if (streamedJob?.status === 'completed' && streamedJob.result_id) {
      getOptimizerResults(streamedJob.result_id).then(setAllTrials).catch(() => setAllTrials(null))
    }
  }, [streamedJob])

  const currentSchema: StrategyParam[] = strategies.data?.find((s) => s.name === strategyName)?.parameters ?? []

  function buildParamGrid(): Record<string, unknown> {
    const param_grid: Record<string, unknown> = {}
    for (const p of currentSchema) {
      const raw = sweeps[p.name]
      if (!raw) continue
      const values = parseSweepValues(raw, p.type)
      if (values.length > 0) param_grid[p.name] = values
    }
    return param_grid
  }

  async function handleRun(e: React.FormEvent) {
    e.preventDefault()
    setRunning(true)
    setError(null)
    setResult(null)
    setAllTrials(null)
    setJobId(null)
    const param_grid = buildParamGrid()

    try {
      if (runInBackground) {
        const job = await submitOptimizerJob({ strategy_name: strategyName, dataset, param_grid, top_n: Number(topN), rolling })
        setJobId(job.id)
      } else {
        const res = await runOptimizer({ strategy_name: strategyName, dataset, param_grid, top_n: Number(topN), rolling })
        setResult(res)
        getOptimizerResults(res.batch_id).then(setAllTrials).catch(() => setAllTrials(null))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Optimization failed to run.')
    } finally {
      setRunning(false)
    }
  }

  // Background-job results reuse the same rendering as the synchronous
  // path once the payload is available via the linked run/batch.
  const displayConfidenceWarnings = result
    ? { confidence: result.confidence, warnings: result.warnings, strategy: result.strategy, combosTotal: result.combos_tried }
    : null

  return (
    <div>
      <div className="page-header">
        <h1>Optimizer</h1>
        <p>Sweep parameters, validate out-of-sample, and see which combinations survive.</p>
      </div>

      {(strategies.loading || datasets.loading) && <LoadingState label="Loading strategies and datasets…" />}
      {strategies.error && <ErrorState message={strategies.error} onRetry={strategies.refetch} />}

      {strategies.data && datasets.data && (
        <form className="panel" onSubmit={handleRun}>
          <div className="field-row">
            <div className="field">
              <label htmlFor="opt-strategy">Strategy</label>
              <select id="opt-strategy" value={strategyName} onChange={(e) => setStrategyName(e.target.value)}>
                {strategies.data.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="opt-dataset">Dataset</label>
              <select id="opt-dataset" value={dataset} onChange={(e) => setDataset(e.target.value)}>
                {datasets.data.map((d) => <option key={d.filename} value={d.filename}>{d.filename}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="opt-topn">Top N to Validate</label>
              <input id="opt-topn" type="number" value={topN} onChange={(e) => setTopN(e.target.value)} />
            </div>
          </div>

          <div className="checkbox-field" style={{ margin: '8px 0' }}>
            <input id="opt-rolling" type="checkbox" checked={rolling} onChange={(e) => setRolling(e.target.checked)} />
            <label htmlFor="opt-rolling" style={{ margin: 0, textTransform: 'none', fontSize: 13, color: 'var(--text)' }}>
              Rolling walk-forward validation (slower, more robust)
            </label>
          </div>
          <div className="checkbox-field" style={{ margin: '0 0 16px' }}>
            <input id="opt-bg" type="checkbox" checked={runInBackground} onChange={(e) => setRunInBackground(e.target.checked)} />
            <label htmlFor="opt-bg" style={{ margin: 0, textTransform: 'none', fontSize: 13, color: 'var(--text)' }}>
              Run in background with live progress (see also the Jobs page)
            </label>
          </div>

          <h3>Parameters to Sweep</h3>
          <p style={{ color: 'var(--text-faint)', fontSize: 12 }}>
            Comma-separated values, e.g. "5, 9, 13". Leave blank to hold a parameter at its default.
          </p>
          <div className="field-row">
            {currentSchema.map((p) => (
              <div className="field" key={p.name}>
                <label htmlFor={`sweep-${p.name}`} title={p.description ?? undefined}>{p.name}</label>
                <input
                  id={`sweep-${p.name}`}
                  placeholder={`e.g. ${p.default}, ...`}
                  value={sweeps[p.name] ?? ''}
                  onChange={(e) => setSweeps((prev) => ({ ...prev, [p.name]: e.target.value }))}
                />
              </div>
            ))}
          </div>

          <button className="btn" type="submit" disabled={running || !strategyName || !dataset} style={{ marginTop: 12 }}>
            {running ? 'Submitting…' : 'Run Optimizer'}
          </button>
        </form>
      )}

      {error && <ErrorState message={error} />}

      {jobId && streamedJob && (
        <div className="panel">
          <h3>Job Progress</h3>
          <JobProgressBar job={streamedJob} />
        </div>
      )}

      {displayConfidenceWarnings && (
        <div className="panel">
          <h3>Summary</h3>
          <p>
            {int(displayConfidenceWarnings.combosTotal)} combination(s) tried for <strong>{displayConfidenceWarnings.strategy}</strong>.
            {' '}Confidence: {displayConfidenceWarnings.confidence ? (
              <Badge tone={displayConfidenceWarnings.confidence === 'High' ? 'good' : displayConfidenceWarnings.confidence === 'Medium' ? 'warn' : 'bad'}>
                {displayConfidenceWarnings.confidence}
              </Badge>
            ) : '—'}
          </p>
          {displayConfidenceWarnings.warnings.length > 0 && (
            <ul style={{ marginTop: 8 }}>
              {displayConfidenceWarnings.warnings.map((w, i) => <li key={i} style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>{w}</li>)}
            </ul>
          )}
        </div>
      )}

      {allTrials && allTrials.length > 0 && (
        <div className="panel">
          <h3>Trials {allTrials.some((t) => t.rank !== null) ? '(ranked ones were validated out-of-sample)' : ''}</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Parameters</th>
                  <th>Train Trades</th>
                  <th>Train Net P&amp;L</th>
                  <th>Train PF</th>
                  <th>Validation Trades</th>
                  <th>Validation Net P&amp;L</th>
                  <th>Validation PF</th>
                </tr>
              </thead>
              <tbody>
                {allTrials
                  .slice()
                  .sort((a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity) || Number(b.train_net_pnl) - Number(a.train_net_pnl))
                  .map((t, i) => (
                    <tr key={i}>
                      <td>{t.rank ?? '—'}</td>
                      <td className="text-col">{Object.entries(t.params).map(([k, v]) => `${k}=${v}`).join(', ')}</td>
                      <td>{int(t.train_trades)}</td>
                      <td>{money(t.train_net_pnl)}</td>
                      <td>{num(t.train_profit_factor)}</td>
                      <td>{t.validation_trades !== null ? int(t.validation_trades) : '—'}</td>
                      <td>{t.validation_net_pnl !== null ? money(t.validation_net_pnl) : '—'}</td>
                      <td>{num(t.validation_profit_factor)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <p style={{ marginTop: 8, fontSize: 12, color: 'var(--text-faint)' }}>
            Judge only by the validation columns — the training columns reward overfitting by construction.
          </p>
        </div>
      )}

      {allTrials && allTrials.length > 0 && (
        <div className="panel">
          <h3>Parameter Heatmap (all combinations tried, training score)</h3>
          <ParameterHeatmap trials={allTrials} />
        </div>
      )}
    </div>
  )
}
