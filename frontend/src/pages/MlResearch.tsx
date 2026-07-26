import { useEffect, useMemo, useState } from 'react'
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import {
  archiveModel, computeModelBacktestMetrics, deleteModel, deployModel, getCorrelation, getDeployment,
  getFeatureDistribution, getMlDatasetHealth, getModelVersions, listDatasets, listModels,
  listStrategies, listTrades, mlDatasetExportUrl, predictTrade, rollbackModel, stopModel,
  submitModelTraining, unarchiveModel, updateModelNotes,
} from '../api'
import { useApi } from '../useApi'
import { useJobStream } from '../useJobStream'
import { LoadingState, ErrorState, EmptyState, StatTile, Badge } from '../components/UI'
import { JobProgressBar } from '../components/JobProgress'
import { HistogramChart, FeatureScatterChart, ROCCurveChart, PRCurveChart, CalibrationChart } from '../components/Charts'
import { CorrelationHeatmap } from '../components/CorrelationHeatmap'
import { ConfusionMatrix } from '../components/ConfusionMatrix'
import { dateTime, money, num, tone } from '../format'
import type {
  ClassificationMetrics, DeploymentOut, DeploymentStatus, EvaluationMode, FeatureImportanceRow,
  MlModelOut, ModelType, PredictionResult,
} from '../types'

type Tab = 'dataset' | 'features' | 'correlation' | 'training' | 'comparison' | 'sandbox' | 'terminal'

const TABS: { key: Tab; label: string }[] = [
  { key: 'dataset', label: 'Dataset' },
  { key: 'features', label: 'Feature Explorer' },
  { key: 'correlation', label: 'Correlation' },
  { key: 'training', label: 'Models & Training' },
  { key: 'comparison', label: 'Comparison' },
  { key: 'sandbox', label: 'Prediction Sandbox' },
  { key: 'terminal', label: 'Terminal' },
]

const LINE_COLORS = ['#5b9dff', '#2ecc71', '#e2b93b', '#ff5c5c', '#a78bfa', '#38bdf8', '#fb923c', '#f472b6']

export default function MlResearch() {
  const strategies = useApi(listStrategies)
  const [strategy, setStrategy] = useState('')
  const [tab, setTab] = useState<Tab>('dataset')
  const [activeTrainingJobId, setActiveTrainingJobId] = useState<string | null>(null)
  const [terminalLines, setTerminalLines] = useState<string[]>([])

  useEffect(() => {
    if (!strategy && strategies.data && strategies.data.length > 0) setStrategy(strategies.data[0].name)
  }, [strategies.data, strategy])

  function appendTerminalLine(line: string) {
    setTerminalLines((lines) => [...lines.slice(-199), `${new Date().toLocaleTimeString()}  ${line}`])
  }

  return (
    <div>
      <div className="page-header">
        <h1>ML Research</h1>
        <p>Train, evaluate, compare, and deploy machine-learning models over your recorded trades — one strategy at a time.</p>
      </div>

      <div className="panel">
        <div className="field" style={{ maxWidth: 260 }}>
          <label htmlFor="ml-strategy">Strategy</label>
          <select id="ml-strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            {(strategies.data ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
        </div>
        <div className="pill-row" style={{ marginTop: 8, marginBottom: 0 }}>
          {TABS.map((t) => (
            <button key={t.key} type="button" className={`pill${tab === t.key ? ' active' : ''}`} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {strategies.loading && <LoadingState label="Loading strategies…" />}
      {strategies.error && <ErrorState message={strategies.error} onRetry={strategies.refetch} />}

      {strategy && tab === 'dataset' && <DatasetTab strategy={strategy} />}
      {strategy && tab === 'features' && <FeatureExplorerTab strategy={strategy} />}
      {strategy && tab === 'correlation' && <CorrelationTab strategy={strategy} />}
      {strategy && tab === 'training' && (
        <ModelsTrainingTab
          strategy={strategy}
          onTrainingStarted={(jobId) => setActiveTrainingJobId(jobId)}
          onTerminalLine={appendTerminalLine}
        />
      )}
      {strategy && tab === 'comparison' && <ComparisonTab strategy={strategy} />}
      {strategy && tab === 'sandbox' && <PredictionSandboxTab strategy={strategy} />}
      {tab === 'terminal' && <TerminalTab jobId={activeTrainingJobId} lines={terminalLines} />}
    </div>
  )
}

// --- Dataset ---

function DatasetTab({ strategy }: { strategy: string }) {
  const health = useApi(() => getMlDatasetHealth(strategy), [strategy])

  return (
    <div>
      {health.loading && <LoadingState label="Loading dataset health…" />}
      {health.error && <ErrorState message={health.error} onRetry={health.refetch} />}
      {health.data && (
        <>
          <div className="grid grid-stats" style={{ marginBottom: 16 }}>
            <StatTile label="Total Trades" value={health.data.trade_count} />
            <StatTile label="Wins" value={health.data.win_count} tone="good" />
            <StatTile label="Losses" value={health.data.loss_count} tone="bad" />
            <StatTile label="Win Rate" value={health.data.win_rate !== null ? `${(health.data.win_rate * 100).toFixed(1)}%` : '—'} />
            <StatTile label="Feature Count" value={health.data.feature_count} />
            <StatTile
              label="Missing Values"
              value={`${health.data.missing_value_count} (${(health.data.missing_value_ratio * 100).toFixed(1)}%)`}
            />
          </div>

          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Deduplication</h3>
            <p style={{ fontSize: 12.5, color: 'var(--text-dim)', marginTop: -4 }}>
              Rows across every backtest run recorded for this strategy, collapsed to one row per unique
              market opportunity before training (see below) — a rerun or overlapping walk-forward window
              must not double-count the same trade.
            </p>
            <div className="grid grid-stats">
              <StatTile label="Total Rows (all runs)" value={health.data.total_rows} />
              <StatTile label="Unique Timestamps" value={health.data.unique_timestamps} />
              <StatTile
                label="Duplicate Market Events"
                value={health.data.duplicate_market_events}
                tone={health.data.duplicate_market_events > 0 ? 'bad' : 'neutral'}
              />
              <StatTile label="Final Training Dataset Size" value={health.data.trade_count} tone="good" />
            </div>
          </div>

          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Dataset Health</h3>
            <p>
              <Badge tone={health.data.status === 'READY' ? 'good' : health.data.status === 'WARNING' ? 'warn' : 'bad'}>
                {health.data.status.replace(/_/g, ' ')}
              </Badge>
            </p>
            {health.data.date_range && (
              <p style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
                Date range: {dateTime(health.data.date_range[0])} — {dateTime(health.data.date_range[1])}
              </p>
            )}
            {health.data.reasons.length > 0 && (
              <ul style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
                {health.data.reasons.map((r) => <li key={r}>{r}</li>)}
              </ul>
            )}
            <a
              className="btn btn-secondary" href={mlDatasetExportUrl(strategy)}
              style={{ display: 'inline-block', marginTop: 8, textDecoration: 'none' }}
            >
              Export dataset (CSV)
            </a>
          </div>
        </>
      )}
    </div>
  )
}

// --- Feature Explorer ---

function FeatureExplorerTab({ strategy }: { strategy: string }) {
  const correlation = useApi(() => getCorrelation(strategy), [strategy])
  const models = useApi(() => listModels({ strategy }), [strategy])
  const [selected, setSelected] = useState<string | null>(null)
  const distribution = useApi(
    () => (selected ? getFeatureDistribution(strategy, selected) : Promise.resolve(null)),
    [strategy, selected],
  )

  const selectedRow = useMemo(
    () => correlation.data?.find((r) => r.feature === selected) ?? null,
    [correlation.data, selected],
  )
  const latestFinished = useMemo(
    () => (models.data ?? []).find((m) => m.status === 'finished') ?? null,
    [models.data],
  )
  const importanceRow = useMemo(
    () => latestFinished?.feature_importance?.find((f) => f.feature === selected) ?? null,
    [latestFinished, selected],
  )

  return (
    <div>
      {correlation.loading && <LoadingState label="Loading features…" />}
      {correlation.error && <ErrorState message={correlation.error} onRetry={correlation.refetch} />}
      {correlation.data && correlation.data.length === 0 && (
        <EmptyState label="No features yet — record more trades for this strategy." />
      )}
      {correlation.data && correlation.data.length > 0 && (
        <div className="grid grid-2">
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Features</h3>
            <div className="pill-row">
              {correlation.data.map((r) => (
                <button
                  key={r.feature} type="button" className={`pill${selected === r.feature ? ' active' : ''}`}
                  onClick={() => setSelected(r.feature)}
                >
                  {r.feature}
                </button>
              ))}
            </div>
          </div>

          <div className="panel">
            <h3 style={{ marginTop: 0 }}>{selected ?? 'Select a feature'}</h3>
            {!selected && <EmptyState label="Click a feature on the left to see its distribution and stats." />}
            {selected && distribution.loading && <LoadingState label="Loading distribution…" />}
            {selected && distribution.data && (
              <div>
                <div className="grid grid-stats" style={{ marginBottom: 12 }}>
                  <StatTile label="Mean" value={distribution.data.mean !== null ? distribution.data.mean.toFixed(3) : '—'} />
                  <StatTile label="Std Dev" value={distribution.data.std !== null ? distribution.data.std.toFixed(3) : '—'} />
                  <StatTile label="Min" value={distribution.data.min !== null ? distribution.data.min.toFixed(3) : '—'} />
                  <StatTile label="Max" value={distribution.data.max !== null ? distribution.data.max.toFixed(3) : '—'} />
                  <StatTile label="Win Avg" value={distribution.data.win_average !== null ? distribution.data.win_average.toFixed(3) : '—'} tone="good" />
                  <StatTile label="Loss Avg" value={distribution.data.loss_average !== null ? distribution.data.loss_average.toFixed(3) : '—'} tone="bad" />
                </div>
                {selectedRow && (
                  <p style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
                    Correlation with win/loss: <strong>{selectedRow.corr_vs_win?.toFixed(3) ?? '—'}</strong>
                    {' · '}with net P&amp;L: <strong>{selectedRow.corr_vs_pnl?.toFixed(3) ?? '—'}</strong>
                  </p>
                )}
                {importanceRow && (
                  <p style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
                    Feature importance (from {latestFinished?.model_type.replace(/_/g, ' ')} v{latestFinished?.version}):{' '}
                    <strong>{importanceRow.importance.toFixed(4)}</strong>
                  </p>
                )}
                {distribution.data.bins.length > 0 ? (
                  <HistogramChart
                    bins={distribution.data.bins} counts={distribution.data.counts}
                    winCounts={distribution.data.win_counts} lossCounts={distribution.data.loss_counts}
                  />
                ) : (
                  <EmptyState label="Not enough variation in this feature to plot a distribution." />
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// --- Correlation ---

type CorrKey = 'corr_vs_win' | 'corr_vs_pnl' | 'corr_vs_r'

function CorrelationTab({ strategy }: { strategy: string }) {
  const correlation = useApi(() => getCorrelation(strategy), [strategy])
  const [sortBy, setSortBy] = useState<CorrKey>('corr_vs_win')

  const sorted = useMemo(() => {
    if (!correlation.data) return []
    return [...correlation.data].sort((a, b) => Math.abs(b[sortBy] ?? 0) - Math.abs(a[sortBy] ?? 0))
  }, [correlation.data, sortBy])

  const positive = useMemo(
    () => sorted.filter((r) => (r[sortBy] ?? 0) > 0).slice(0, 5),
    [sorted, sortBy],
  )
  const negative = useMemo(
    () => [...sorted].filter((r) => (r[sortBy] ?? 0) < 0).sort((a, b) => (a[sortBy] ?? 0) - (b[sortBy] ?? 0)).slice(0, 5),
    [sorted, sortBy],
  )

  return (
    <div>
      {correlation.loading && <LoadingState label="Computing correlations…" />}
      {correlation.error && <ErrorState message={correlation.error} onRetry={correlation.refetch} />}
      {correlation.data && correlation.data.length === 0 && <EmptyState label="No features yet." />}
      {correlation.data && correlation.data.length > 0 && (
        <>
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Heatmap</h3>
            <CorrelationHeatmap rows={correlation.data} />
          </div>

          <div className="panel">
            <div className="field" style={{ maxWidth: 220 }}>
              <label htmlFor="corr-sort">Rank by</label>
              <select id="corr-sort" value={sortBy} onChange={(e) => setSortBy(e.target.value as CorrKey)}>
                <option value="corr_vs_win">Win/Loss</option>
                <option value="corr_vs_pnl">Net P&amp;L</option>
                <option value="corr_vs_r">R Multiple</option>
              </select>
            </div>
          </div>

          <div className="grid grid-2">
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>Top Positive</h3>
              {positive.length === 0 && <EmptyState label="None." />}
              {positive.length > 0 && (
                <table>
                  <thead><tr><th>Feature</th><th>Value</th></tr></thead>
                  <tbody>
                    {positive.map((r) => (
                      <tr key={r.feature}><td className="text-col">{r.feature}</td><td className="tone-good">{r[sortBy]?.toFixed(3)}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>Top Negative</h3>
              {negative.length === 0 && <EmptyState label="None." />}
              {negative.length > 0 && (
                <table>
                  <thead><tr><th>Feature</th><th>Value</th></tr></thead>
                  <tbody>
                    {negative.map((r) => (
                      <tr key={r.feature}><td className="text-col">{r.feature}</td><td className="tone-bad">{r[sortBy]?.toFixed(3)}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          <div className="panel">
            <h3 style={{ marginTop: 0 }}>All Features</h3>
            <div className="table-scroll">
              <table>
                <thead><tr><th>Feature</th><th>Win/Loss</th><th>Net P&amp;L</th><th>R Multiple</th></tr></thead>
                <tbody>
                  {sorted.map((r) => (
                    <tr key={r.feature}>
                      <td className="text-col">{r.feature}</td>
                      <td>{r.corr_vs_win?.toFixed(3) ?? '—'}</td>
                      <td>{r.corr_vs_pnl?.toFixed(3) ?? '—'}</td>
                      <td>{r.corr_vs_r?.toFixed(3) ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// --- Models & Training ---

const MODEL_TYPES: { value: ModelType; label: string }[] = [
  { value: 'logistic_regression', label: 'Logistic Regression' },
  { value: 'random_forest', label: 'Random Forest' },
  { value: 'xgboost', label: 'XGBoost' },
  { value: 'neural_network', label: 'Neural Network' },
]

function defaultHyperparameters(modelType: ModelType): Record<string, unknown> {
  switch (modelType) {
    case 'random_forest':
      return { n_estimators: 200 }
    case 'xgboost':
      return { n_estimators: 200, max_depth: 4, learning_rate: 0.1 }
    case 'neural_network':
      return { epochs: 50, learning_rate: 0.001, hidden_sizes: [32, 16] }
    default:
      return { max_iter: 1000, C: 1.0 }
  }
}

function ModelsTrainingTab({
  strategy, onTrainingStarted, onTerminalLine,
}: {
  strategy: string
  onTrainingStarted: (jobId: string) => void
  onTerminalLine: (line: string) => void
}) {
  const [showArchived, setShowArchived] = useState(false)
  const models = useApi(() => listModels({ strategy, include_archived: showArchived }), [strategy, showArchived])
  const deployment = useApi(() => getDeployment(strategy), [strategy])

  const [modelType, setModelType] = useState<ModelType>('random_forest')
  const [evaluationMode, setEvaluationMode] = useState<EvaluationMode>('chronological_split')
  const [hyperparamsText, setHyperparamsText] = useState(JSON.stringify(defaultHyperparameters('random_forest')))
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [trainingJobId, setTrainingJobId] = useState<string | null>(null)
  const streamedJob = useJobStream(trainingJobId)

  useEffect(() => {
    setHyperparamsText(JSON.stringify(defaultHyperparameters(modelType)))
  }, [modelType])

  useEffect(() => {
    if (!streamedJob) return
    if (streamedJob.progress_message) onTerminalLine(streamedJob.progress_message)
    if (streamedJob.status === 'completed' || streamedJob.status === 'failed') {
      models.refetch()
      deployment.refetch()
      onTerminalLine(streamedJob.status === 'completed' ? 'Completed.' : `Failed: ${streamedJob.error_message ?? 'unknown error'}`)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamedJob?.status, streamedJob?.progress_message])

  async function handleTrain(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setSubmitError(null)
    try {
      let hyperparameters: Record<string, unknown>
      try {
        hyperparameters = JSON.parse(hyperparamsText)
      } catch {
        throw new Error('Hyperparameters must be valid JSON.')
      }
      onTerminalLine(`Loading dataset for ${strategy}…`)
      const resp = await submitModelTraining({ strategy, model_type: modelType, hyperparameters, evaluation_mode: evaluationMode })
      setTrainingJobId(resp.job_id)
      onTrainingStarted(resp.job_id)
      onTerminalLine(`Training ${modelType.replace(/_/g, ' ')} (${evaluationMode})…`)
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Could not submit training job.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDeploy(modelId: string) {
    await deployModel(modelId)
    deployment.refetch()
  }

  async function handleRollback(modelId: string) {
    await rollbackModel(modelId, strategy)
    deployment.refetch()
  }

  async function handleStop(modelId: string) {
    await stopModel(modelId)
    models.refetch()
  }

  async function handleArchive(modelId: string, archived: boolean) {
    if (archived) await archiveModel(modelId)
    else await unarchiveModel(modelId)
    models.refetch()
  }

  async function handleDelete(modelId: string) {
    if (!window.confirm('Delete this model permanently? This cannot be undone.')) return
    await deleteModel(modelId)
    models.refetch()
  }

  async function handleSaveNotes(modelId: string, notes: string) {
    await updateModelNotes(modelId, notes)
    models.refetch()
  }

  return (
    <div>
      <form className="panel" onSubmit={handleTrain}>
        <h3 style={{ marginTop: 0 }}>Train a Model</h3>
        <div className="field-row">
          <div className="field">
            <label htmlFor="model-type">Model type</label>
            <select id="model-type" value={modelType} onChange={(e) => setModelType(e.target.value as ModelType)}>
              {MODEL_TYPES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="eval-mode">Evaluation mode</label>
            <select id="eval-mode" value={evaluationMode} onChange={(e) => setEvaluationMode(e.target.value as EvaluationMode)}>
              <option value="chronological_split">Chronological split (train / validation / test)</option>
              <option value="walk_forward">Walk-forward (rolling folds)</option>
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="hyperparams">Hyperparameters (JSON)</label>
          <input id="hyperparams" className="mono" value={hyperparamsText} onChange={(e) => setHyperparamsText(e.target.value)} />
        </div>
        <button className="btn" type="submit" disabled={submitting}>{submitting ? 'Submitting…' : 'Train'}</button>
        {submitError && <ErrorState message={submitError} />}
        {streamedJob && <div style={{ marginTop: 12 }}><JobProgressBar job={streamedJob} /></div>}
      </form>

      {deployment.data && <DeploymentPanel status={deployment.data} onRollback={handleRollback} />}

      <div className="panel">
        <label className="checkbox-field">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Show archived
        </label>
      </div>

      {models.loading && <LoadingState label="Loading models…" />}
      {models.error && <ErrorState message={models.error} onRetry={models.refetch} />}
      {models.data && models.data.length === 0 && <EmptyState label="No models trained yet for this strategy." />}

      {(models.data ?? []).map((m) => (
        <ModelReportCard
          key={m.id} model={m} strategy={strategy}
          isDeployed={deployment.data?.current?.model_id === m.id}
          onDeploy={() => handleDeploy(m.id)}
          onStop={() => handleStop(m.id)}
          onArchive={(archived) => handleArchive(m.id, archived)}
          onDelete={() => handleDelete(m.id)}
          onSaveNotes={(notes) => handleSaveNotes(m.id, notes)}
        />
      ))}
    </div>
  )
}

function DeploymentPanel({ status, onRollback }: { status: DeploymentStatus; onRollback: (modelId: string) => void }) {
  return (
    <div className="panel">
      <h3 style={{ marginTop: 0 }}>Deployment</h3>
      <p>
        Currently deployed:{' '}
        {status.current ? (
          <Badge tone="good">{status.current.model_id}</Badge>
        ) : (
          <Badge tone="neutral">none</Badge>
        )}
      </p>
      {status.history.length > 0 && (
        <table>
          <thead><tr><th>Action</th><th>Model</th><th>When</th><th /></tr></thead>
          <tbody>
            {status.history.map((h: DeploymentOut) => (
              <tr key={h.id}>
                <td>{h.action}</td>
                <td className="mono">{h.model_id ?? '—'}</td>
                <td>{dateTime(h.created_at)}</td>
                <td>
                  {h.model_id && h.model_id !== status.current?.model_id && (
                    <button className="btn btn-secondary" type="button" onClick={() => onRollback(h.model_id!)}>Rollback to this</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function ModelReportCard({
  model, strategy, isDeployed, onDeploy, onStop, onArchive, onDelete, onSaveNotes,
}: {
  model: MlModelOut
  strategy: string
  isDeployed: boolean
  onDeploy: () => void
  onStop: () => void
  onArchive: (archived: boolean) => void
  onDelete: () => void
  onSaveNotes: (notes: string) => void
}) {
  const [notesDraft, setNotesDraft] = useState(model.notes ?? '')
  const [datasetForBacktest, setDatasetForBacktest] = useState('')
  const datasets = useApi(listDatasets)

  const statusTone = model.status === 'finished' ? 'good'
    : model.status === 'failed' ? 'bad'
    : model.status === 'stopped' ? 'warn'
    : 'neutral'

  async function handleComputeBacktestMetrics() {
    if (!datasetForBacktest) return
    await computeModelBacktestMetrics(model.id, datasetForBacktest)
  }

  const diagnostics = model.metrics?.diagnostics
  const outOfSampleTitle = model.evaluation_mode === 'walk_forward' ? 'Out-of-Sample (Walk-Forward)' : 'Out-of-Sample (Validation)'
  const outOfSampleMetrics = model.evaluation_mode === 'walk_forward' ? model.metrics?.walk_forward_out_of_sample : model.metrics?.validation

  return (
    <div className="panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h3 style={{ marginBottom: 4, marginTop: 0 }}>
            {model.model_type.replace(/_/g, ' ')} · v{model.version}{' '}
            {isDeployed && <Badge tone="good">deployed</Badge>}
          </h3>
          <p style={{ fontSize: 11.5, color: 'var(--text-faint)', margin: 0 }}>
            {strategy} · trained {dateTime(model.created_at)} · dataset v{model.dataset_version.slice(0, 8)} · {model.dataset_size} trades
          </p>
        </div>
        <Badge tone={statusTone}>{model.status}</Badge>
      </div>

      {model.overfit_warning && (
        <div className="caveats" style={{ marginTop: 8 }}>
          <h3>Overfitting Warning</h3>
          <p style={{ margin: 0 }}>{model.overfit_note}</p>
        </div>
      )}

      {model.status === 'failed' && model.error_message && <ErrorState message={model.error_message} />}

      {model.metrics && (
        <div className="grid grid-2" style={{ marginTop: 12 }}>
          <MetricsPanel title="In-Sample (Train)" metrics={model.metrics.train} />
          <MetricsPanel title={outOfSampleTitle} metrics={outOfSampleMetrics} />
        </div>
      )}
      {model.metrics?.test && (
        <div style={{ marginTop: 8 }}>
          <MetricsPanel title="Out-of-Sample (Test)" metrics={model.metrics.test} />
        </div>
      )}

      {diagnostics && (
        <div className="grid grid-2" style={{ marginTop: 12 }}>
          <div>
            <h4>Confusion Matrix (validation)</h4>
            <ConfusionMatrix {...diagnostics.confusion_matrix} />
          </div>
          {diagnostics.roc_curve && (
            <div>
              <h4>ROC Curve{outOfSampleMetrics?.roc_auc != null ? ` (AUC ${outOfSampleMetrics.roc_auc.toFixed(3)})` : ''}</h4>
              <ROCCurveChart fpr={diagnostics.roc_curve.fpr} tpr={diagnostics.roc_curve.tpr} />
            </div>
          )}
          {diagnostics.pr_curve && (
            <div>
              <h4>Precision-Recall Curve</h4>
              <PRCurveChart precision={diagnostics.pr_curve.precision} recall={diagnostics.pr_curve.recall} />
            </div>
          )}
          {diagnostics.calibration_curve && (
            <div>
              <h4>Calibration</h4>
              <CalibrationChart predicted={diagnostics.calibration_curve.predicted} actual={diagnostics.calibration_curve.actual} />
            </div>
          )}
        </div>
      )}

      {model.feature_importance && model.feature_importance.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <h4>Feature Importance</h4>
          <FeatureImportanceBars rows={model.feature_importance} />
        </div>
      )}

      <details style={{ marginTop: 12 }}>
        <summary style={{ cursor: 'pointer', fontSize: 12.5, color: 'var(--text-dim)' }}>Hyperparameters &amp; provenance</summary>
        <dl className="detail-list">
          <Row label="Hyperparameters" value={JSON.stringify(model.hyperparameters)} />
          <Row label="Feature columns" value={`${model.feature_columns.length}: ${model.feature_columns.join(', ')}`} />
          <Row label="App version" value={model.app_version ?? '—'} />
          <Row label="Git commit" value={model.git_commit ? model.git_commit.slice(0, 10) : '—'} />
        </dl>
      </details>

      {model.derived_backtest_metrics && (
        <div style={{ marginTop: 12 }}>
          <h4>Backtest Impact (with vs. without AI filter)</h4>
          <div className="grid grid-2">
            <div>
              <h4 style={{ marginBottom: 4 }}>Without AI</h4>
              <dl className="detail-list">
                <Row label="Trades" value={String(model.derived_backtest_metrics.without_ai.trade_count ?? '—')} />
                <Row label="Net P&L" value={money(model.derived_backtest_metrics.without_ai.net_pnl)} />
                <Row label="Sharpe" value={model.derived_backtest_metrics.without_ai.sharpe_ratio ?? '—'} />
              </dl>
            </div>
            <div>
              <h4 style={{ marginBottom: 4 }}>With AI</h4>
              <dl className="detail-list">
                <Row label="Trades" value={String(model.derived_backtest_metrics.with_ai.trade_count ?? '—')} />
                <Row label="Net P&L" value={money(model.derived_backtest_metrics.with_ai.net_pnl)} />
                <Row label="Sharpe" value={model.derived_backtest_metrics.with_ai.sharpe_ratio ?? '—'} />
              </dl>
            </div>
          </div>
          <p style={{ fontSize: 11.5, color: 'var(--text-faint)', margin: '8px 0 0' }}>
            Good ROC AUC on the classifier doesn't guarantee the filter helps the strategy — these numbers say
            whether it actually did.
          </p>
          <div className="grid grid-stats">
            <StatTile
              label="P&L Improvement" value={money(model.derived_backtest_metrics.pnl_improvement)}
              tone={tone(model.derived_backtest_metrics.pnl_improvement)}
            />
            <StatTile
              label="Profit Factor Δ" value={num(model.derived_backtest_metrics.profit_factor_improvement)}
              tone={tone(model.derived_backtest_metrics.profit_factor_improvement)}
            />
            <StatTile
              label="Expectancy Δ" value={money(model.derived_backtest_metrics.expectancy_improvement)}
              tone={tone(model.derived_backtest_metrics.expectancy_improvement)}
            />
            <StatTile
              label="Drawdown Reduction" value={money(model.derived_backtest_metrics.drawdown_reduction)}
              tone={tone(model.derived_backtest_metrics.drawdown_reduction)}
            />
            <StatTile
              label="Trades Retained"
              value={`${model.derived_backtest_metrics.trade_count_retained} (${model.derived_backtest_metrics.trade_count_retained_pct !== null ? model.derived_backtest_metrics.trade_count_retained_pct.toFixed(0) : '—'}%)`}
            />
          </div>
        </div>
      )}

      <div className="field-row" style={{ marginTop: 12, alignItems: 'flex-end' }}>
        <div className="field" style={{ maxWidth: 220 }}>
          <label htmlFor={`bt-dataset-${model.id}`}>Compute backtest metrics on</label>
          <select id={`bt-dataset-${model.id}`} value={datasetForBacktest} onChange={(e) => setDatasetForBacktest(e.target.value)}>
            <option value="">Select dataset…</option>
            {(datasets.data ?? []).map((d) => <option key={d.filename} value={d.filename}>{d.filename}</option>)}
          </select>
        </div>
        <button className="btn btn-secondary" type="button" disabled={!datasetForBacktest} onClick={handleComputeBacktestMetrics}>
          Compute
        </button>
      </div>

      <div className="field" style={{ marginTop: 12 }}>
        <label htmlFor={`notes-${model.id}`}>Notes</label>
        <input id={`notes-${model.id}`} value={notesDraft} onChange={(e) => setNotesDraft(e.target.value)} />
      </div>

      <div className="pill-row" style={{ marginTop: 8 }}>
        <button className="btn btn-secondary" type="button" onClick={() => onSaveNotes(notesDraft)}>Save Notes</button>
        {(model.status === 'queued' || model.status === 'training') && (
          <button className="btn btn-secondary" type="button" onClick={onStop}>Stop</button>
        )}
        {model.status === 'finished' && !isDeployed && (
          <button className="btn" type="button" onClick={onDeploy}>Deploy</button>
        )}
        <button className="btn btn-secondary" type="button" onClick={() => onArchive(!model.archived)}>
          {model.archived ? 'Unarchive' : 'Archive'}
        </button>
        <button className="btn btn-secondary" type="button" onClick={onDelete} style={{ color: 'var(--bad)' }}>Delete</button>
      </div>
    </div>
  )
}

function MetricsPanel({ title, metrics }: { title: string; metrics?: ClassificationMetrics }) {
  return (
    <div className="panel" style={{ margin: 0 }}>
      <h4 style={{ marginTop: 0 }}>{title}</h4>
      {!metrics ? (
        <EmptyState label="Not available." />
      ) : (
        <div className="grid grid-stats">
          <StatTile label="Accuracy" value={metrics.accuracy.toFixed(3)} />
          <StatTile label="Precision" value={metrics.precision.toFixed(3)} />
          <StatTile label="Recall" value={metrics.recall.toFixed(3)} />
          <StatTile label="F1" value={metrics.f1.toFixed(3)} />
          <StatTile label="ROC AUC" value={metrics.roc_auc !== null ? metrics.roc_auc.toFixed(3) : '—'} />
          <StatTile label="Trades" value={metrics.trade_count} />
        </div>
      )}
    </div>
  )
}

function FeatureImportanceBars({ rows }: { rows: FeatureImportanceRow[] }) {
  const sorted = useMemo(() => [...rows].sort((a, b) => Math.abs(b.importance) - Math.abs(a.importance)).slice(0, 15), [rows])
  const max = Math.max(...sorted.map((r) => Math.abs(r.importance)), 0.0001)
  return (
    <div>
      {sorted.map((r, i) => (
        <div key={r.feature} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, fontSize: 11.5 }}>
          <span style={{ width: 22, color: 'var(--text-faint)' }}>#{i + 1}</span>
          <span className="mono" style={{ width: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.feature}>
            {r.feature}
          </span>
          <div style={{ flex: 1, background: 'var(--bg-panel-2)', height: 10, borderRadius: 3 }}>
            <div
              style={{
                width: `${Math.max(2, (Math.abs(r.importance) / max) * 100)}%`, height: '100%',
                background: r.importance >= 0 ? 'var(--good)' : 'var(--bad)', borderRadius: 3,
              }}
            />
          </div>
          <span className="mono" style={{ width: 60, textAlign: 'right' }}>{r.importance.toFixed(4)}</span>
        </div>
      ))}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid var(--border)', fontSize: 12.5, gap: 12 }}>
      <span style={{ color: 'var(--text-faint)' }}>{label}</span>
      <span className="mono" style={{ textAlign: 'right', wordBreak: 'break-word' }}>{value}</span>
    </div>
  )
}

// --- Comparison ---

function ComparisonTab({ strategy }: { strategy: string }) {
  const models = useApi(() => listModels({ strategy, include_archived: true }), [strategy])
  const finished = useMemo(() => (models.data ?? []).filter((m) => m.status === 'finished'), [models.data])

  function outOfSampleOf(m: MlModelOut) {
    return m.evaluation_mode === 'walk_forward' ? m.metrics?.walk_forward_out_of_sample : m.metrics?.validation
  }

  const best = useMemo(() => {
    let winner: MlModelOut | null = null
    let bestScore = -1
    for (const m of finished) {
      const score = outOfSampleOf(m)?.accuracy ?? -1
      if (score > bestScore) {
        winner = m
        bestScore = score
      }
    }
    return winner
  }, [finished])

  return (
    <div>
      {models.loading && <LoadingState label="Loading models…" />}
      {models.error && <ErrorState message={models.error} onRetry={models.refetch} />}
      {finished.length === 0 && <EmptyState label="No finished models yet for this strategy." />}

      {finished.length > 0 && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Model Comparison</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Model</th><th>Eval Mode</th><th>Accuracy</th><th>ROC AUC</th>
                  <th>Precision</th><th>Recall</th><th>F1</th><th>Dataset Ver</th><th>Features</th>
                  <th>Training Time</th><th>Sharpe (AI)</th><th>Overfit</th>
                </tr>
              </thead>
              <tbody>
                {finished.map((m) => {
                  const outOfSample = outOfSampleOf(m)
                  const isBest = best?.id === m.id
                  return (
                    <tr key={m.id} style={isBest ? { background: 'var(--good-bg)' } : undefined}>
                      <td className="text-col">
                        {m.model_type.replace(/_/g, ' ')} v{m.version} {isBest && <Badge tone="good">best</Badge>}
                      </td>
                      <td>{m.evaluation_mode === 'walk_forward' ? 'walk-forward' : 'chronological'}</td>
                      <td>{outOfSample ? outOfSample.accuracy.toFixed(3) : '—'}</td>
                      <td>{outOfSample?.roc_auc != null ? outOfSample.roc_auc.toFixed(3) : '—'}</td>
                      <td>{outOfSample ? outOfSample.precision.toFixed(3) : '—'}</td>
                      <td>{outOfSample ? outOfSample.recall.toFixed(3) : '—'}</td>
                      <td>{outOfSample ? outOfSample.f1.toFixed(3) : '—'}</td>
                      <td className="mono">{m.dataset_version.slice(0, 8)}</td>
                      <td>{m.feature_columns.length}</td>
                      <td>{m.metrics?.training_seconds !== undefined ? `${m.metrics.training_seconds.toFixed(1)}s` : '—'}</td>
                      <td>{m.derived_backtest_metrics?.with_ai.sharpe_ratio ?? '—'}</td>
                      <td>{m.overfit_warning ? <Badge tone="warn">yes</Badge> : <Badge tone="neutral">no</Badge>}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <FeatureImportanceHistory models={finished} />
    </div>
  )
}

function FeatureImportanceHistory({ models }: { models: MlModelOut[] }) {
  const families = useMemo(() => Array.from(new Set(models.map((m) => m.model_family))), [models])
  const [family, setFamily] = useState('')

  useEffect(() => {
    if ((!family || !families.includes(family)) && families.length > 0) setFamily(families[0])
  }, [families, family])

  const versions = useApi(() => (family ? getModelVersions(family) : Promise.resolve([])), [family])

  const featureNames = useMemo(() => {
    const names = new Set<string>()
    for (const v of versions.data ?? []) (v.feature_importance ?? []).forEach((f) => names.add(f.feature))
    return Array.from(names).slice(0, 8)
  }, [versions.data])

  const rows = useMemo(() => (versions.data ?? []).map((v) => {
    const row: Record<string, number | string> = { version: `v${v.version}` }
    for (const name of featureNames) {
      const match = (v.feature_importance ?? []).find((f) => f.feature === name)
      row[name] = match ? match.importance : 0
    }
    return row
  }), [versions.data, featureNames])

  if (families.length === 0) return null

  return (
    <div className="panel">
      <h3 style={{ marginTop: 0 }}>Feature Importance Over Time</h3>
      <div className="field" style={{ maxWidth: 320 }}>
        <label htmlFor="fi-family">Model family</label>
        <select id="fi-family" value={family} onChange={(e) => setFamily(e.target.value)}>
          {families.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
      </div>
      {rows.length > 1 ? (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="version" stroke="var(--text-faint)" tick={{ fontSize: 11 }} />
            <YAxis stroke="var(--text-faint)" tick={{ fontSize: 11 }} />
            <Tooltip contentStyle={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {featureNames.map((name, i) => (
              <Line key={name} type="monotone" dataKey={name} stroke={LINE_COLORS[i % LINE_COLORS.length]} strokeWidth={1.5} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      ) : (
        <EmptyState label="Train at least 2 versions of this model to see importance drift." />
      )}
    </div>
  )
}

// --- Prediction Sandbox ---

function PredictionSandboxTab({ strategy }: { strategy: string }) {
  const models = useApi(() => listModels({ strategy }), [strategy])
  const finished = useMemo(() => (models.data ?? []).filter((m) => m.status === 'finished'), [models.data])
  const [modelId, setModelId] = useState('')

  useEffect(() => {
    if ((!modelId || !finished.some((m) => m.id === modelId)) && finished.length > 0) setModelId(finished[0].id)
  }, [finished, modelId])

  const trades = useApi(() => listTrades({ strategy }), [strategy])
  const [tradeId, setTradeId] = useState<number | null>(null)
  const [prediction, setPrediction] = useState<PredictionResult | null>(null)
  const [predicting, setPredicting] = useState(false)
  const [predictError, setPredictError] = useState<string | null>(null)

  const selectedTrade = useMemo(() => (trades.data ?? []).find((t) => t.id === tradeId) ?? null, [trades.data, tradeId])

  async function handlePredict() {
    if (!modelId || tradeId === null) return
    setPredicting(true)
    setPredictError(null)
    try {
      setPrediction(await predictTrade(modelId, tradeId))
    } catch (err) {
      setPredictError(err instanceof Error ? err.message : 'Prediction failed.')
    } finally {
      setPredicting(false)
    }
  }

  return (
    <div>
      <div className="panel">
        {finished.length === 0 && <EmptyState label="Train a model for this strategy first." />}
        {finished.length > 0 && (
          <>
            <div className="field-row">
              <div className="field">
                <label htmlFor="sandbox-model">Model</label>
                <select id="sandbox-model" value={modelId} onChange={(e) => setModelId(e.target.value)}>
                  {finished.map((m) => <option key={m.id} value={m.id}>{m.model_type.replace(/_/g, ' ')} v{m.version}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="sandbox-trade">Historical trade</label>
                <select id="sandbox-trade" value={tradeId ?? ''} onChange={(e) => setTradeId(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">Select…</option>
                  {(trades.data ?? []).slice(0, 300).map((t) => (
                    <option key={t.id} value={t.id}>{dateTime(t.entry_time)} · {t.outcome} · {money(t.net_pnl)}</option>
                  ))}
                </select>
              </div>
            </div>
            <button className="btn" type="button" disabled={!modelId || tradeId === null || predicting} onClick={handlePredict}>
              {predicting ? 'Predicting…' : 'Predict'}
            </button>
            {predictError && <ErrorState message={predictError} />}
          </>
        )}
      </div>

      {prediction && selectedTrade && (
        <div className="grid grid-2">
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Prediction</h3>
            <div className="grid grid-stats">
              <StatTile label="Predicted" value={<Badge tone={prediction.probability >= 0.5 ? 'good' : 'bad'}>{prediction.probability >= 0.5 ? 'Win' : 'Loss'}</Badge>} />
              <StatTile label="Actual" value={<Badge tone={selectedTrade.outcome === 'win' ? 'good' : 'bad'}>{selectedTrade.outcome}</Badge>} />
              <StatTile label="Confidence" value={`${(prediction.confidence * 100).toFixed(1)}%`} />
              <StatTile label="Win Probability" value={`${(prediction.expected_win_probability * 100).toFixed(1)}%`} />
              <StatTile label="Expected Value (R)" value={prediction.expected_value_r !== null ? prediction.expected_value_r.toFixed(2) : '—'} />
              <StatTile
                label="Similar Trades"
                value={`${prediction.similar_trade_count}${prediction.similar_trade_win_rate !== null ? ` (${(prediction.similar_trade_win_rate * 100).toFixed(0)}% win)` : ''}`}
              />
            </div>
            {prediction.calibration_bucket && (
              <p style={{ fontSize: 12.5, color: 'var(--text-dim)', marginTop: 8 }}>
                At this probability, similarly-scored historical trades won{' '}
                {(prediction.calibration_bucket.actual_win_rate * 100).toFixed(0)}% of the time
                (model predicted {(prediction.calibration_bucket.predicted * 100).toFixed(0)}%).
              </p>
            )}
          </div>
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Top Contributing Features</h3>
            <FeatureImportanceBars rows={prediction.top_reasons.map((r) => ({ feature: r.feature, importance: r.contribution }))} />
          </div>
        </div>
      )}

      {selectedTrade && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Feature Value vs. Net P&amp;L (this strategy)</h3>
          <FeatureScatterChart
            points={(trades.data ?? []).map((t) => ({
              value: Number(Object.values(t.entry_metadata)[0] ?? 0), net_pnl: Number(t.net_pnl), outcome: t.outcome,
            }))}
          />
        </div>
      )}
    </div>
  )
}

// --- Terminal ---

function TerminalTab({ jobId, lines }: { jobId: string | null; lines: string[] }) {
  return (
    <div className="panel">
      <h3 style={{ marginTop: 0 }}>Research Log</h3>
      {lines.length === 0 ? (
        <EmptyState label="Nothing logged yet — train a model to see live progress here." />
      ) : (
        <pre
          className="mono"
          style={{ maxHeight: 420, overflowY: 'auto', fontSize: 12, lineHeight: 1.6, background: 'var(--bg-panel-2)', padding: 12, borderRadius: 6 }}
        >
          {lines.join('\n')}
        </pre>
      )}
      {jobId && <p style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 8 }}>Watching job <code>{jobId}</code>.</p>}
    </div>
  )
}
