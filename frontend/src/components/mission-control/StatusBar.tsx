import { useEffect, useState } from 'react'
import { getSystemHealth } from '../../api'
import { useApi } from '../../useApi'

// Global chrome, rendered once in Layout.tsx above <Outlet/> -- "always
// visible" per the Mission Control spec. Version/environment/uptime come
// from GET /api/system/health (see api/routes/system.py::get_health) --
// the one real-data slice the team-deployment plan (item #7) scoped for
// this bar. Branch/commit stay static placeholders (git info, not part of
// that route's payload) and "Last Startup" isn't provided either -- both
// noted in missionControlData.ts. Only the clock genuinely ticks
// independent of any backend call.
const GIT_BRANCH = 'main'
const GIT_COMMIT = '280d52b'
const FALLBACK_VERSION = '0.7.0'
const LAST_STARTUP = '17m ago'

function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const remMinutes = minutes % 60
  return remMinutes ? `${hours}h ${remMinutes}m` : `${hours}h`
}

export default function StatusBar() {
  const now = useClock()
  const timeLabel = now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const { data: health, refetch } = useApi(getSystemHealth, [])
  // Refetches every 30s so uptime/environment/version stay live without a
  // page reload -- cheap (one small JSON payload) and this bar is always
  // on screen for as long as the app is open.
  useEffect(() => {
    const id = setInterval(refetch, 30_000)
    return () => clearInterval(id)
  }, [refetch])

  const version = health?.version ?? FALLBACK_VERSION
  const environment = health?.environment ?? 'development'
  const uptime = health ? formatUptime(health.uptime_seconds) : '—'

  return (
    <div className="status-bar">
      <div className="status-bar-group">
        <div className="status-bar-item">
          <span className="sb-label">Branch</span>
          <span className="sb-value">{GIT_BRANCH}</span>
        </div>
        <span className="status-bar-sep">|</span>
        <div className="status-bar-item">
          <span className="sb-label">Commit</span>
          <span className="sb-value">{GIT_COMMIT}</span>
        </div>
        <span className="status-bar-sep">|</span>
        <div className="status-bar-item">
          <span className="sb-label">Version</span>
          <span className="sb-value">v{version}</span>
        </div>
        <span className="status-bar-sep">|</span>
        <span className={`env-badge env-badge-${environment}`}>{environment}</span>
      </div>
      <div className="status-bar-group">
        <div className="status-bar-item">
          <span className="sb-label">Last Startup</span>
          <span className="sb-value">{LAST_STARTUP}</span>
        </div>
        <span className="status-bar-sep">|</span>
        <div className="status-bar-item">
          <span className="sb-label">Uptime</span>
          <span className="sb-value">{uptime}</span>
        </div>
        <span className="status-bar-sep">|</span>
        <div className="status-bar-item">
          <span className="sb-value">{timeLabel}</span>
        </div>
      </div>
    </div>
  )
}
