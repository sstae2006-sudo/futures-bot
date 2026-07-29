import { useEffect, useState } from 'react'
import { getIntegrationQueue } from '../../api'
import { useApi } from '../../useApi'
import { Badge } from '../UI'
import type { MergeReadinessLevel } from '../../types'

// SIL Phase 6 "Integration Coordinator" Milestone 1 -- work items in
// testing/ready_for_review, ordered by merge-readiness confidence score
// (a *view* over work_items, not a separate persisted queue -- see
// api/collaboration_service.py::get_integration_queue's docstring).
// Deliberately not called "Merge Queue" -- CollaborationWorkspace's
// existing tab of that name is a plain client-side filter with no
// scoring/ordering behind it; this panel is the real thing, so the two
// must read as clearly different at a glance.
const LEVEL_TONE: Record<MergeReadinessLevel, 'good' | 'warn' | 'bad' | 'neutral'> = {
  ready: 'good',
  needs_review: 'warn',
  risky: 'bad',
  not_ready: 'bad',
}

export default function IntegrationQueuePanel() {
  const { data: entries, refetch } = useApi(() => getIntegrationQueue(), [])
  const [expandedId, setExpandedId] = useState<string | null>(null)

  useEffect(() => {
    const id = setInterval(refetch, 15_000)
    return () => clearInterval(id)
  }, [refetch])

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Integration Queue</h3>
      </div>
      <p style={{ fontSize: 11, opacity: 0.6, marginTop: -4, marginBottom: 8 }}>
        Ordered by merge-readiness confidence score
      </p>
      {!entries && <p style={{ fontSize: 13, opacity: 0.7 }}>Loading…</p>}
      {entries && entries.length === 0 && (
        <p style={{ fontSize: 13, opacity: 0.7 }}>Nothing in testing or ready for review.</p>
      )}
      {entries && entries.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {entries.map((e, i) => {
            const expanded = expandedId === e.work_item.id
            return (
              <li
                key={e.work_item.id}
                style={{ padding: '6px 0', borderBottom: '1px solid var(--border, rgba(128,128,128,0.15))', fontSize: 13 }}
              >
                <button
                  type="button"
                  onClick={() => setExpandedId(expanded ? null : e.work_item.id)}
                  style={{
                    all: 'unset', cursor: 'pointer', display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center', width: '100%',
                  }}
                >
                  <span>
                    <span style={{ opacity: 0.5, marginRight: 6 }}>#{i + 1}</span>
                    {e.work_item.title}
                  </span>
                  <Badge tone={LEVEL_TONE[e.merge_readiness.level]}>{e.merge_readiness.score}/100</Badge>
                </button>
                {e.readiness_note && (
                  <div style={{ opacity: 0.6, fontSize: 11, marginTop: 2 }}>{e.readiness_note}</div>
                )}
                {expanded && (
                  <ul style={{ listStyle: 'none', margin: '6px 0 0', padding: '0 0 0 12px', fontSize: 12 }}>
                    {e.merge_readiness.factors.map((f) => (
                      <li key={f.name} style={{ marginBottom: 2 }}>
                        <strong>{f.name}</strong>{f.penalty !== 0 ? ` (${f.penalty})` : ''}: {f.explanation}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
