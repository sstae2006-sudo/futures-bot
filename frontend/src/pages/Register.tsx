import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { createOrganization, createUser, getOrganizations } from '../api'
import { useApi } from '../useApi'
import { useSession } from '../session'
import type { UserMe } from '../types'

type OrgMode = 'create' | 'join'
type Step = 'organization' | 'account' | 'success'

// Registration flow -- deliberately no password/OAuth (see
// accounts/store.py's docstring: this is an internal development
// platform, not a public product). Role is never a form field: the org
// creator becomes `owner` automatically, anyone joining an existing org
// becomes `member` -- a human owner/admin can promote someone afterward
// via the Team Members page. That's a deliberate simplicity choice, not
// a missing feature: letting a self-registering user pick their own
// "owner"/"admin" role from a dropdown would be a strange thing to allow
// even in a no-auth system.
export default function Register() {
  const { currentUser, login } = useSession()
  const navigate = useNavigate()
  const { data: organizations } = useApi(() => getOrganizations(), [])

  const [step, setStep] = useState<Step>('organization')
  const [orgMode, setOrgMode] = useState<OrgMode>('create')
  const [newOrgName, setNewOrgName] = useState('')
  const [selectedOrgId, setSelectedOrgId] = useState('')

  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [createdUser, setCreatedUser] = useState<UserMe | null>(null)
  const [copied, setCopied] = useState(false)
  // If org creation succeeds but the subsequent createUser call fails
  // (e.g. the chosen username is already taken), retrying the form used
  // to call createOrganization again with the same name and fail with
  // "already exists," leaving the user stuck -- the org now exists with
  // no owner, and there was no way to get past this screen. Caching the
  // id here means a retry reuses the org it already created instead of
  // trying to create it again.
  const [createdOrgId, setCreatedOrgId] = useState<string | null>(null)

  if (currentUser) {
    return <Navigate to="/" replace />
  }

  function handleOrganizationSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (orgMode === 'create' && !newOrgName.trim()) {
      setError('Enter a name for your new organization.')
      return
    }
    if (orgMode === 'join' && !selectedOrgId) {
      setError('Select an organization to join.')
      return
    }
    setStep('account')
  }

  async function handleAccountSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (!displayName.trim() || !username.trim()) {
      setError('Display name and username are both required.')
      return
    }
    setBusy(true)
    try {
      let orgId = orgMode === 'create' ? createdOrgId ?? selectedOrgId : selectedOrgId
      if (orgMode === 'create' && !createdOrgId) {
        const org = await createOrganization(newOrgName.trim())
        orgId = org.id
        setCreatedOrgId(org.id)
      }
      const user = await createUser({
        display_name: displayName.trim(),
        username: username.trim(),
        org_id: orgId,
        role: orgMode === 'create' ? 'owner' : 'member',
        email: email.trim() || undefined,
      })
      setCreatedUser(user)
      setStep('success')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed -- please try again.')
    } finally {
      setBusy(false)
    }
  }

  async function handleCopyApiKey() {
    if (!createdUser?.api_key) return
    try {
      await navigator.clipboard.writeText(createdUser.api_key)
      setCopied(true)
    } catch {
      // Clipboard access can be denied by the browser -- the key is still
      // shown on screen for a manual copy, so this isn't fatal.
    }
  }

  function handleFinish() {
    if (!createdUser) return
    login(createdUser.id)
    navigate('/')
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '48px 24px' }}>
      <div style={{ width: '100%', maxWidth: 480 }}>
        <h1 style={{ marginBottom: 4 }}>Register</h1>
        <p style={{ opacity: 0.7, marginBottom: 24, fontSize: 13 }}>
          Step {step === 'organization' ? 1 : step === 'account' ? 2 : 3} of 3
        </p>

        {error && <p role="alert" style={{ color: 'var(--danger, #d33)', marginBottom: 12 }}>{error}</p>}

        {step === 'organization' && (
          <form onSubmit={handleOrganizationSubmit}>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <input type="radio" checked={orgMode === 'create'} onChange={() => setOrgMode('create')} />
                Create a new organization
              </label>
              {orgMode === 'create' && (
                <input
                  placeholder="Organization name"
                  value={newOrgName}
                  onChange={(e) => { setNewOrgName(e.target.value); setCreatedOrgId(null) }}
                  aria-label="Organization name"
                  style={{ width: '100%', marginLeft: 24, marginBottom: 8 }}
                />
              )}
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <input type="radio" checked={orgMode === 'join'} onChange={() => setOrgMode('join')} />
                Join an existing organization
              </label>
              {orgMode === 'join' && (
                <select
                  value={selectedOrgId}
                  onChange={(e) => setSelectedOrgId(e.target.value)}
                  aria-label="Select organization to join"
                  style={{ width: '100%', marginLeft: 24 }}
                >
                  <option value="">Select an organization…</option>
                  {(organizations ?? []).map((org) => (
                    <option key={org.id} value={org.id}>{org.name}</option>
                  ))}
                </select>
              )}
            </div>
            <button type="submit" className="btn btn-primary">Next</button>
          </form>
        )}

        {step === 'account' && (
          <form onSubmit={handleAccountSubmit}>
            <div className="field" style={{ marginBottom: 12 }}>
              <label htmlFor="reg-display-name">Display Name</label>
              <input
                id="reg-display-name" value={displayName} onChange={(e) => setDisplayName(e.target.value)}
                style={{ width: '100%' }}
              />
            </div>
            <div className="field" style={{ marginBottom: 12 }}>
              <label htmlFor="reg-username">Username</label>
              <input
                id="reg-username" value={username} onChange={(e) => setUsername(e.target.value)}
                style={{ width: '100%' }}
              />
            </div>
            <div className="field" style={{ marginBottom: 12 }}>
              <label htmlFor="reg-email">Email (optional)</label>
              <input
                id="reg-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                style={{ width: '100%' }}
              />
            </div>
            <p style={{ fontSize: 12, opacity: 0.7, marginBottom: 12 }}>
              {orgMode === 'create'
                ? "You'll be the Owner of this new organization."
                : "You'll join as a Member -- an existing Owner/Admin can change your role later."}
            </p>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="button" className="btn btn-secondary" onClick={() => setStep('organization')}>Back</button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? 'Creating…' : 'Create Account'}
              </button>
            </div>
          </form>
        )}

        {step === 'success' && createdUser && (
          <div>
            <h2 style={{ fontSize: 16, marginBottom: 8 }}>Welcome, {createdUser.display_name}!</h2>
            <p style={{ fontSize: 13, opacity: 0.8, marginBottom: 12 }}>
              Here is your personal API key. Save it now -- it won't be shown again in full
              (you can always regenerate a new one from your Profile page).
            </p>
            <div style={{
              fontFamily: 'monospace', fontSize: 13, padding: '8px 12px', border: '1px solid var(--border, #ccc)',
              borderRadius: 4, marginBottom: 8, wordBreak: 'break-all',
            }}
            >
              {createdUser.api_key}
            </div>
            <button type="button" className="btn btn-secondary" onClick={handleCopyApiKey} style={{ marginBottom: 16 }}>
              {copied ? 'Copied!' : 'Copy to clipboard'}
            </button>
            <div>
              <button type="button" className="btn btn-primary" onClick={handleFinish}>
                Continue to Mission Control →
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
