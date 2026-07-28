import { useEffect, useState } from 'react'
import { getTimeline, getWorkItemConflicts, getWorkItems } from '../../api'
import { useApi } from '../../useApi'
import { useSession } from '../../session'
import { Badge } from '../UI'
import WorkItemTable from './WorkItemTable'
import type { ConflictPair, TimelineEntry, WorkItem } from '../../types'

// SIL Phase 2's "Mission Control Workspace" -- the collaboration
// platform's operating surface, not just a list. Every tab reads real
// data (getWorkItems/getTimeline/getWorkItemConflicts), no mocks, and
// polls every 15s the same way InfrastructurePanel does, so this feels
// as "live" as the rest of Mission Control. Scoped to the signed-in
// user's own organization (session.tsx) -- "My Active Work" uses their
// real user id, not a manually-typed one, now that a real (if still
// no-auth) session concept exists.
const TABS = [
  'My Active Work', 'Team Active Work', 'AI Workers', 'Recent Activity',
  'Merge Queue', 'Conflict Warnings', 'Ready For Review',
] as const
type Tab = (typeof TABS)[number]

const RISK_TONE: Record<string, 'good' | 'warn' | 'bad' | 'neutral'> = {
  no_risk: 'good', low: 'good', medium: 'warn', high: 'bad', critical: 'bad',
}

function TimelineList({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) return <p style={{ fontSize: 13, opacity: 0.7 }}>No recent activity.</p>
  return (
    <ul style={{ listStyle: 'none', padding: 0, margin: 0, fontSize: 13 }}>
      {entries.map((e, i) => (
        <li key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--border, rgba(128,128,128,0.15))' }}>
          <Badge tone={e.kind === 'commit' ? 'neutral' : 'good'}>{e.kind === 'commit' ? 'commit' : e.title}</Badge>{' '}
          {e.kind === 'commit' ? e.title : (e.detail ?? '')}
          <div style={{ opacity: 0.6, fontSize: 11 }}>
            {e.actor ?? 'unknown'} · {e.timestamp}
          </div>
        </li>
      ))}
    </ul>
  )
}

function ConflictList({ conflicts }: { conflicts: ConflictPair[] }) {
  if (conflicts.length === 0) return <p style={{ fontSize: 13, opacity: 0.7 }}>No conflicts detected across active work.</p>
  return (
    <ul style={{ listStyle: 'none', padding: 0, margin: 0, fontSize: 13 }}>
      {conflicts.map((c, i) => (
        <li key={i} style={{ padding: '6px 0', borderBottom: '1px solid var(--border, rgba(128,128,128,0.15))' }}>
          <Badge tone={RISK_TONE[c.risk]}>{c.risk.replace('_', ' ')}</Badge> {c.item_a_title} ↔ {c.item_b_title}
          <div style={{ opacity: 0.7, marginTop: 2 }}>{c.reason}</div>
        </li>
      ))}
    </ul>
  )
}

function TabButton({ tab, active, count, onClick }: { tab: Tab; active: boolean; count?: number; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={active ? 'btn btn-primary' : 'btn btn-secondary'}
      style={{ fontSize: 12, padding: '4px 8px' }}
    >
      {tab}{count !== undefined ? ` (${count})` : ''}
    </button>
  )
}

export default function CollaborationWorkspace() {
  const [tab, setTab] = useState<Tab>('Team Active Work')
  const { currentUser, organization } = useSession()
  const orgId = organization?.id

  const { data: items, refetch: refetchItems } = useApi(() => getWorkItems(undefined, orgId), [orgId])
  const { data: timeline, refetch: refetchTimeline } = useApi(() => getTimeline({ limit: 30 }), [])
  const { data: conflicts, refetch: refetchConflicts } = useApi(() => getWorkItemConflicts(orgId), [orgId])

  useEffect(() => {
    const id = setInterval(() => {
      refetchItems()
      refetchTimeline()
      refetchConflicts()
    }, 15_000)
    return () => clearInterval(id)
  }, [refetchItems, refetchTimeline, refetchConflicts])

  const activeItems = (items ?? []).filter((i: WorkItem) => i.status !== 'completed')
  const myItems = activeItems.filter((i) => currentUser && i.owner_user_id === currentUser.id)
  const aiItems = activeItems.filter((i) => i.owner_type === 'ai')
  const mergeQueueItems = activeItems.filter((i) => i.status === 'testing' || i.status === 'ready_for_review')
  const readyForReviewItems = activeItems.filter((i) => i.status === 'ready_for_review')

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Collaboration Workspace</h3>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>
        {TABS.map((t) => {
          const counts: Partial<Record<Tab, number>> = {
            'My Active Work': myItems.length,
            'Team Active Work': activeItems.length,
            'AI Workers': aiItems.length,
            'Merge Queue': mergeQueueItems.length,
            'Conflict Warnings': conflicts?.length ?? 0,
            'Ready For Review': readyForReviewItems.length,
          }
          return <TabButton key={t} tab={t} active={tab === t} count={counts[t]} onClick={() => setTab(t)} />
        })}
      </div>

      {tab === 'My Active Work' && (
        <WorkItemTable items={myItems} onRefetch={refetchItems} emptyMessage="No active work items assigned to you." />
      )}

      {tab === 'Team Active Work' && (
        <WorkItemTable items={activeItems} onRefetch={refetchItems} emptyMessage="No active work items." />
      )}

      {tab === 'AI Workers' && (
        <WorkItemTable items={aiItems} onRefetch={refetchItems} emptyMessage="No AI-owned work items active." />
      )}

      {tab === 'Recent Activity' && <TimelineList entries={timeline ?? []} />}

      {tab === 'Merge Queue' && (
        <WorkItemTable items={mergeQueueItems} onRefetch={refetchItems} emptyMessage="Nothing in testing or ready for review." />
      )}

      {tab === 'Conflict Warnings' && <ConflictList conflicts={conflicts ?? []} />}

      {tab === 'Ready For Review' && (
        <WorkItemTable items={readyForReviewItems} onRefetch={refetchItems} emptyMessage="Nothing is ready for review." />
      )}
    </div>
  )
}
