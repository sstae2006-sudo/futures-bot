import { useEffect } from 'react'
import { getWorkers } from '../../api'
import { useApi } from '../../useApi'
import { Badge } from '../UI'
import type { Worker, WorkerStatus } from '../../types'

// SIL Phase 6 "Integration Coordinator" Milestone 1 -- every human
// developer, Claude Code session, other AI agent, or background job
// that has heartbeated at least once (POST /api/workers/{id}/heartbeat).
// A generic Worker Registry, not something specific to Claude Code --
// see futures_bot/collaboration/__init__.py::WORKER_TYPES's own
// docstring. `is_stale` is computed live server-side from
// last_heartbeat_at every call, never stored -- polls every 15s
// (infra-like cadence, matching InfrastructurePanel -- workers are
// closer to infra than to TeamPanel's 30s human-roster cadence).
const STATUS_TONE: Record<WorkerStatus, 'good' | 'warn' | 'neutral'> = {
  online: 'good',
  idle: 'warn',
  offline: 'neutral',
}

const WORKER_TYPE_LABEL: Record<Worker['worker_type'], string> = {
  human: 'Human',
  claude_code_session: 'Claude Code',
  ai_agent: 'AI Agent',
  validation_worker: 'Validation',
  research_worker: 'Research',
  background_service: 'Background Service',
  distributed_compute_worker: 'Distributed Compute',
}

function timeAgo(seconds: number): string {
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ago`
}

export default function WorkforcePanel() {
  const { data: workers, refetch } = useApi(() => getWorkers(), [])

  useEffect(() => {
    const id = setInterval(refetch, 15_000)
    return () => clearInterval(id)
  }, [refetch])

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Workforce</h3>
      </div>
      {!workers && <p className="text-sm text-dim">Loading…</p>}
      {workers && workers.length === 0 && (
        <p className="text-sm text-dim">No workers have reported in yet.</p>
      )}
      {workers && workers.length > 0 && (
        <ul className="m-0" style={{ listStyle: 'none', padding: 0 }}>
          {workers.map((w) => (
            <li
              key={w.id}
              style={{ padding: '6px 0', borderBottom: '1px solid var(--border, rgba(128,128,128,0.15))', fontSize: 13 }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 600 }}>{w.display_name}</span>
                <Badge tone={w.is_stale ? 'neutral' : STATUS_TONE[w.status]}>
                  {w.is_stale ? 'stale' : w.status}
                </Badge>
              </div>
              <div className="text-dim text-xs mt-1">
                {WORKER_TYPE_LABEL[w.worker_type]}
                {w.subsystem ? ` · ${w.subsystem}` : ''}
                {w.current_work_item_id ? ` · working on ${w.current_work_item_id}` : ''}
              </div>
              {w.capabilities.length > 0 && (
                <div className="mt-1" style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {w.capabilities.map((c) => (
                    <span key={c} className="badge badge-neutral text-xs">
                      {c}
                    </span>
                  ))}
                </div>
              )}
              <div className="text-dim text-xs mt-1">
                last heartbeat {timeAgo(w.seconds_since_heartbeat)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
