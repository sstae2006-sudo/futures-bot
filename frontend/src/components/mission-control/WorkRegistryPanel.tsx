import { useState } from 'react'
import { createWorkItem, getWorkItems } from '../../api'
import { useApi } from '../../useApi'
import { useSession } from '../../session'
import { Badge } from '../UI'
import WorkItemTable from './WorkItemTable'
import type { OverlapWarning, OwnerType, WorkItemPriority } from '../../types'

// Active Work Registry (Team Collaboration Platform) -- who's doing what,
// so multiple humans/AI agents on this codebase don't step on each other.
// File-level overlap warnings surface at creation time (see the
// overlap_warnings this panel's create form displays inline); nothing
// here ever blocks a claim or a new task, only warns -- see
// collaboration/overlap.py's own docstring. The list/action rendering
// itself lives in WorkItemTable.tsx, shared with CollaborationWorkspace's
// several filtered tabs.
const RISK_TONE: Record<string, 'good' | 'warn' | 'bad' | 'neutral'> = {
  no_risk: 'good',
  low: 'good',
  medium: 'warn',
  high: 'bad',
  critical: 'bad',
}

export default function WorkRegistryPanel() {
  const { organization } = useSession()
  const orgId = organization?.id
  const { data: items, refetch } = useApi(() => getWorkItems(undefined, orgId), [orgId])
  const [title, setTitle] = useState('')
  const [files, setFiles] = useState('')
  const [priority, setPriority] = useState<WorkItemPriority>('medium')
  const [ownerType, setOwnerType] = useState<OwnerType>('human')
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
        owner_type: ownerType,
        org_id: orgId,
      })
      setWarnings(result.overlap_warnings)
      setTitle('')
      setFiles('')
      refetch()
    } finally {
      setBusy(false)
    }
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
          <div className="field">
            <select value={ownerType} onChange={(e) => setOwnerType(e.target.value as OwnerType)} aria-label="Owner type">
              <option value="human">Human</option>
              <option value="ai">AI</option>
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

      {items && <WorkItemTable items={items} onRefetch={refetch} emptyMessage="No active work items." />}
    </div>
  )
}
