import { getUsers, updateUser } from '../api'
import { useApi } from '../useApi'
import { useSession } from '../session'
import { Badge } from '../components/UI'
import { isOnline } from '../format'
import type { User, UserRole } from '../types'

const ROLE_TONE: Record<UserRole, 'good' | 'warn' | 'neutral'> = {
  owner: 'good',
  admin: 'good',
  member: 'neutral',
  viewer: 'neutral',
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
