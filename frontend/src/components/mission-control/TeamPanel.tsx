import { useCallback, useEffect } from 'react'
import { getSystemHealth, getUsers } from '../../api'
import { useApi } from '../../useApi'
import { Badge } from '../UI'
import type { UserRole } from '../../types'

// Real registered users (Team Collaboration MVP -- accounts/store.py) plus
// the connected-users estimate /api/system/health already tracks. No
// "current session per user" concept yet (that needs real auth/session
// middleware, deliberately not built here -- see accounts/store.py's
// module docstring) -- this shows who *exists*, not who's live right now,
// alongside the one honest "how many distinct clients recently" number.
const ROLE_TONE: Record<UserRole, 'good' | 'warn' | 'neutral'> = {
  owner: 'good',
  admin: 'good',
  member: 'neutral',
  viewer: 'neutral',
}

function timeAgo(iso: string | null): string {
  if (!iso) return 'never'
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).getTime()
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
  const fetchUsers = useCallback(() => getUsers(), [])
  const { data: users, refetch: refetchUsers } = useApi(fetchUsers, [])
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
                <td style={{ padding: '4px 0' }}>{user.display_name}</td>
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
