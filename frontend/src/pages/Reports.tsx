import { listBacktests, listReports, reportViewUrl } from '../api'
import { useApi } from '../useApi'
import { LoadingState, ErrorState, EmptyState } from '../components/UI'
import { dateTime } from '../format'
import { Link } from 'react-router-dom'

export default function Reports() {
  const reports = useApi(() => listReports())
  const runs = useApi(() => listBacktests({ limit: 50 }))

  return (
    <div>
      <div className="page-header">
        <h1>Reports</h1>
        <p>Every self-contained HTML report generated so far. Generate a new one from any backtest's result page.</p>
      </div>

      {reports.loading && <LoadingState label="Loading reports…" />}
      {reports.error && <ErrorState message={reports.error} onRetry={reports.refetch} />}
      {reports.data && reports.data.length === 0 && <EmptyState label="No reports generated yet." />}

      {reports.data && reports.data.length > 0 && (
        <div className="panel">
          <table>
            <thead>
              <tr><th>Report ID</th><th>Run</th><th>Format</th><th>Generated</th><th></th></tr>
            </thead>
            <tbody>
              {reports.data.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td><Link to={`/backtest/${r.run_id}`}>{r.run_id}</Link></td>
                  <td>{r.format}</td>
                  <td>{dateTime(r.created_at)}</td>
                  <td><a href={reportViewUrl(r.id)} target="_blank" rel="noreferrer">Open ↗</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="page-header" style={{ marginTop: 24 }}>
        <h2>Generate From a Recent Backtest</h2>
      </div>
      {runs.loading && <LoadingState label="Loading recent backtests…" />}
      {runs.data && runs.data.length === 0 && <EmptyState label="No backtests to report on yet." />}
      {runs.data && runs.data.length > 0 && (
        <div className="panel">
          <table>
            <thead><tr><th>Run</th><th>Strategy</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {runs.data.slice(0, 15).map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td className="text-col">{r.strategy}</td>
                  <td>{r.status}</td>
                  <td><Link to={`/backtest/${r.id}`}>Open run →</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
