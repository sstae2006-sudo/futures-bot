import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { SessionProvider, useSession } from '../session'
import * as api from '../api'
import type { Organization, UserMe } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getUserMe: vi.fn(), getOrganization: vi.fn(), sendUserHeartbeat: vi.fn() }
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
  vi.mocked(api.sendUserHeartbeat).mockReset()
  vi.mocked(api.sendUserHeartbeat).mockResolvedValue({} as never)
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

// KNOWN_ISSUES.md ISSUE-043 -- sendUserHeartbeat (POST /api/users/{id}/heartbeat,
// which keeps last_active_at fresh) existed as a real backend route and a
// real frontend function, but SessionProvider never called it, so every
// signed-in user showed "offline" (TeamPanel.tsx's isOnline() 2-minute
// window) within minutes of signing in, including the person actively
// using the app. These tests pin the fix: a heartbeat fires immediately
// on sign-in and on a recurring interval, and stops when there's no
// signed-in user.
describe('SessionProvider heartbeat', () => {
  beforeEach(() => {
    // shouldAdvanceTime: real async work (promises, testing-library's
    // waitFor polling) keeps flowing normally; only setInterval/setTimeout
    // itself is under fake-timer control, advanced explicitly below.
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('sends a heartbeat immediately once a user resolves', async () => {
    window.localStorage.setItem(SESSION_KEY, 'alice')
    vi.mocked(api.getUserMe).mockResolvedValue(ME)
    vi.mocked(api.getOrganization).mockResolvedValue(ORG)

    render(<SessionProvider><Probe /></SessionProvider>)

    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('Alice'))
    expect(api.sendUserHeartbeat).toHaveBeenCalledWith('alice')
  })

  it('sends a heartbeat again after the interval elapses, without a page reload', async () => {
    window.localStorage.setItem(SESSION_KEY, 'alice')
    vi.mocked(api.getUserMe).mockResolvedValue(ME)
    vi.mocked(api.getOrganization).mockResolvedValue(ORG)

    render(<SessionProvider><Probe /></SessionProvider>)
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('Alice'))

    const callsSoFar = vi.mocked(api.sendUserHeartbeat).mock.calls.length
    await vi.advanceTimersByTimeAsync(60_000)

    expect(vi.mocked(api.sendUserHeartbeat).mock.calls.length).toBeGreaterThan(callsSoFar)
  })

  it('never heartbeats when there is no signed-in user', async () => {
    render(<SessionProvider><Probe /></SessionProvider>)

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'))
    await vi.advanceTimersByTimeAsync(120_000)

    expect(api.sendUserHeartbeat).not.toHaveBeenCalled()
  })

  it('a failed heartbeat never surfaces as a session error', async () => {
    window.localStorage.setItem(SESSION_KEY, 'alice')
    vi.mocked(api.getUserMe).mockResolvedValue(ME)
    vi.mocked(api.getOrganization).mockResolvedValue(ORG)
    vi.mocked(api.sendUserHeartbeat).mockRejectedValue(new Error('network down'))

    render(<SessionProvider><Probe /></SessionProvider>)

    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('Alice'))
    expect(screen.getByTestId('error')).toHaveTextContent('none')
  })
})
