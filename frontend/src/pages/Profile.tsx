import { useEffect, useState } from 'react'
import { getUserMe, regenerateApiKey, updateUser } from '../api'
import { useApi } from '../useApi'
import { useSession } from '../session'

// Profile management -- display name/avatar/timezone/preferred AI model/
// default branch naming convention/notification preferences are all
// stored today even though several aren't consulted anywhere yet
// (preferred_ai_model, default_branch_prefix, notification_preferences)
// -- "store the structure even if some settings are not yet active" is
// this page's own brief. API key display/regeneration lives here too --
// this is the one other place (besides registration) a user's own key is
// ever shown.
export default function Profile() {
  const { currentUser, refresh } = useSession()
  const userId = currentUser?.id
  // RequireSession only mounts this page once a session has resolved, so
  // userId is normally set by the time this runs -- but useApi's fetcher
  // still fires on every mount regardless of that guard (e.g. a session
  // that clears itself, via "Switch user" or an expired stale session,
  // while this page is already mounted). Guarding here instead of
  // trusting the route guard avoids calling getUserMe(undefined!.id) and
  // crashing.
  const { data: me, refetch } = useApi(() => (userId ? getUserMe(userId) : Promise.resolve(null)), [userId])

  const [displayName, setDisplayName] = useState('')
  const [avatarUrl, setAvatarUrl] = useState('')
  const [timezone, setTimezone] = useState('')
  const [preferredAiModel, setPreferredAiModel] = useState('')
  const [defaultBranchPrefix, setDefaultBranchPrefix] = useState('')
  const [emailNotifications, setEmailNotifications] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!me) return
    setDisplayName(me.display_name)
    setAvatarUrl(me.avatar_url ?? '')
    setTimezone(me.timezone ?? '')
    setPreferredAiModel(me.preferred_ai_model ?? '')
    setDefaultBranchPrefix(me.default_branch_prefix ?? '')
    setEmailNotifications(Boolean(me.notification_preferences?.email))
  }, [me])

  if (!currentUser || !me) {
    return <p style={{ fontSize: 13, opacity: 0.7 }}>Loading profile…</p>
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setSaved(false)
    try {
      await updateUser(currentUser!.id, {
        display_name: displayName.trim() || undefined,
        avatar_url: avatarUrl.trim() || undefined,
        timezone: timezone.trim() || undefined,
        preferred_ai_model: preferredAiModel.trim() || undefined,
        default_branch_prefix: defaultBranchPrefix.trim() || undefined,
        notification_preferences: { email: emailNotifications },
      })
      await refetch()
      refresh()
      setSaved(true)
    } finally {
      setSaving(false)
    }
  }

  async function handleRegenerateApiKey() {
    if (!window.confirm('Regenerate your API key? The old key will stop working immediately.')) return
    setRegenerating(true)
    try {
      await regenerateApiKey(currentUser!.id)
      await refetch()
    } finally {
      setRegenerating(false)
    }
  }

  async function handleCopyApiKey() {
    if (!me?.api_key) return
    try {
      await navigator.clipboard.writeText(me.api_key)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard permission denied -- the key is still visible on screen.
    }
  }

  return (
    <div style={{ maxWidth: 560 }}>
      <h2>Profile</h2>

      <div className="mc-panel" style={{ marginBottom: 16 }}>
        <div className="mc-panel-head"><h3>Account</h3></div>
        <p style={{ fontSize: 13, margin: '4px 0' }}><strong>Username:</strong> @{me.username}</p>
        <p style={{ fontSize: 13, margin: '4px 0' }}><strong>Email:</strong> {me.email ?? 'not set'}</p>
        <p style={{ fontSize: 13, margin: '4px 0' }}><strong>Role:</strong> {me.role}</p>
        <p style={{ fontSize: 13, margin: '4px 0' }}><strong>Member since:</strong> {me.created_at}</p>
      </div>

      <div className="mc-panel" style={{ marginBottom: 16 }}>
        <div className="mc-panel-head"><h3>Personal API Key</h3></div>
        <div style={{
          fontFamily: 'monospace', fontSize: 13, padding: '8px 12px', border: '1px solid var(--border, #ccc)',
          borderRadius: 4, marginBottom: 8, wordBreak: 'break-all',
        }}
        >
          {me.api_key}
        </div>
        <button type="button" className="btn btn-secondary" onClick={handleCopyApiKey} style={{ marginRight: 8 }}>
          {copied ? 'Copied!' : 'Copy'}
        </button>
        <button type="button" className="btn btn-secondary" onClick={handleRegenerateApiKey} disabled={regenerating}>
          {regenerating ? 'Regenerating…' : 'Regenerate'}
        </button>
        <p style={{ fontSize: 11, opacity: 0.6, marginTop: 8 }}>
          Not yet used for authorization anywhere -- a placeholder for future auth.
        </p>
      </div>

      <form className="mc-panel" onSubmit={handleSave}>
        <div className="mc-panel-head"><h3>Preferences</h3></div>
        <div className="field" style={{ marginBottom: 10 }}>
          <label htmlFor="profile-display-name">Display Name</label>
          <input id="profile-display-name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} style={{ width: '100%' }} />
        </div>
        <div className="field" style={{ marginBottom: 10 }}>
          <label htmlFor="profile-avatar-url">Avatar URL</label>
          <input id="profile-avatar-url" value={avatarUrl} onChange={(e) => setAvatarUrl(e.target.value)} style={{ width: '100%' }} />
        </div>
        <div className="field" style={{ marginBottom: 10 }}>
          <label htmlFor="profile-timezone">Time Zone</label>
          <input
            id="profile-timezone" value={timezone} onChange={(e) => setTimezone(e.target.value)}
            placeholder="e.g. America/New_York" style={{ width: '100%' }}
          />
        </div>
        <div className="field" style={{ marginBottom: 10 }}>
          <label htmlFor="profile-ai-model">Preferred AI Model</label>
          <input
            id="profile-ai-model" value={preferredAiModel} onChange={(e) => setPreferredAiModel(e.target.value)}
            placeholder="e.g. claude-sonnet-5" style={{ width: '100%' }}
          />
          <p style={{ fontSize: 11, opacity: 0.6, marginTop: 2 }}>Stored for future use -- not yet consulted anywhere.</p>
        </div>
        <div className="field" style={{ marginBottom: 10 }}>
          <label htmlFor="profile-branch-prefix">Default Branch Naming Convention</label>
          <input
            id="profile-branch-prefix" value={defaultBranchPrefix} onChange={(e) => setDefaultBranchPrefix(e.target.value)}
            placeholder="e.g. seth/" style={{ width: '100%' }}
          />
        </div>
        <div className="field" style={{ marginBottom: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input type="checkbox" checked={emailNotifications} onChange={(e) => setEmailNotifications(e.target.checked)} />
            Email notifications
          </label>
          <p style={{ fontSize: 11, opacity: 0.6, marginTop: 2 }}>Stored for future use -- no notifications are sent yet.</p>
        </div>
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? 'Saving…' : 'Save Changes'}
        </button>
        {saved && <span style={{ marginLeft: 8, fontSize: 12, opacity: 0.7 }}>Saved.</span>}
      </form>
    </div>
  )
}
