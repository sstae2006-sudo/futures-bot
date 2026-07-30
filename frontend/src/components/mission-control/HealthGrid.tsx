import { getLiveStatus, getResearchServerStatus, getSystemHealth, getSystemOverview } from '../../api'
import type { SystemHealth } from '../../types'
import { useApi } from '../../useApi'

// KNOWN_ISSUES.md ISSUE-040 -- this grid used to render 10 hardcoded
// component-health cards (a fake "94% operational" score included) with
// no relationship to any real check. Rebuilt from only what's genuinely,
// cheaply checkable today: the backend responded at all (this component
// rendering real data at all proves that), the research database's real
// status (`GET /api/system/overview`), and whether a paper-trading /
// live-trading session is actually running (`GET /api/research-server/status`,
// `GET /api/live/status`). Cards for things with no real per-component
// check available yet (a "Risk Engine" health signal, an "AI Services"
// deployment count, per-request API latency) were dropped rather than
// faked -- adding real ones back is a legitimate future addition, not
// something to fabricate now.
type Tone = 'good' | 'bad' | 'warn' | 'neutral'

function HealthCard({ name, tone, status, detail }: { name: string; tone: Tone; status: string; detail: string }) {
  return (
    <div className="mc-health-card">
      <div className="mc-health-card-head">
        <span className="mc-health-card-name">{name}</span>
        <span className={`mc-health-dot tone-${tone}`} aria-hidden="true" />
      </div>
      <div className={`mc-health-card-status tone-${tone}`}>{status}</div>
      <div className="mc-health-card-detail">{detail}</div>
    </div>
  )
}

// Only rendered when the backend is actually in team-deployment mode
// (database.configured) -- a single-developer SQLite setup has no shared
// Postgres/TimescaleDB to report on at all, so there's nothing honest to
// show here otherwise.
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
  const { data: health, error: healthError } = useApi(getSystemHealth, [])
  const { data: overview } = useApi(getSystemOverview, [])
  const { data: liveStatus } = useApi(getLiveStatus, [])
  const { data: researchServer } = useApi(getResearchServerStatus, [])

  const cards: { name: string; tone: Tone; status: string; detail: string }[] = []

  if (health) {
    cards.push({ name: 'Backend', tone: 'good', status: 'Operational', detail: `v${health.version}, ${health.environment}` })
  } else if (healthError) {
    cards.push({ name: 'Backend', tone: 'bad', status: 'Down', detail: healthError })
  }

  if (overview) {
    const dbOk = overview.database_status === 'ok'
    cards.push({
      name: 'Research Database', tone: dbOk ? 'good' : 'warn',
      status: dbOk ? 'Operational' : overview.database_status, detail: overview.database_path,
    })
  }

  if (researchServer) {
    cards.push({
      name: 'Research Server', tone: researchServer.running ? 'good' : 'neutral',
      status: researchServer.running ? 'Running' : 'Stopped',
      detail: researchServer.running ? `Paper trader: ${researchServer.paper_trader.running ? 'active' : 'idle'}` : 'No session running',
    })
  }

  if (liveStatus) {
    const running = liveStatus.status === 'running'
    cards.push({
      name: 'Live Trading', tone: running ? 'good' : liveStatus.status === 'error' ? 'bad' : 'neutral',
      status: liveStatus.status[0].toUpperCase() + liveStatus.status.slice(1),
      detail: running ? `${liveStatus.strategy} on ${liveStatus.contract}` : liveStatus.halt_reason ?? 'No session running',
    })
  }

  const anyBad = cards.some((c) => c.tone === 'bad')
  const anyWarn = cards.some((c) => c.tone === 'warn')
  const overallTone: Tone = anyBad ? 'bad' : anyWarn ? 'warn' : 'good'
  const overallLabel = anyBad ? 'ISSUES DETECTED' : anyWarn ? 'DEGRADED' : 'OPERATIONAL'

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>System Health</h3>
      </div>
      {cards.length > 0 && (
        <div className="mc-health-hero">
          <div>
            <span className={`mc-health-status tone-${overallTone}`}>{overallLabel}</span>
          </div>
          <div>
            <div className="sub">{cards.length} system(s) checked</div>
          </div>
        </div>
      )}
      <div className="mc-health-grid">
        {cards.map((c) => (
          <HealthCard key={c.name} {...c} />
        ))}
        {health?.database.configured && <TeamDatabaseCard health={health} />}
      </div>
    </div>
  )
}
