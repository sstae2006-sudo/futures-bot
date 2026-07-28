import { getUsers, updateUser } from '../api'
import { useApi } from '../useApi'
import { useSession } from '../session'
import { Badge } from '../components/UI'
import type { User, UserRole } from '../types'

const ROLE_TONE: Record<UserRole, 'good' | 'warn' | 'neutral'> = {
  owner: 'good',
  admin: 'good',
  member: 'neutral',
  viewer: 'neutral',
}

//: A user "seen" this recently is shown online -- derived from
//: `last_active_at` (the heartbeat every active client session sends),
//: not a real presence/websocket system. See `accounts/store.py`'s
//: docstring for why nothing more precise exists without real auth.
const ONLINE_WINDOW_MS = 2 * 60 * 1000

function isOnline(lastActiveAt: string | null): boolean {
  if (!lastActiveAt) return false
  const hasTimezone = lastActiveAt.endsWith('Z') || /[+-]\d\d:\d\d$/.test(lastActiveAt)
  const normalized = hasTimezone ? lastActiveAt.replace(' ', 'T') : `${lastActiveAt.replace(' ', 'T')}Z`
  const then = new Date(normalized).getTime()
  if (Number.isNaN(then)) return false
  return Date.now() - then < ONLINE_WINDOW_MS
}

// Team Members -- the fuller roster view TeamPanel's Mission Control
// widget only summarizes. Role changes (manage_members capability --
// accounts/permissions.py's table, advisory only here too) reuse the
// existing PATCH /api/users/{id}, no new backend endpoint needed.
export default function TeamMembers() {
  const { organization, currentUser, can, refresh: refreshSession } = useSession()
  const { data: members, refetch } = useApi(() => getUsers(organization?.id), [organization?.id])

  const canManageMembers = can('manage_members')

  async function handleRoleChange(member: User, role: UserRole) {
    await updateUser(member.id, { role })
    await refetch()
    if (member.id === currentUser?.id) refreshSession()
  }

  if (!organization) {
    return <p style={{ fontSize: 13, opacity: 0.7 }}>Loading organization…</p>
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <h2>Team Members</h2>
      <p style={{ opacity: 0.7, fontSize: 13, marginBottom: 16 }}>{organization.name}</p>

      {members && members.length === 0 && <p style={{ fontSize: 13, opacity: 0.7 }}>No members yet.</p>}
      {members && members.length > 0 && (
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ textAlign: 'left', opacity: 0.6 }}>
              <th style={{ padding: '4px 0' }}>Name</th>
              <th>Status</th>
              <th>Role</th>
              <th>Joined</th>
            </tr>
          </thead>
          <tbody>
            {members.map((member) => (
              <tr key={member.id}>
                <td style={{ padding: '6px 0' }}>
                  {member.display_name} <span style={{ opacity: 0.6 }}>@{member.username}</span>
                  {member.id === currentUser?.id && <Badge tone="neutral"> you</Badge>}
                </td>
                <td>
                  <Badge tone={isOnline(member.last_active_at) ? 'good' : 'neutral'}>
                    {isOnline(member.last_active_at) ? 'online' : 'offline'}
                  </Badge>
                </td>
                <td>
                  {canManageMembers ? (
                    <select
                      value={member.role}
                      onChange={(e) => handleRoleChange(member, e.target.value as UserRole)}
                      aria-label={`Role for ${member.display_name}`}
                    >
                      <option value="owner">Owner</option>
                      <option value="admin">Admin</option>
                      <option value="member">Member</option>
                      <option value="viewer">Viewer</option>
                    </select>
                  ) : (
                    <Badge tone={ROLE_TONE[member.role]}>{member.role}</Badge>
                  )}
                </td>
                <td style={{ opacity: 0.7 }}>{member.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
