import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { getUsers } from '../api'
import { useApi } from '../useApi'
import { useSession } from '../session'

// The onboarding gate: "launch SIL, register, create/join an org, open
// Mission Control" starts here. Two paths -- register a brand-new
// account (the primary ask), or pick yourself from the roster if you've
// already registered on this instance before (there's no password, so
// "signing in" is genuinely just selecting who you are -- see
// session.tsx's own docstring for why that's an accepted limitation of a
// no-auth internal tool, not an oversight).
export default function Welcome() {
  const { currentUser, login } = useSession()
  const navigate = useNavigate()
  const { data: users } = useApi(() => getUsers(), [])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState('')

  if (currentUser) {
    return <Navigate to="/" replace />
  }

  function handleContinueAsExisting(e: React.FormEvent) {
    e.preventDefault()
    if (!selectedUserId) return
    login(selectedUserId)
    navigate('/')
  }

  return (
    // Onboarding card layout (2026-10 design audit F4C).
    <div className="onboarding" style={{ minHeight: '100vh', alignItems: 'center' }}>
      <div className="onboarding-card stack-4">
        <div>
          <h1>Welcome to SIL</h1>
          <p className="lede m-0">
            The futures-bot Collaboration Platform -- research, backtesting, and a shared workspace
            for your team (and any AI sessions working alongside you).
          </p>
        </div>

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <button type="button" className="btn btn-primary" onClick={() => navigate('/register')}>
            Register an Account
          </button>
          {users && users.length > 0 && (
            <button type="button" className="btn btn-secondary" onClick={() => setPickerOpen((v) => !v)}>
              I already have an account
            </button>
          )}
        </div>

        {pickerOpen && users && users.length > 0 && (
          <form onSubmit={handleContinueAsExisting} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <select
              value={selectedUserId}
              onChange={(e) => setSelectedUserId(e.target.value)}
              aria-label="Select your account"
              style={{ minWidth: 220 }}
            >
              <option value="">Select your account…</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>{u.display_name} (@{u.username})</option>
              ))}
            </select>
            <button type="submit" className="btn btn-primary" disabled={!selectedUserId}>Continue</button>
          </form>
        )}
      </div>
    </div>
  )
}
