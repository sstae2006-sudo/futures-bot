import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { SessionProvider, useSession } from '../session'
import * as api from '../api'
import type { Organization, UserMe } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getUserMe: vi.fn(), getOrganization: vi.fn() }
})

const SESSION_KEY = 'futures-bot:session:user-id'

const ORG: Organization = { id: 'org1', name: 'Acme', created_at: '2026-07-28T00:00:00+00:00' }
const ME: UserMe = {
  id: 'alice', display_name: 'Alice', username: 'alice', email: null, avatar_url: null,
  org_id: 'org1', role: 'owner', created_at: '2026-07-28T00:00:00+00:00', last_active_at: null,
  timezone: null, preferred_ai_model: null, default_branch_prefix: null, notification_preferences: {},
  api_key: 'fbot_test',
}

function Probe() {
  const { currentUser, organization, loading, error, can } = useSession()
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="user">{currentUser ? currentUser.display_name : 'none'}</span>
      <span data-testid="org">{organization ? organization.name : 'none'}</span>
      <span data-testid="error">{error ?? 'none'}</span>
      <span data-testid="can-manage-org">{String(can('manage_organization'))}</span>
    </div>
  )
}

beforeEach(() => {
  window.localStorage.clear()
  vi.mocked(api.getUserMe).mockReset()
  vi.mocked(api.getOrganization).mockReset()
})

describe('SessionProvider', () => {
  it('resolves to no user, not loading, when localStorage is empty', async () => {
    render(<SessionProvider><Probe /></SessionProvider>)

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'))
    expect(screen.getByTestId('user')).toHaveTextContent('none')
    expect(api.getUserMe).not.toHaveBeenCalled()
  })

  it('resolves the stored user id into a real user + organization', async () => {
    window.localStorage.setItem(SESSION_KEY, 'alice')
    vi.mocked(api.getUserMe).mockResolvedValue(ME)
    vi.mocked(api.getOrganization).mockResolvedValue(ORG)

    render(<SessionProvider><Probe /></SessionProvider>)

    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('Alice'))
    expect(screen.getByTestId('org')).toHaveTextContent('Acme')
  })

  it('clears a stale session that no longer resolves to a real user', async () => {
    window.localStorage.setItem(SESSION_KEY, 'does-not-exist')
    vi.mocked(api.getUserMe).mockRejectedValue(new Error('404'))

    render(<SessionProvider><Probe /></SessionProvider>)

    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('none'))
    expect(window.localStorage.getItem(SESSION_KEY)).toBeNull()
    expect(screen.getByTestId('error')).not.toHaveTextContent('none')
  })

  it('owner has manage_organization; a fresh no-session probe does not', async () => {
    window.localStorage.setItem(SESSION_KEY, 'alice')
    vi.mocked(api.getUserMe).mockResolvedValue(ME)
    vi.mocked(api.getOrganization).mockResolvedValue(ORG)

    render(<SessionProvider><Probe /></SessionProvider>)

    await waitFor(() => expect(screen.getByTestId('can-manage-org')).toHaveTextContent('true'))
  })

  it('can() is always false with no current user', async () => {
    render(<SessionProvider><Probe /></SessionProvider>)

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'))
    expect(screen.getByTestId('can-manage-org')).toHaveTextContent('false')
  })
})
