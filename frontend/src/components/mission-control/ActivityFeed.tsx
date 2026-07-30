import { useEffect } from 'react'
import { getTimeline } from '../../api'
import { useApi } from '../../useApi'
import type { TimelineEntry } from '../../types'

// KNOWN_ISSUES.md ISSUE-040 -- this used to render a hardcoded feed
// (fake backtest/optimization/import results, a verbatim-but-frozen
// scripts/start.ps1 boot sequence from one specific past run) with no
// relationship to what's actually happening now. Wired to the real,
// already-built SIL activity timeline instead (`GET /api/activity/timeline`
// -- collaboration/timeline.py -- work-item events merged with real git
// commits) rather than inventing a second, parallel activity system.
const ICON: Record<TimelineEntry['kind'], string> = {
  work_item: '•',
  commit: '✓',
}

function ActivityRow({ entry }: { entry: TimelineEntry }) {
  return (
    <details className="mc-activity-item tone-neutral">
      <summary>
        <span className="mc-activity-icon" aria-hidden="true">{ICON[entry.kind]}</span>
        <span className="mc-activity-title">{entry.title}</span>
        <span className="mc-activity-time">{entry.timestamp}</span>
      </summary>
      {entry.detail && <div className="mc-activity-detail">{entry.detail}{entry.actor ? ` -- ${entry.actor}` : ''}</div>}
    </details>
  )
}

export default function ActivityFeed() {
  const { data: entries, refetch } = useApi(() => getTimeline({ limit: 15 }), [])

  useEffect(() => {
    const id = setInterval(refetch, 30_000)
    return () => clearInterval(id)
  }, [refetch])

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Live Activity</h3>
      </div>
      {!entries && <p style={{ fontSize: 13, opacity: 0.7 }}>Loading…</p>}
      {entries && entries.length === 0 && (
        <p style={{ fontSize: 13, opacity: 0.7 }}>No recent activity.</p>
      )}
      {entries && entries.length > 0 && (
        <div>
          {entries.map((entry, i) => (
            <ActivityRow key={`${entry.kind}-${entry.timestamp}-${i}`} entry={entry} />
          ))}
        </div>
      )}
    </div>
  )
}
