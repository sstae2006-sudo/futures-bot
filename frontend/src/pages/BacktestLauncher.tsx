import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  getBacktest, listDatasets, listModels, listStrategies, runBacktest, submitAiBacktestComparison,
  submitBacktestJob,
} from '../api'
import { useApi } from '../useApi'
import { useJobStream } from '../useJobStream'
import { LoadingState, ErrorState, StatTile } from '../components/UI'
import { JobProgressBar } from '../components/JobProgress'
import BacktestResults from '../components/BacktestResults'
import { money, num, tone } from '../format'
import type { DerivedBacktestMetrics, RunDetail, StrategyParam } from '../types'

export default function BacktestLauncher() {
  const { runId } = useParams()
  if (runId) return <BacktestDetailView runId={runId} />
  return <BacktestForm />
}

function BacktestDetailView({ runId }: { runId: string }) {
  const run = useApi(() => getBacktest(runId), [runId])
  return (
    <div>
      <div className="page-header">
        <h1>Backtest Result</h1>
        {run.data && <p>{run.data.strategy} on {run.data.contract} — run {run.data.id}</p>}
      </div>
      {run.loading && <LoadingState label="Loading run…" />}
      {run.error && <ErrorState message={run.error} onRetry={run.refetch} />}
      {run.data && <BacktestResults run={run.data} />}
    </div>
  )
}

function BacktestForm() {
  const navigate = useNavigate()
  const strategies = useApi(listStrategies)
  const datasets = useApi(listDatasets)

  const [strategyName, setStrategyName] = useState('')
  const [dataset, setDataset] = useState('')
  const [contract, setContract] = useState('MES')
  const [walkForward, setWalkForward] = useState(false)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [startingCash, setStartingCash] = useState('')
  const [stopLoss, setStopLoss] = useState('')
  const [takeProfit, setTakeProfit] = useState('')
  const [contractsPerTrade, setContractsPerTrade] = useState('')
  const [params, setParams] = useState<Record<string, string>>({})

  const [runInBackground, setRunInBackground] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<RunDetail | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const streamedJob = useJobStream(jobId)

  // Phase 9: Backtest + AI -- optionally filter entries through a trained model.
  const models = useApi(() => (strategyName ? listModels({ strategy: strategyName }) : Promise.resolve([])), [strategyName])
  const finishedModels = useMemo(() => (models.data ?? []).filter((m) => m.status === 'finished'), [models.data])
  const [mlModelId, setMlModelId] = useState('')
  const [mlThreshold, setMlThreshold] = useState('0.5')
  const [comparing, setComparing] = useState(false)
  const [comparisonError, setComparisonError] = useState<string | null>(null)
  const [comparisonJobId, setComparisonJobId] = useState<string | null>(null)
  const comparisonJob = useJobStream(comparisonJobId)
  const comparison = comparisonJob?.status === 'completed' ? (comparisonJob.result_payload as unknown as DerivedBacktestMetrics | null) : null

  // Once a background job completes, fetch the full result the same way
  // the synchronous path already renders it.
  useEffect(() => {
    if (streamedJob?.status === 'completed' && streamedJob.result_id) {
      getBacktest(streamedJob.result_id).then(setResult).catch((err) => setError(err instanceof Error ? err.message : 'Could not load the result.'))
    }
    if (streamedJob?.status === 'failed') {
      setError(streamedJob.error_message ?? 'The backtest job failed.')
    }
  }, [streamedJob])

  // Default the strategy/dataset selects once their options arrive.
  useEffect(() => {
    if (!strategyName && strategies.data && strategies.data.length > 0) {
      setStrategyName(strategies.data[0].name)
    }
  }, [strategies.data, strategyName])
  useEffect(() => {
    if (!dataset && datasets.data && datasets.data.length > 0) {
      setDataset(datasets.data[0].filename)
    }
  }, [datasets.data, dataset])

  const currentSchema: StrategyParam[] = strategies.data?.find((s) => s.name === strategyName)?.parameters ?? []

  // Reset param overrides to the new strategy's defaults whenever the
  // strategy selection changes, so a stale param from a previous strategy
  // never gets silently submitted for one that doesn't use it.
  useEffect(() => {
    const defaults: Record<string, string> = {}
    for (const p of currentSchema) defaults[p.name] = p.default === null ? '' : String(p.default)
    setParams(defaults)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyName])

  function coerceParam(p: StrategyParam, raw: string): unknown {
    if (raw === '') return undefined
    if (p.type === 'boolean') return raw === 'true'
    if (p.type === 'int') return parseInt(raw, 10)
    if (p.type === 'number') return parseFloat(raw)
    return raw
  }

  function buildRequestBody() {
    const strategy_params: Record<string, unknown> = {}
    for (const p of currentSchema) {
      const coerced = coerceParam(p, params[p.name] ?? '')
      if (coerced !== undefined) strategy_params[p.name] = coerced
    }
    return {
      strategy_name: strategyName,
      dataset,
      contract,
      strategy_params,
      walk_forward: walkForward,
      start: startDate || undefined,
      end: endDate || undefined,
      starting_cash: startingCash ? Number(startingCash) : undefined,
      stop_loss_points: stopLoss ? Number(stopLoss) : undefined,
      take_profit_points: takeProfit ? Number(takeProfit) : undefined,
      contracts_per_trade: contractsPerTrade ? Number(contractsPerTrade) : undefined,
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setRunning(true)
    setError(null)
    setResult(null)
    setJobId(null)
    try {
      const body = buildRequestBody()
      if (runInBackground) {
        const job = await submitBacktestJob(body)
        setJobId(job.id)
      } else {
        const run = await runBacktest(body)
        setResult(run)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The backtest failed to run.')
    } finally {
      setRunning(false)
    }
  }

  async function handleCompareWithAi() {
    if (!mlModelId) return
    setComparing(true)
    setComparisonError(null)
    setComparisonJobId(null)
    try {
      const job = await submitAiBacktestComparison({
        backtest: buildRequestBody(), ml_model_id: mlModelId, ml_min_win_probability: Number(mlThreshold) || 0.5,
      })
      setComparisonJobId(job.id)
    } catch (err) {
      setComparisonError(err instanceof Error ? err.message : 'The AI comparison failed to run.')
    } finally {
      setComparing(false)
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Backtest Launcher</h1>
        <p>Configure and run a backtest against historical data.</p>
      </div>

      {(strategies.loading || datasets.loading) && <LoadingState label="Loading strategies and datasets…" />}
      {strategies.error && <ErrorState message={strategies.error} onRetry={strategies.refetch} />}
      {datasets.error && <ErrorState message={datasets.error} onRetry={datasets.refetch} />}

      {strategies.data && datasets.data && (
        <form className="panel" onSubmit={handleSubmit}>
          <div className="field-row">
            <div className="field">
              <label htmlFor="strategy">Strategy</label>
              <select id="strategy" value={strategyName} onChange={(e) => setStrategyName(e.target.value)}>
                {strategies.data.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="dataset">Dataset</label>
              <select id="dataset" value={dataset} onChange={(e) => setDataset(e.target.value)}>
                {datasets.data.map((d) => (
                  <option key={d.filename} value={d.filename}>
                    {d.filename} {d.bars_hint !== null ? `(${d.bars_hint.toLocaleString()} bars)` : ''}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="contract">Contract</label>
              <select id="contract" value={contract} onChange={(e) => setContract(e.target.value)}>
                {['MES', 'MNQ', 'M2K', 'MYM'].map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="start">Start Date</label>
              <input id="start" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="end">End Date</label>
              <input id="end" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
            </div>
          </div>

          <h3 style={{ marginTop: 16 }}>Risk &amp; Sizing</h3>
          <div className="field-row">
            <div className="field">
              <label htmlFor="cash">Starting Balance</label>
              <input id="cash" type="number" placeholder="from config.yaml" value={startingCash} onChange={(e) => setStartingCash(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="contracts">Contracts</label>
              <input id="contracts" type="number" placeholder="from config.yaml" value={contractsPerTrade} onChange={(e) => setContractsPerTrade(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="sl">Stop Loss (pts)</label>
              <input id="sl" type="number" placeholder="from config.yaml" value={stopLoss} onChange={(e) => setStopLoss(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="tp">Take Profit (pts)</label>
              <input id="tp" type="number" placeholder="from config.yaml" value={takeProfit} onChange={(e) => setTakeProfit(e.target.value)} />
            </div>
          </div>

          {currentSchema.length > 0 && (
            <>
              <h3 style={{ marginTop: 16 }}>Strategy Parameters</h3>
              <div className="field-row">
                {currentSchema.map((p) => (
                  <div className="field" key={p.name}>
                    <label htmlFor={`param-${p.name}`} title={p.description ?? undefined}>{p.name}</label>
                    {p.type === 'boolean' ? (
                      <select
                        id={`param-${p.name}`}
                        value={params[p.name] ?? ''}
                        onChange={(e) => setParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                      >
                        <option value="true">true</option>
                        <option value="false">false</option>
                      </select>
                    ) : (
                      <input
                        id={`param-${p.name}`}
                        type={p.type === 'int' || p.type === 'number' ? 'number' : 'text'}
                        value={params[p.name] ?? ''}
                        onChange={(e) => setParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                      />
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          <div className="checkbox-field" style={{ margin: '16px 0 8px' }}>
            <input id="wf" type="checkbox" checked={walkForward} onChange={(e) => setWalkForward(e.target.checked)} />
            <label htmlFor="wf" style={{ margin: 0, textTransform: 'none', fontSize: 13, color: 'var(--text)' }}>
              Walk-forward (70/30 train/validation split)
            </label>
          </div>
          <div className="checkbox-field" style={{ margin: '0 0 16px' }}>
            <input id="bg" type="checkbox" checked={runInBackground} onChange={(e) => setRunInBackground(e.target.checked)} />
            <label htmlFor="bg" style={{ margin: 0, textTransform: 'none', fontSize: 13, color: 'var(--text)' }}>
              Run in background with live progress (see also the Jobs page)
            </label>
          </div>

          <button className="btn" type="submit" disabled={running || !strategyName || !dataset}>
            {running ? 'Submitting…' : 'Run Backtest'}
          </button>
        </form>
      )}

      {error && <ErrorState message={error} />}

      {finishedModels.length > 0 && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Backtest + AI</h3>
          <p style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
            Optionally filter entries through a trained model (see ML Research) and compare the result against
            an identical backtest with no filter.
          </p>
          <div className="field-row">
            <div className="field">
              <label htmlFor="ml-model">Model</label>
              <select id="ml-model" value={mlModelId} onChange={(e) => setMlModelId(e.target.value)}>
                <option value="">None</option>
                {finishedModels.map((m) => <option key={m.id} value={m.id}>{m.model_type.replace(/_/g, ' ')} v{m.version}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="ml-threshold">Min win probability</label>
              <input id="ml-threshold" type="number" min="0" max="1" step="0.05" value={mlThreshold} onChange={(e) => setMlThreshold(e.target.value)} />
            </div>
          </div>
          <button
            className="btn btn-secondary" type="button" disabled={!mlModelId || !dataset || comparing}
            onClick={handleCompareWithAi}
          >
            {comparing ? 'Submitting…' : 'Compare With / Without AI'}
          </button>
          {comparisonError && <ErrorState message={comparisonError} />}
          {comparisonJobId && comparisonJob && comparisonJob.status !== 'completed' && (
            <div style={{ marginTop: 12 }}><JobProgressBar job={comparisonJob} /></div>
          )}
          {comparison && (
            <div style={{ marginTop: 12 }}>
              <div className="grid grid-2">
                <div>
                  <h4>Without AI</h4>
                  <div className="grid grid-stats">
                    <StatTile label="Trades" value={comparison.without_ai.trade_count ?? '—'} />
                    <StatTile label="Net P&L" value={money(comparison.without_ai.net_pnl)} />
                    <StatTile label="Sharpe" value={comparison.without_ai.sharpe_ratio ?? '—'} />
                  </div>
                </div>
                <div>
                  <h4>With AI</h4>
                  <div className="grid grid-stats">
                    <StatTile label="Trades" value={comparison.with_ai.trade_count ?? '—'} />
                    <StatTile label="Net P&L" value={money(comparison.with_ai.net_pnl)} />
                    <StatTile label="Sharpe" value={comparison.with_ai.sharpe_ratio ?? '—'} />
                  </div>
                </div>
              </div>
              <h4 style={{ marginBottom: 4 }}>Impact of AI filtering</h4>
              <p style={{ fontSize: 12.5, color: 'var(--text-dim)', marginTop: 0 }}>
                A good ROC AUC doesn't guarantee this filter helps the strategy — these five numbers say
                whether it actually did.
              </p>
              <div className="grid grid-stats">
                <StatTile
                  label="P&L Improvement" value={money(comparison.pnl_improvement)}
                  tone={tone(comparison.pnl_improvement)}
                />
                <StatTile
                  label="Profit Factor Δ" value={num(comparison.profit_factor_improvement)}
                  tone={tone(comparison.profit_factor_improvement)}
                />
                <StatTile
                  label="Expectancy Δ" value={money(comparison.expectancy_improvement)}
                  tone={tone(comparison.expectancy_improvement)}
                />
                <StatTile
                  label="Drawdown Reduction" value={money(comparison.drawdown_reduction)}
                  tone={tone(comparison.drawdown_reduction)}
                />
                <StatTile
                  label="Trades Retained"
                  value={`${comparison.trade_count_retained} (${comparison.trade_count_retained_pct !== null ? comparison.trade_count_retained_pct.toFixed(0) : '—'}%)`}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {jobId && streamedJob && streamedJob.status !== 'completed' && (
        <div className="panel">
          <h3>Progress</h3>
          <JobProgressBar job={streamedJob} />
        </div>
      )}

      {result && (
        <>
          <div className="page-header" style={{ marginTop: 24 }}>
            <h2>Results</h2>
            <button className="btn btn-secondary" onClick={() => navigate(`/backtest/${result.id}`)}>
              Open permanent link
            </button>
          </div>
          <BacktestResults run={result} />
        </>
      )}
    </div>
  )
}
