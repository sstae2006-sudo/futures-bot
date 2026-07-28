import { useEffect, useState } from 'react'
import { getUsers, updateOrganization } from '../api'
import { useApi } from '../useApi'
import { useSession } from '../session'

// Organization settings -- today just a rename, gated behind
// `can('manage_organization')` (owner/admin -- accounts/permissions.py's
// table, mirrored client-side; advisory only, see that module's
// docstring). Member count comes from the same roster Team Members
// shows, not a separate count endpoint.
export default function OrganizationSettings() {
  const { organization, can, refresh } = useSession()
  const { data: members } = useApi(() => getUsers(organization?.id), [organization?.id])

  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (organization) setName(organization.name)
  }, [organization])

  if (!organization) {
    return <p style={{ fontSize: 13, opacity: 0.7 }}>Loading organization…</p>
  }

  const canManage = can('manage_organization')

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSaved(false)
    if (!name.trim()) return
    setSaving(true)
    try {
      await updateOrganization(organization!.id, name.trim())
      refresh()
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not rename the organization.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ maxWidth: 560 }}>
      <h2>Organization Settings</h2>

      <div className="mc-panel" style={{ marginBottom: 16 }}>
        <div className="mc-panel-head"><h3>Overview</h3></div>
        <p style={{ fontSize: 13, margin: '4px 0' }}><strong>Created:</strong> {organization.created_at}</p>
        <p style={{ fontSize: 13, margin: '4px 0' }}><strong>Members:</strong> {members ? members.length : '…'}</p>
      </div>

      <form className="mc-panel" onSubmit={handleSave}>
        <div className="mc-panel-head"><h3>Name</h3></div>
        {error && <p role="alert" style={{ color: 'var(--danger, #d33)', marginBottom: 8 }}>{error}</p>}
        <div className="field" style={{ marginBottom: 12 }}>
          <input
            value={name} onChange={(e) => setName(e.target.value)} disabled={!canManage}
            aria-label="Organization name" style={{ width: '100%' }}
          />
        </div>
        {canManage ? (
          <>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            {saved && <span style={{ marginLeft: 8, fontSize: 12, opacity: 0.7 }}>Saved.</span>}
          </>
        ) : (
          <p style={{ fontSize: 12, opacity: 0.6 }}>Only an Owner or Admin can rename the organization.</p>
        )}
      </form>
    </div>
  )
}
