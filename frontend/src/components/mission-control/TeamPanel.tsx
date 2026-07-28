import { useEffect } from 'react'
import { getSystemHealth, getUsers } from '../../api'
import { useApi } from '../../useApi'
import { useSession } from '../../session'
import { isOnline } from '../../format'
import { Badge } from '../UI'
import type { UserRole } from '../../types'

// Real registered users (accounts/store.py) scoped to the signed-in
// user's own organization (session.tsx -- Registration & Organization
// Management, 2026-07-28), plus the connected-users estimate
// /api/system/health already tracks. Online status is derived from
// last_active_at (the heartbeat an active client session sends), not a
// real presence/websocket system -- see accounts/store.py's docstring
// for why nothing more precise exists without real auth.
const ROLE_TONE: Record<UserRole, 'good' | 'warn' | 'neutral'> = {
  owner: 'good',
  admin: 'good',
  member: 'neutral',
  viewer: 'neutral',
}

function timeAgo(iso: string | null): string {
  if (!iso) return 'never'
  // SQLite's timestamps (local mode) come back as "YYYY-MM-DD HH:MM:SS" --
  // no "T", no timezone marker, but the value is UTC (SQLite's own
  // datetime('now')). `new Date()` on that raw string is parsed as *local*
  // time by every JS engine (confirmed directly: no Z means no UTC
  // assumption), silently shifting it by the browser's own UTC offset --
  // normalize to real ISO 8601 (T separator + explicit Z) before parsing,
  // rather than relying on any engine's leniency about the space form.
  const hasTimezone = iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso)
  const normalized = hasTimezone ? iso.replace(' ', 'T') : `${iso.replace(' ', 'T')}Z`
  const then = new Date(normalized).getTime()
  const diffMs = Date.now() - then
  if (diffMs < 0 || Number.isNaN(diffMs)) return iso
  const minutes = Math.floor(diffMs / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export default function TeamPanel() {
  const { organization } = useSession()
  const orgId = organization?.id
  const { data: users, refetch: refetchUsers } = useApi(() => getUsers(orgId), [orgId])
  const { data: health, refetch: refetchHealth } = useApi(getSystemHealth, [])

  useEffect(() => {
    const id = setInterval(() => {
      refetchUsers()
      refetchHealth()
    }, 30_000)
    return () => clearInterval(id)
  }, [refetchUsers, refetchHealth])

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Team</h3>
      </div>
      <p style={{ fontSize: 13, opacity: 0.8, marginBottom: 8 }}>
        {health ? `${health.connected_users} distinct client${health.connected_users === 1 ? '' : 's'} in the last 15 minutes` : 'Loading…'}
      </p>
      {users && users.length === 0 && (
        <p style={{ fontSize: 13, opacity: 0.7 }}>No users registered yet.</p>
      )}
      {users && users.length > 0 && (
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <tbody>
            {users.map((user) => (
              <tr key={user.id}>
                <td style={{ padding: '4px 0' }}>
                  <Badge tone={isOnline(user.last_active_at) ? 'good' : 'neutral'}>
                    {isOnline(user.last_active_at) ? 'online' : 'offline'}
                  </Badge>{' '}
                  {user.display_name}
                </td>
                <td style={{ padding: '4px 0' }}>
                  <Badge tone={ROLE_TONE[user.role]}>{user.role}</Badge>
                </td>
                <td style={{ padding: '4px 0', textAlign: 'right', opacity: 0.7 }}>
                  {timeAgo(user.last_active_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
