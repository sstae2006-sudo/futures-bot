import { Link } from 'react-router-dom'
import { getAutomationStatus, getSystemHealth } from '../../api'
import { useApi } from '../../useApi'

// KNOWN_ISSUES.md ISSUE-040 -- this used to render two hardcoded warning/
// info entries with no relationship to real system state. Derived from
// real signals instead: any background scheduler's last_error
// (`GET /api/automation/status` -- git-watcher/maintenance/git-sync all
// report this already) and the team database's real reachability
// (`GET /api/system/health`). No new alert-generation backend built --
// this is pure display-layer relabeling of already-computed, already-
// real status fields, not a new calculation.
type Severity = 'critical' | 'warning' | 'info'

interface Alert {
  message: string
  time: string | null
}

const GROUPS: { severity: Severity; label: string; tone: 'bad' | 'warn' | 'neutral' }[] = [
  { severity: 'critical', label: 'Critical', tone: 'bad' },
  { severity: 'warning', label: 'Warning', tone: 'warn' },
  { severity: 'info', label: 'Info', tone: 'neutral' },
]

function AlertGroup({ label, tone, items }: { label: string; tone: 'bad' | 'warn' | 'neutral'; items: Alert[] }) {
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
          <span className="ts">{item.time ?? ''}</span>
        </div>
      ))}
    </div>
  )
}

export default function AlertCenter() {
  const { data: automation } = useApi(getAutomationStatus, [])
  const { data: health } = useApi(getSystemHealth, [])

  const alerts: Record<Severity, Alert[]> = { critical: [], warning: [], info: [] }

  if (automation) {
    const schedulers = [
      { name: 'Git watcher', s: automation.git_watcher },
      { name: 'Maintenance', s: automation.maintenance },
      { name: 'Git sync', s: automation.git_sync },
    ]
    for (const { name, s } of schedulers) {
      if (s.last_error) {
        alerts.critical.push({ message: `${name}: ${s.last_error}`, time: s.last_cycle_at })
      }
    }
    if (automation.maintenance.last_db_health_ok === false) {
      alerts.critical.push({ message: 'Team database health check failed', time: automation.maintenance.last_cycle_at })
    }
  }

  if (health?.database.configured && !health.database.ok) {
    alerts.critical.push({ message: `Team database (TimescaleDB) unreachable: ${health.database.error ?? 'unknown error'}`, time: null })
  }

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
