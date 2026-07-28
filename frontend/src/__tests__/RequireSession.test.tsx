import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import RequireSession from '../components/RequireSession'
import { SessionProvider } from '../session'
import * as api from '../api'
import type { Organization, UserMe } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getUserMe: vi.fn(), getOrganization: vi.fn() }
})

const SESSION_KEY = 'futures-bot:session:user-id'

beforeEach(() => {
  window.localStorage.clear()
  vi.mocked(api.getUserMe).mockReset()
  vi.mocked(api.getOrganization).mockReset()
})

function renderGuarded() {
  return render(
    <SessionProvider>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/welcome" element={<div>Welcome Page</div>} />
          <Route path="/" element={<RequireSession><div>Protected Content</div></RequireSession>} />
        </Routes>
      </MemoryRouter>
    </SessionProvider>,
  )
}

describe('RequireSession', () => {
  it('redirects to /welcome when there is no session', async () => {
    renderGuarded()

    await waitFor(() => expect(screen.getByText('Welcome Page')).toBeInTheDocument())
    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument()
  })

  it('renders the protected content once a session resolves', async () => {
    window.localStorage.setItem(SESSION_KEY, 'alice')
    const org: Organization = { id: 'org1', name: 'Acme', created_at: '2026-07-28T00:00:00+00:00' }
    const me: UserMe = {
      id: 'alice', display_name: 'Alice', username: 'alice', email: null, avatar_url: null,
      org_id: 'org1', role: 'owner', created_at: '2026-07-28T00:00:00+00:00', last_active_at: null,
      timezone: null, preferred_ai_model: null, default_branch_prefix: null, notification_preferences: {},
      api_key: 'fbot_test',
    }
    vi.mocked(api.getUserMe).mockResolvedValue(me)
    vi.mocked(api.getOrganization).mockResolvedValue(org)

    renderGuarded()

    await waitFor(() => expect(screen.getByText('Protected Content')).toBeInTheDocument())
    expect(screen.queryByText('Welcome Page')).not.toBeInTheDocument()
  })

  it('redirects when a stale session fails to resolve', async () => {
    window.localStorage.setItem(SESSION_KEY, 'does-not-exist')
    vi.mocked(api.getUserMe).mockRejectedValue(new Error('404'))

    renderGuarded()

    await waitFor(() => expect(screen.getByText('Welcome Page')).toBeInTheDocument())
  })
})
