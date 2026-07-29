import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import TeamMembers from '../pages/TeamMembers'
import { SessionProvider } from '../session'
import * as api from '../api'
import type { Organization, User, UserMe } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getUserMe: vi.fn(), getOrganization: vi.fn(), getUsers: vi.fn(), updateUser: vi.fn() }
})

const SESSION_KEY = 'futures-bot:session:user-id'
const ORG: Organization = { id: 'org1', name: 'Acme', created_at: '2026-07-28T00:00:00+00:00' }

function makeMe(role: UserMe['role']): UserMe {
  return {
    id: 'alice', display_name: 'Alice', username: 'alice', email: null, avatar_url: null,
    org_id: 'org1', role, created_at: '2026-07-28T00:00:00+00:00', last_active_at: null,
    timezone: null, preferred_ai_model: null, default_branch_prefix: null, notification_preferences: {},
    api_key: 'fbot_x',
  }
}

function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 'bob', display_name: 'Bob', username: 'bob', email: null, avatar_url: null,
    org_id: 'org1', role: 'member', created_at: '2026-07-28T00:00:00+00:00', last_active_at: null,
    timezone: null, preferred_ai_model: null, default_branch_prefix: null, notification_preferences: {},
    ...overrides,
  }
}

function renderPage() {
  return render(<SessionProvider><TeamMembers /></SessionProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.setItem(SESSION_KEY, 'alice')
  vi.mocked(api.getOrganization).mockResolvedValue(ORG)
})

describe('TeamMembers', () => {
  it('shows members with a role badge for a non-managing viewer', async () => {
    vi.mocked(api.getUserMe).mockResolvedValue(makeMe('viewer'))
    vi.mocked(api.getUsers).mockResolvedValue([makeUser()])

    renderPage()

    await waitFor(() => expect(screen.getByText('Bob')).toBeInTheDocument())
    expect(screen.queryByLabelText('Role for Bob')).not.toBeInTheDocument()
  })

  it('an owner can change another members role', async () => {
    vi.mocked(api.getUserMe).mockResolvedValue(makeMe('owner'))
    vi.mocked(api.getUsers).mockResolvedValue([makeUser()])
    vi.mocked(api.updateUser).mockResolvedValue(makeUser({ role: 'admin' }))

    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Role for Bob')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Role for Bob'), { target: { value: 'admin' } })

    await waitFor(() => expect(api.updateUser).toHaveBeenCalledWith('bob', { role: 'admin' }))
  })

  it('marks the signed-in user as "you"', async () => {
    vi.mocked(api.getUserMe).mockResolvedValue(makeMe('owner'))
    vi.mocked(api.getUsers).mockResolvedValue([makeUser({ id: 'alice', display_name: 'Alice' })])

    renderPage()

    await waitFor(() => expect(screen.getByText('you')).toBeInTheDocument())
  })

  it('an owner cannot edit their own role, even though they have manage_members (self-lockout prevention)', async () => {
    // Regression test for a real incident (2026-07-28): a sole owner
    // demoted themselves to member via this exact dropdown, which
    // removed manage_members on the next session refresh -- hiding the
    // role editor for every row, including their own, with no way back
    // through this page at all (permissions are advisory-only, not
    // enforced server-side, so recovery required a direct API call
    // bypassing the UI). You can no longer edit your own role here --
    // only a teammate can change it for you.
    vi.mocked(api.getUserMe).mockResolvedValue(makeMe('owner'))
    vi.mocked(api.getUsers).mockResolvedValue([
      makeUser({ id: 'alice', display_name: 'Alice', role: 'owner' }),
      makeUser({ id: 'bob', display_name: 'Bob' }),
    ])

    renderPage()

    await waitFor(() => expect(screen.getByLabelText('Role for Bob')).toBeInTheDocument())
    expect(screen.queryByLabelText('Role for Alice')).not.toBeInTheDocument()
  })

  it('shows an empty state with no members', async () => {
    vi.mocked(api.getUserMe).mockResolvedValue(makeMe('owner'))
    vi.mocked(api.getUsers).mockResolvedValue([])

    renderPage()

    await waitFor(() => expect(screen.getByText('No members yet.')).toBeInTheDocument())
  })
})
