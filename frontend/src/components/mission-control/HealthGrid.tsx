import { getSystemHealth } from '../../api'
import type { SystemHealth } from '../../types'
import { useApi } from '../../useApi'
import type { ComponentHealth, HealthStatus } from './missionControlData'
import { componentHealth, overallHealth } from './missionControlData'

const STATUS_TONE: Record<HealthStatus, 'good' | 'bad' | 'warn' | 'neutral'> = {
  operational: 'good',
  degraded: 'warn',
  down: 'bad',
  unknown: 'neutral',
}

const STATUS_LABEL: Record<HealthStatus, string> = {
  operational: 'Operational',
  degraded: 'Degraded',
  down: 'Down',
  unknown: 'No session',
}

const OVERALL_LABEL: Record<HealthStatus, string> = {
  operational: 'OPERATIONAL',
  degraded: 'DEGRADED',
  down: 'CRITICAL',
  unknown: 'UNKNOWN',
}

function HealthCard({ component }: { component: ComponentHealth }) {
  const tone = STATUS_TONE[component.status]
  return (
    <div className="mc-health-card">
      <div className="mc-health-card-head">
        <span className="mc-health-card-name">{component.name}</span>
        <span className={`mc-health-dot tone-${tone}`} aria-hidden="true" />
      </div>
      <div className={`mc-health-card-status tone-${tone}`}>{STATUS_LABEL[component.status]}</div>
      <div className="mc-health-card-detail">{component.detail}</div>
      <div className="mc-health-card-updated">Updated {component.lastUpdate}</div>
    </div>
  )
}

// Only rendered when the backend is actually in team-deployment mode
// (database.configured) -- a single-developer SQLite setup has no shared
// Postgres/TimescaleDB to report on at all, so there's nothing honest to
// show here otherwise. Distinct from the mock "Database"/"Research
// Database" cards below (those describe the local market_data.db/
// research.db SQLite files, unaffected by team mode either way).
function TeamDatabaseCard({ health }: { health: SystemHealth }) {
  const tone = health.database.ok ? 'good' : 'bad'
  const detail = health.database.ok
    ? `Connected, ${health.database.latency_ms?.toFixed(0) ?? '?'}ms latency`
    : health.database.error ?? 'Unreachable'
  return (
    <div className="mc-health-card">
      <div className="mc-health-card-head">
        <span className="mc-health-card-name">Team Database (TimescaleDB)</span>
        <span className={`mc-health-dot tone-${tone}`} aria-hidden="true" />
      </div>
      <div className={`mc-health-card-status tone-${tone}`}>{health.database.ok ? 'Operational' : 'Down'}</div>
      <div className="mc-health-card-detail">{detail}</div>
      <div className="mc-health-card-updated">
        {health.connected_users} connected · last backup {health.last_backup_at ?? 'never'}
      </div>
    </div>
  )
}

export default function HealthGrid() {
  const tone = STATUS_TONE[overallHealth.status]
  const { data: health } = useApi(getSystemHealth, [])

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>System Health</h3>
      </div>
      <div className="mc-health-hero">
        <div>
          <span className={`mc-health-status tone-${tone}`}>{OVERALL_LABEL[overallHealth.status]}</span>
        </div>
        <div>
          <span className={`mc-health-score tone-${tone}`}>{overallHealth.score}%</span>
        </div>
        <div>
          <div className="sub">Overall Platform Health</div>
          <div className="sub">{overallHealth.breakdown}</div>
        </div>
      </div>
      <div className="mc-health-grid">
        {componentHealth.map((component) => (
          <HealthCard key={component.name} component={component} />
        ))}
        {health?.database.configured && <TeamDatabaseCard health={health} />}
      </div>
    </div>
  )
}
