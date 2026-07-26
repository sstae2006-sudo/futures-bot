import { useEffect, useMemo, useState } from 'react'
import { createExperiment, listExperiments, listModels, listStrategies, updateExperimentNotes } from '../api'
import { useApi } from '../useApi'
import { LoadingState, ErrorState, EmptyState, Badge } from '../components/UI'
import { dateTime } from '../format'
import type { ExperimentOut } from '../types'

export default function Experiments() {
  const strategies = useApi(listStrategies)
  const experiments = useApi(listExperiments)

  const [name, setName] = useState('')
  const [hypothesis, setHypothesis] = useState('')
  const [strategy, setStrategy] = useState('')
  const [dataset, setDataset] = useState('')
  const [notes, setNotes] = useState('')
  const [modelId, setModelId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!strategy && strategies.data && strategies.data.length > 0) setStrategy(strategies.data[0].name)
  }, [strategies.data, strategy])

  // Phase 9: optionally link a trained model -- its dataset version and
  // headline validation metrics are captured onto the experiment so two
  // experiments can be compared side-by-side without a second lookup.
  const models = useApi(() => (strategy ? listModels({ strategy }) : Promise.resolve([])), [strategy])
  const finishedModels = useMemo(() => (models.data ?? []).filter((m) => m.status === 'finished'), [models.data])
  useEffect(() => { setModelId('') }, [strategy])

  const [selected, setSelected] = useState<ExperimentOut | null>(null)
  const [notesDraft, setNotesDraft] = useState('')
  const [compareIds, setCompareIds] = useState<string[]>([])

  function toggleCompare(id: string) {
    setCompareIds((ids) => (ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id]))
  }

  const compareExperiments = useMemo(
    () => (experiments.data ?? []).filter((e) => compareIds.includes(e.id)),
    [experiments.data, compareIds],
  )

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const linkedModel = finishedModels.find((m) => m.id === modelId)
      await createExperiment({
        name, hypothesis, strategy, dataset: dataset || undefined, notes: notes || undefined,
        model_id: modelId || undefined,
        dataset_version: linkedModel?.dataset_version,
        metrics: linkedModel?.metrics ? { ...linkedModel.metrics } : undefined,
      })
      setName('')
      setHypothesis('')
      setDataset('')
      setNotes('')
      setModelId('')
      experiments.refetch()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the experiment.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleSaveNotes() {
    if (!selected) return
    const updated = await updateExperimentNotes(selected.id, notesDraft)
    setSelected(updated)
    experiments.refetch()
  }

  return (
    <div>
      <div className="page-header">
        <h1>Research Experiments</h1>
        <p>Track a hypothesis, what you tested it with, and what you learned — e.g. "Does VWAP reversion perform better in high volatility?"</p>
      </div>

      <form className="panel" onSubmit={handleCreate}>
        <h3 style={{ marginTop: 0 }}>New Experiment</h3>
        <div className="field">
          <label htmlFor="exp-name">Name</label>
          <input id="exp-name" value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="exp-hypothesis">Hypothesis</label>
          <input
            id="exp-hypothesis" value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} required
            placeholder="e.g. VWAP reversion performs better during high-volatility sessions."
          />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="exp-strategy">Strategy</label>
            <select id="exp-strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {(strategies.data ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="exp-dataset">Dataset (optional)</label>
            <input id="exp-dataset" value={dataset} onChange={(e) => setDataset(e.target.value)} placeholder="data_MES_full.csv" />
          </div>
          <div className="field">
            <label htmlFor="exp-model">Trained model (optional)</label>
            <select id="exp-model" value={modelId} onChange={(e) => setModelId(e.target.value)}>
              <option value="">None</option>
              {finishedModels.map((m) => <option key={m.id} value={m.id}>{m.model_type.replace(/_/g, ' ')} v{m.version}</option>)}
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="exp-notes">Notes (optional)</label>
          <input id="exp-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>
        <button className="btn" type="submit" disabled={submitting || !name || !hypothesis || !strategy}>
          {submitting ? 'Saving…' : 'Save Experiment'}
        </button>
        {error && <ErrorState message={error} />}
      </form>

      {experiments.loading && <LoadingState label="Loading experiments…" />}
      {experiments.error && <ErrorState message={experiments.error} onRetry={experiments.refetch} />}
      {experiments.data && experiments.data.length === 0 && <EmptyState label="No experiments recorded yet." />}

      {experiments.data && experiments.data.length > 0 && (
        <div className="grid grid-2">
          <div className="panel">
            <h3>History</h3>
            <table>
              <thead><tr><th></th><th>Name</th><th>Strategy</th><th>Model</th><th>Created</th></tr></thead>
              <tbody>
                {experiments.data.map((exp) => (
                  <tr
                    key={exp.id}
                    style={{ cursor: 'pointer', background: selected?.id === exp.id ? 'var(--accent-dim)' : undefined }}
                  >
                    <td>
                      <input
                        type="checkbox" checked={compareIds.includes(exp.id)}
                        onChange={() => toggleCompare(exp.id)} onClick={(e) => e.stopPropagation()}
                      />
                    </td>
                    <td className="text-col" onClick={() => { setSelected(exp); setNotesDraft(exp.notes ?? '') }}>{exp.name}</td>
                    <td className="text-col" onClick={() => { setSelected(exp); setNotesDraft(exp.notes ?? '') }}>{exp.strategy}</td>
                    <td onClick={() => { setSelected(exp); setNotesDraft(exp.notes ?? '') }}>
                      {exp.model_id ? <Badge tone="neutral">linked</Badge> : '—'}
                    </td>
                    <td onClick={() => { setSelected(exp); setNotesDraft(exp.notes ?? '') }}>{dateTime(exp.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 8 }}>
              Check 2 or more to compare them side-by-side below.
            </p>
          </div>

          <div className="panel">
            <h3>Detail</h3>
            {!selected && <EmptyState label="Select an experiment to view or update it." />}
            {selected && (
              <div>
                <p><strong>Hypothesis:</strong> {selected.hypothesis}</p>
                <p><strong>Strategy:</strong> {selected.strategy}</p>
                {selected.dataset && <p><strong>Dataset:</strong> {selected.dataset}</p>}
                {selected.run_id && <p><strong>Linked run:</strong> <code>{selected.run_id}</code></p>}
                {selected.model_id && (
                  <p>
                    <strong>Linked model:</strong> <code>{selected.model_id}</code>
                    {selected.dataset_version && <> · dataset v{selected.dataset_version.slice(0, 8)}</>}
                  </p>
                )}
                <div className="field">
                  <label htmlFor="notes-draft">Notes / findings</label>
                  <input id="notes-draft" value={notesDraft} onChange={(e) => setNotesDraft(e.target.value)} />
                </div>
                <button className="btn btn-secondary" onClick={handleSaveNotes}>Save Notes</button>
              </div>
            )}
          </div>
        </div>
      )}

      {compareExperiments.length >= 2 && (
        <div className="panel">
          <h3>Compare Experiments</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Field</th>
                  {compareExperiments.map((e) => <th key={e.id} className="text-col">{e.name}</th>)}
                </tr>
              </thead>
              <tbody>
                <tr><td className="text-col">Strategy</td>{compareExperiments.map((e) => <td key={e.id}>{e.strategy}</td>)}</tr>
                <tr><td className="text-col">Hypothesis</td>{compareExperiments.map((e) => <td key={e.id} className="text-col">{e.hypothesis}</td>)}</tr>
                <tr><td className="text-col">Dataset</td>{compareExperiments.map((e) => <td key={e.id}>{e.dataset ?? '—'}</td>)}</tr>
                <tr><td className="text-col">Model</td>{compareExperiments.map((e) => <td key={e.id} className="mono">{e.model_id ?? '—'}</td>)}</tr>
                <tr><td className="text-col">Dataset version</td>{compareExperiments.map((e) => <td key={e.id} className="mono">{e.dataset_version?.slice(0, 8) ?? '—'}</td>)}</tr>
                <tr>
                  <td className="text-col">Validation accuracy</td>
                  {compareExperiments.map((e) => {
                    const validation = (e.metrics as { validation?: { accuracy?: number } } | null)?.validation
                    return <td key={e.id}>{validation?.accuracy !== undefined ? validation.accuracy.toFixed(3) : '—'}</td>
                  })}
                </tr>
                <tr><td className="text-col">Created</td>{compareExperiments.map((e) => <td key={e.id}>{dateTime(e.created_at)}</td>)}</tr>
                <tr><td className="text-col">Notes</td>{compareExperiments.map((e) => <td key={e.id} className="text-col">{e.notes ?? '—'}</td>)}</tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
