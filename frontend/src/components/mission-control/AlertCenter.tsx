import { Link } from 'react-router-dom'
import type { AlertItem, AlertSeverity } from './missionControlData'
import { alerts } from './missionControlData'

const GROUPS: { severity: AlertSeverity; label: string; tone: 'bad' | 'warn' | 'neutral' }[] = [
  { severity: 'critical', label: 'Critical', tone: 'bad' },
  { severity: 'warning', label: 'Warning', tone: 'warn' },
  { severity: 'info', label: 'Info', tone: 'neutral' },
]

function AlertGroup({ label, tone, items }: { label: string; tone: 'bad' | 'warn' | 'neutral'; items: AlertItem[] }) {
  return (
    <div className="mc-alert-group">
      <div className={`mc-alert-group-label tone-${tone}`}>
        {label}
        <span className={`mc-alert-count badge-${tone === 'bad' ? 'bad' : tone === 'warn' ? 'warn' : 'neutral'}`}>{items.length}</span>
      </div>
      {items.length === 0 && <div className="mc-alert-empty">Nothing to report.</div>}
      {items.map((item, i) => (
        <div key={i} className={`mc-alert-row tone-${tone}`}>
          <span className="msg">{item.message}</span>
          <span className="ts">{item.time}</span>
        </div>
      ))}
    </div>
  )
}

export default function AlertCenter() {
  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Alert Center</h3>
        <Link to="/logs">Full logs →</Link>
      </div>
      {GROUPS.map((group) => (
        <AlertGroup key={group.severity} label={group.label} tone={group.tone} items={alerts[group.severity]} />
      ))}
    </div>
  )
}
