import { useState } from 'react'
import { getLogs } from '../api'
import { useApi } from '../useApi'
import { LoadingState, ErrorState, EmptyState, Badge } from '../components/UI'
import { dateTime } from '../format'

export default function Logs() {
  const [kind, setKind] = useState('')
  const [search, setSearch] = useState('')
  const logs = useApi(() => getLogs({ limit: 300, kind: kind || undefined }), [kind])

  const filtered = (logs.data ?? []).filter((l) => l.message.toLowerCase().includes(search.toLowerCase()))

  return (
    <div>
      <div className="page-header">
        <h1>System Logs</h1>
        <p>Backtest/optimizer run history and journalled strategy events (errors, halts, forced flattens).</p>
      </div>

      <div className="panel">
        <div className="field-row">
          <div className="field">
            <label htmlFor="log-kind">Kind</label>
            <select id="log-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">All</option>
              <option value="run:backtest">Backtest runs</option>
              <option value="run:walk_forward">Walk-forward runs</option>
              <option value="run:optimizer">Optimizer runs</option>
              <option value="strategy_error">Strategy errors</option>
              <option value="halt">Kill-switch halts</option>
              <option value="flatten_failed">Flatten failures</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="log-search">Search message</label>
            <input id="log-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter by text…" />
          </div>
        </div>
      </div>

      {logs.loading && <LoadingState label="Loading logs…" />}
      {logs.error && <ErrorState message={logs.error} onRetry={logs.refetch} />}
      {logs.data && filtered.length === 0 && <EmptyState label="No log entries match." />}

      {logs.data && filtered.length > 0 && (
        <div className="panel">
          <div className="table-scroll">
            <table>
              <thead>
                <tr><th>Timestamp</th><th>Level</th><th>Kind</th><th>Message</th></tr>
              </thead>
              <tbody>
                {filtered.map((l, i) => (
                  <tr key={i}>
                    <td>{dateTime(l.timestamp)}</td>
                    <td><Badge tone={l.level === 'ERROR' ? 'bad' : l.level === 'WARNING' ? 'warn' : 'neutral'}>{l.level}</Badge></td>
                    <td className="text-col">{l.kind}</td>
                    <td className="text-col">{l.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
