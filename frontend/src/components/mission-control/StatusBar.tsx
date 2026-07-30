import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getBranchInfo, getSystemHealth } from '../../api'
import { useApi } from '../../useApi'
import { useSession } from '../../session'

// Global chrome, rendered once in Layout.tsx above <Outlet/> -- "always
// visible" per the Mission Control spec. Version/environment/uptime come
// from GET /api/system/health. Branch/commit (KNOWN_ISSUES.md ISSUE-040
// -- these were previously hardcoded to one specific past commit,
// silently going stale on every commit since) now come from the real
// GET /api/git/branch-info (collaboration/git_info.py -- already built
// for SIL Phase 6, live `git` introspection, never persisted). "Last
// Startup" was dropped rather than fixed -- there's no real absolute
// timestamp for it beyond re-deriving one from `uptime_seconds`, and
// "Uptime" already conveys the same information honestly. Only the
// clock genuinely ticks independent of any backend call.
const FALLBACK_VERSION = '0.7.0'

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
  const { currentUser, organization, logout } = useSession()
  const now = useClock()
  const timeLabel = now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const { data: health, refetch } = useApi(getSystemHealth, [])
  const { data: branchInfo } = useApi(() => getBranchInfo(), [])
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
  const branch = branchInfo?.is_detached ? 'detached' : branchInfo?.branch ?? '—'
  const commit = branchInfo?.last_commit?.short_hash ?? '—'

  return (
    <div className="status-bar">
      <div className="status-bar-group">
        <div className="status-bar-item">
          <span className="sb-label">Branch</span>
          <span className="sb-value">{branch}</span>
        </div>
        <span className="status-bar-sep">|</span>
        <div className="status-bar-item">
          <span className="sb-label">Commit</span>
          <span className="sb-value">{commit}</span>
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
          <span className="sb-label">Uptime</span>
          <span className="sb-value">{uptime}</span>
        </div>
        <span className="status-bar-sep">|</span>
        <div className="status-bar-item">
          <span className="sb-value">{timeLabel}</span>
        </div>
        {currentUser && organization && (
          <>
            <span className="status-bar-sep">|</span>
            <div className="status-bar-item">
              <Link to="/profile" className="sb-value">
                {currentUser.display_name} ({currentUser.role}) · {organization.name}
              </Link>
              <button
                type="button"
                onClick={logout}
                title="Switch user"
                style={{ marginLeft: 6, fontSize: 11, opacity: 0.7, background: 'none', border: 'none', cursor: 'pointer', color: 'inherit' }}
              >
                Switch
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
