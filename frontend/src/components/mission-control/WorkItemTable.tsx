import { claimWorkItem, completeWorkItem, releaseWorkItem, updateWorkItemStatus } from '../../api'
import { useSession } from '../../session'
import { Badge } from '../UI'
import type { ManualWorkItemStatus, WorkItem, WorkItemStatus } from '../../types'
import { WORK_ITEM_LIFECYCLE } from '../../types'

// Shared list/action rendering for a set of work items -- used by both
// WorkRegistryPanel (the create-form + full list) and
// CollaborationWorkspace's several filtered tabs, so the claim/release/
// complete/advance actions and the lifecycle visualization exist in one
// place, not duplicated across panels.
const STATUS_TONE: Record<WorkItemStatus, 'good' | 'warn' | 'neutral'> = {
  open: 'neutral',
  planned: 'neutral',
  claimed: 'warn',
  in_progress: 'warn',
  testing: 'warn',
  ready_for_review: 'warn',
  merged: 'good',
  completed: 'good',
}

const NEXT_STAGE: Partial<Record<WorkItemStatus, { label: string; status: ManualWorkItemStatus }>> = {
  claimed: { label: 'Start', status: 'in_progress' },
  in_progress: { label: 'Mark testing', status: 'testing' },
  testing: { label: 'Ready for review', status: 'ready_for_review' },
  ready_for_review: { label: 'Mark merged', status: 'merged' },
}

function lifecycleIndex(status: WorkItemStatus): number {
  const normalized = status === 'open' ? 'planned' : status
  const idx = WORK_ITEM_LIFECYCLE.indexOf(normalized as (typeof WORK_ITEM_LIFECYCLE)[number])
  return idx === -1 ? 0 : idx
}

function LifecycleDots({ status }: { status: WorkItemStatus }) {
  const current = lifecycleIndex(status)
  return (
    <span aria-label={`Lifecycle stage: ${status}`} style={{ letterSpacing: 1, fontSize: 10 }}>
      {WORK_ITEM_LIFECYCLE.map((stage, i) => (
        <span key={stage} title={stage.replace(/_/g, ' ')} style={{ opacity: i <= current ? 1 : 0.25 }}>
          {'●'}
        </span>
      ))}
    </span>
  )
}

export default function WorkItemTable({
  items, onRefetch, emptyMessage = 'No work items.', showLifecycle = true,
}: {
  items: WorkItem[]
  onRefetch: () => void
  emptyMessage?: string
  showLifecycle?: boolean
}) {
  const { currentUser } = useSession()

  async function handleClaim(id: string) {
    // Defaults to the signed-in user (session.tsx) so claiming your own
    // work is a single click in the common case; still overridable (e.g.
    // claiming on behalf of a teammate who isn't at their keyboard) since
    // there's no auth boundary stopping that anyway.
    const userId = window.prompt('Claim as user ID:', currentUser?.id ?? '')
    if (!userId) return
    await claimWorkItem(id, userId)
    onRefetch()
  }

  async function handleRelease(id: string) {
    await releaseWorkItem(id)
    onRefetch()
  }

  async function handleComplete(id: string) {
    await completeWorkItem(id)
    onRefetch()
  }

  async function handleAdvance(id: string, status: ManualWorkItemStatus) {
    await updateWorkItemStatus(id, status)
    onRefetch()
  }

  if (items.length === 0) {
    return <p style={{ fontSize: 13, opacity: 0.7 }}>{emptyMessage}</p>
  }

  return (
    <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
      <tbody>
        {items.map((item) => {
          const nextStage = NEXT_STAGE[item.status]
          return (
            <tr key={item.id}>
              <td style={{ padding: '4px 0' }}>
                <div>
                  {item.title} {item.owner_type === 'ai' && <Badge tone="neutral">AI</Badge>}
                </div>
                <div style={{ opacity: 0.6, fontSize: 11 }}>
                  {item.owner_user_id ?? 'unclaimed'} · {item.estimated_files.length} file(s) · {item.priority}
                  {item.branch && <> · {item.branch}</>}
                </div>
                {showLifecycle && item.status !== 'completed' && (
                  <div style={{ marginTop: 2 }}>
                    <LifecycleDots status={item.status} />
                  </div>
                )}
              </td>
              <td style={{ padding: '4px 8px' }}>
                <Badge tone={STATUS_TONE[item.status]}>{item.status.replace(/_/g, ' ')}</Badge>
              </td>
              <td style={{ padding: '4px 0', textAlign: 'right', whiteSpace: 'nowrap' }}>
                {item.status !== 'completed' && item.status !== 'claimed' && !item.owner_user_id && (
                  <button type="button" className="btn btn-secondary" onClick={() => handleClaim(item.id)}>Claim</button>
                )}
                {item.owner_user_id && item.status !== 'completed' && (
                  <>
                    {nextStage && (
                      <>
                        <button type="button" className="btn btn-secondary" onClick={() => handleAdvance(item.id, nextStage.status)}>
                          {nextStage.label}
                        </button>{' '}
                      </>
                    )}
                    <button type="button" className="btn btn-secondary" onClick={() => handleRelease(item.id)}>Release</button>{' '}
                    <button type="button" className="btn btn-secondary" onClick={() => handleComplete(item.id)}>Complete</button>
                  </>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
