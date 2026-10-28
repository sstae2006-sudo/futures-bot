import { useEffect, useState } from 'react'
import { approveDraftWorkItem, discardDraftWorkItem, getAutomationStatus, getDraftWorkItems } from '../../api'
import { useApi } from '../../useApi'
import { Badge } from '../UI'
import { dateTime } from '../../format'

// SIL Phase 4 "Intelligent Automation Layer" -- the git-watcher (drafts a
// work item for uncommitted changes not covered by any active work item)
// and the maintenance scheduler (discards stale drafts, checks DB health).
// Both are opt-in (automation.enabled in config.yaml, default False -- see
// config.py::AutomationSettings's docstring) -- "running: false" here
// just means it wasn't turned on, not that something is broken. Polls
// every 15s, same cadence InfrastructurePanel uses.
function SchedulerStatus({
  label,
  running,
  lastCycleAt,
  lastResult,
  lastError,
  cyclesCompleted,
}: {
  label: string
  running: boolean
  lastCycleAt: string | null
  lastResult: string | null
  lastError: string | null
  cyclesCompleted: number
}) {
  return (
    <div className="mb-3">
      <div className="mb-1" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span className="text-sm" style={{ fontWeight: 600 }}>{label}</span>
        <Badge tone={running ? 'good' : 'neutral'}>{running ? 'running' : 'stopped'}</Badge>
      </div>
      {running ? (
        <div className="text-xs text-dim">
          <div>{cyclesCompleted} cycle{cyclesCompleted === 1 ? '' : 's'} completed{lastCycleAt ? ` · last ${dateTime(lastCycleAt)}` : ''}</div>
          {lastError ? (
            <div className="tone-bad">{lastError}</div>
          ) : lastResult ? (
            <div>{lastResult}</div>
          ) : null}
        </div>
      ) : (
        <div className="text-xs text-dim">Not enabled (automation.enabled in config.yaml).</div>
      )}
    </div>
  )
}

export default function AutomationPanel() {
  const { data: status, refetch: refetchStatus } = useApi(getAutomationStatus, [])
  const { data: drafts, refetch: refetchDrafts } = useApi(getDraftWorkItems, [])
  const [busyId, setBusyId] = useState<string | null>(null)

  useEffect(() => {
    const id = setInterval(() => {
      refetchStatus()
      refetchDrafts()
    }, 15_000)
    return () => clearInterval(id)
  }, [refetchStatus, refetchDrafts])

  async function handleApprove(itemId: string) {
    setBusyId(itemId)
    try {
      await approveDraftWorkItem(itemId)
      refetchDrafts()
    } finally {
      setBusyId(null)
    }
  }

  async function handleDiscard(itemId: string) {
    setBusyId(itemId)
    try {
      await discardDraftWorkItem(itemId)
      refetchDrafts()
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Automation</h3>
      </div>

      {!status && <p className="text-sm text-dim">Loading…</p>}
      {status && (
        <>
          <SchedulerStatus
            label="Git-watcher"
            running={status.git_watcher.running}
            lastCycleAt={status.git_watcher.last_cycle_at}
            lastResult={status.git_watcher.last_result}
            lastError={status.git_watcher.last_error}
            cyclesCompleted={status.git_watcher.cycles_completed}
          />
          <SchedulerStatus
            label="Maintenance"
            running={status.maintenance.running}
            lastCycleAt={status.maintenance.last_cycle_at}
            lastResult={status.maintenance.last_result}
            lastError={status.maintenance.last_error}
            cyclesCompleted={status.maintenance.cycles_completed}
          />
          <SchedulerStatus
            label="Git-sync (pull-only)"
            running={status.git_sync.running}
            lastCycleAt={status.git_sync.last_cycle_at}
            lastResult={status.git_sync.last_result}
            lastError={status.git_sync.last_error}
            cyclesCompleted={status.git_sync.cycles_completed}
          />
        </>
      )}

      <div style={{ marginTop: 12, borderTop: '1px solid var(--border, rgba(128,128,128,0.25))', paddingTop: 12 }}>
        <div className="text-sm mb-1" style={{ fontWeight: 600 }}>
          Draft work items {drafts ? `(${drafts.length})` : ''}
        </div>
        {drafts && drafts.length === 0 && (
          <p className="text-xs text-dim">No drafts awaiting review.</p>
        )}
        {drafts && drafts.length > 0 && (
          <ul className="m-0" style={{ listStyle: 'none', padding: 0 }}>
            {drafts.map((item) => (
              <li key={item.id} className="mb-2 text-xs">
                <div style={{ fontWeight: 600 }}>{item.title}</div>
                <div style={{ opacity: 0.7 }}>{item.estimated_files.length} file{item.estimated_files.length === 1 ? '' : 's'}</div>
                <div className="mt-1">
                  <button
                    className="btn btn-primary text-xs"
                    style={{ padding: '2px 8px', marginRight: 6 }}
                    disabled={busyId === item.id}
                    onClick={() => handleApprove(item.id)}
                  >
                    Approve
                  </button>
                  <button
                    className="btn btn-secondary text-xs"
                    style={{ padding: '2px 8px' }}
                    disabled={busyId === item.id}
                    onClick={() => handleDiscard(item.id)}
                  >
                    Discard
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
