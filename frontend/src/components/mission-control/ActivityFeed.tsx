import type { ActivityEvent } from './missionControlData'
import { activityFeed } from './missionControlData'

const ICON: Record<ActivityEvent['tone'], string> = {
  good: '✓',
  bad: '✕',
  warn: '!',
  neutral: '•',
}

function ActivityRow({ event }: { event: ActivityEvent }) {
  return (
    <details className={`mc-activity-item tone-${event.tone}`}>
      <summary>
        <span className="mc-activity-icon" aria-hidden="true">{ICON[event.tone]}</span>
        <span className="mc-activity-title">{event.title}</span>
        <span className="mc-activity-time">{event.time}</span>
      </summary>
      {event.detail && <div className="mc-activity-detail">{event.detail}</div>}
    </details>
  )
}

export default function ActivityFeed() {
  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Live Activity</h3>
      </div>
      <div>
        {activityFeed.map((event, i) => (
          <ActivityRow key={`${event.title}-${i}`} event={event} />
        ))}
      </div>
    </div>
  )
}
