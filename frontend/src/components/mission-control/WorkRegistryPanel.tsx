import { useState } from 'react'
import {
  claimWorkItem, completeWorkItem, createWorkItem, getWorkItems, releaseWorkItem,
} from '../../api'
import { useApi } from '../../useApi'
import { Badge } from '../UI'
import type { OverlapWarning, WorkItem, WorkItemPriority } from '../../types'

// Active Work Registry (Team Collaboration MVP) -- who's doing what, so
// multiple humans/AI agents on this codebase don't step on each other.
// File-level overlap warnings surface at creation time (see the
// overlap_warnings this panel's create form displays inline); nothing
// here ever blocks a claim or a new task, only warns -- see
// collaboration/overlap.py's own docstring.
const STATUS_TONE: Record<WorkItem['status'], 'good' | 'warn' | 'neutral'> = {
  open: 'neutral',
  claimed: 'warn',
  completed: 'good',
}

const RISK_TONE: Record<string, 'good' | 'warn' | 'bad' | 'neutral'> = {
  no_risk: 'good',
  low: 'good',
  medium: 'warn',
  high: 'bad',
  critical: 'bad',
}

export default function WorkRegistryPanel() {
  const { data: items, refetch } = useApi(() => getWorkItems(), [])
  const [title, setTitle] = useState('')
  const [files, setFiles] = useState('')
  const [priority, setPriority] = useState<WorkItemPriority>('medium')
  const [warnings, setWarnings] = useState<OverlapWarning[]>([])
  const [busy, setBusy] = useState(false)

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim()) return
    setBusy(true)
    try {
      const result = await createWorkItem({
        title,
        estimated_files: files.split(',').map((f) => f.trim()).filter(Boolean),
        priority,
      })
      setWarnings(result.overlap_warnings)
      setTitle('')
      setFiles('')
      refetch()
    } finally {
      setBusy(false)
    }
  }

  async function handleClaim(id: string) {
    const userId = window.prompt('Your user ID (or name) to claim this task:')
    if (!userId) return
    await claimWorkItem(id, userId)
    refetch()
  }

  async function handleRelease(id: string) {
    await releaseWorkItem(id)
    refetch()
  }

  async function handleComplete(id: string) {
    await completeWorkItem(id)
    refetch()
  }

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Active Work</h3>
      </div>

      <form onSubmit={handleCreate} style={{ marginBottom: 12 }}>
        <div className="field-row">
          <div className="field" style={{ flex: 2 }}>
            <input
              placeholder="Task title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              aria-label="Task title"
            />
          </div>
          <div className="field" style={{ flex: 2 }}>
            <input
              placeholder="Files touched (comma-separated)"
              value={files}
              onChange={(e) => setFiles(e.target.value)}
              aria-label="Estimated files"
            />
          </div>
          <div className="field">
            <select value={priority} onChange={(e) => setPriority(e.target.value as WorkItemPriority)} aria-label="Priority">
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
          </div>
          <button type="submit" className="btn btn-primary" disabled={busy || !title.trim()}>
            Add
          </button>
        </div>
      </form>

      {warnings.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          {warnings.map((w) => (
            <p key={w.work_item_id} role="alert" style={{ fontSize: 12, margin: '2px 0' }}>
              <Badge tone={RISK_TONE[w.risk]}>{w.risk.replace('_', ' ')}</Badge> {w.reason}
            </p>
          ))}
        </div>
      )}

      {items && items.length === 0 && <p style={{ fontSize: 13, opacity: 0.7 }}>No active work items.</p>}
      {items && items.length > 0 && (
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td style={{ padding: '4px 0' }}>
                  <div>{item.title}</div>
                  <div style={{ opacity: 0.6, fontSize: 11 }}>
                    {item.owner_user_id ?? 'unclaimed'} · {item.estimated_files.length} file(s) · {item.priority}
                  </div>
                </td>
                <td style={{ padding: '4px 8px' }}>
                  <Badge tone={STATUS_TONE[item.status]}>{item.status}</Badge>
                </td>
                <td style={{ padding: '4px 0', textAlign: 'right', whiteSpace: 'nowrap' }}>
                  {item.status !== 'completed' && item.status !== 'claimed' && (
                    <button type="button" className="btn btn-secondary" onClick={() => handleClaim(item.id)}>Claim</button>
                  )}
                  {item.status === 'claimed' && (
                    <>
                      <button type="button" className="btn btn-secondary" onClick={() => handleRelease(item.id)}>Release</button>{' '}
                      <button type="button" className="btn btn-secondary" onClick={() => handleComplete(item.id)}>Complete</button>
                    </>
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
