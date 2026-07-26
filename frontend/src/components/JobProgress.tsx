import type { JobOut } from '../types'
import { Badge } from './UI'

export function JobProgressBar({ job }: { job: JobOut }) {
  const pct = job.progress_total > 0 ? Math.min(100, Math.round((job.progress_current / job.progress_total) * 100)) : 0
  const tone = job.status === 'failed' ? 'bad' : job.status === 'completed' ? 'good' : 'warn'

  return (
    <div className="job-progress">
      <div className="job-progress-header">
        <Badge tone={job.status === 'completed' ? 'good' : job.status === 'failed' ? 'bad' : 'neutral'}>
          {job.status}
        </Badge>
        <span className="job-progress-pct">{job.progress_total > 0 ? `${pct}%` : ''}</span>
      </div>
      <div className="job-progress-track">
        <div className={`job-progress-fill tone-fill-${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="job-progress-message">
        {job.status === 'failed' ? job.error_message : job.progress_message ?? 'Waiting to start…'}
      </div>
    </div>
  )
}
