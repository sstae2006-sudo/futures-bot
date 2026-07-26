import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { listJobs } from '../api'
import { useApi } from '../useApi'
import { useJobStream } from '../useJobStream'
import { LoadingState, ErrorState, EmptyState, Badge } from '../components/UI'
import { JobProgressBar } from '../components/JobProgress'
import { dateTime } from '../format'

const REFRESH_INTERVAL_MS = 2000

export default function Jobs() {
  const { jobId } = useParams()
  if (jobId) return <JobDetail jobId={jobId} />
  return <JobsList />
}

function JobsList() {
  const [statusFilter, setStatusFilter] = useState('')
  const jobs = useApi(() => listJobs({ status: statusFilter || undefined, limit: 100 }), [statusFilter])

  // Background jobs page: refresh periodically rather than opening one SSE
  // connection per row -- simple, and fine at this data volume (a local
  // research tool's job history, not a production job queue dashboard).
  useEffect(() => {
    const interval = window.setInterval(() => jobs.refetch(), REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter])

  return (
    <div>
      <div className="page-header">
        <h1>Jobs</h1>
        <p>Background backtest, optimizer, comparison, and report runs.</p>
      </div>

      <div className="panel">
        <div className="field" style={{ maxWidth: 220 }}>
          <label htmlFor="job-status">Status</label>
          <select id="job-status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
        </div>
      </div>

      {jobs.loading && <LoadingState label="Loading jobs…" />}
      {jobs.error && <ErrorState message={jobs.error} onRetry={jobs.refetch} />}
      {jobs.data && jobs.data.length === 0 && <EmptyState label="No jobs yet. Submit one from the Backtest Launcher or Optimizer page." />}

      {jobs.data && jobs.data.length > 0 && (
        <div className="panel">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Kind</th><th>Status</th><th>Progress</th><th>Message</th><th>Created</th><th></th>
                </tr>
              </thead>
              <tbody>
                {jobs.data.map((job) => {
                  const pct = job.progress_total > 0 ? Math.round((job.progress_current / job.progress_total) * 100) : null
                  return (
                    <tr key={job.id}>
                      <td className="text-col">{job.kind}</td>
                      <td>
                        <Badge tone={job.status === 'completed' ? 'good' : job.status === 'failed' ? 'bad' : 'neutral'}>
                          {job.status}
                        </Badge>
                      </td>
                      <td>{pct !== null ? `${pct}% (${job.progress_current}/${job.progress_total})` : '—'}</td>
                      <td className="text-col">{job.status === 'failed' ? job.error_message : job.progress_message}</td>
                      <td>{dateTime(job.created_at)}</td>
                      <td><Link to={`/jobs/${job.id}`}>Details</Link></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function JobDetail({ jobId }: { jobId: string }) {
  const streamed = useJobStream(jobId)

  return (
    <div>
      <div className="page-header">
        <h1>Job {jobId}</h1>
      </div>

      {!streamed && <LoadingState label="Connecting to job stream…" />}
      {streamed && (
        <div className="panel">
          <p><strong>Kind:</strong> {streamed.kind}</p>
          <JobProgressBar job={streamed} />
          {streamed.status === 'completed' && streamed.result_id && (
            <p style={{ marginTop: 12 }}>
              Result: <code>{streamed.result_id}</code>
              {(streamed.kind === 'backtest' || streamed.kind === 'walk_forward') && (
                <> — <Link to={`/backtest/${streamed.result_id}`}>view backtest result</Link></>
              )}
              {streamed.kind === 'report' && (
                <> — report ready</>
              )}
            </p>
          )}
          {streamed.status === 'completed' && streamed.result_payload && (
            <pre style={{ marginTop: 12, fontSize: 11, overflowX: 'auto' }}>
              {JSON.stringify(streamed.result_payload, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
