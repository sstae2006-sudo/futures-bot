import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Welcome from '../pages/Welcome'
import { SessionProvider } from '../session'
import * as api from '../api'
import type { User } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getUsers: vi.fn() }
})

function renderWelcome() {
  return render(
    <SessionProvider>
      <MemoryRouter initialEntries={['/welcome']}>
        <Welcome />
      </MemoryRouter>
    </SessionProvider>,
  )
}

function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 'u1', display_name: 'Seth', username: 'seth', email: null, avatar_url: null,
    org_id: 'org1', role: 'owner', created_at: '2026-07-28T00:00:00+00:00', last_active_at: null,
    timezone: null, preferred_ai_model: null, default_branch_prefix: null, notification_preferences: {},
    ...overrides,
  }
}

beforeEach(() => {
  window.localStorage.clear()
})

describe('Welcome', () => {
  it('offers to register when there are no existing users', async () => {
    vi.mocked(api.getUsers).mockResolvedValue([])

    renderWelcome()

    await waitFor(() => expect(screen.getByText('Register an Account')).toBeInTheDocument())
    expect(screen.queryByText('I already have an account')).not.toBeInTheDocument()
  })

  it('offers to continue as an existing user when the roster is non-empty', async () => {
    vi.mocked(api.getUsers).mockResolvedValue([makeUser()])

    renderWelcome()

    await waitFor(() => expect(screen.getByText('I already have an account')).toBeInTheDocument())
    fireEvent.click(screen.getByText('I already have an account'))

    expect(screen.getByLabelText('Select your account')).toBeInTheDocument()
  })
})
