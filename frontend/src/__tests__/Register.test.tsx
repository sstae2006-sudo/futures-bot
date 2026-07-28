import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Register from '../pages/Register'
import { SessionProvider } from '../session'
import * as api from '../api'
import type { Organization, UserMe } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getOrganizations: vi.fn(),
    createOrganization: vi.fn(),
    createUser: vi.fn(),
  }
})

function renderRegister() {
  return render(
    <SessionProvider>
      <MemoryRouter initialEntries={['/register']}>
        <Register />
      </MemoryRouter>
    </SessionProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getOrganizations).mockResolvedValue([])
})

describe('Register', () => {
  it('creating a new org registers the user as owner', async () => {
    const org: Organization = { id: 'org1', name: 'Acme', created_at: '2026-07-28T00:00:00+00:00' }
    vi.mocked(api.createOrganization).mockResolvedValue(org)
    const created: UserMe = {
      id: 'u1', display_name: 'Seth', username: 'seth', email: null, avatar_url: null,
      org_id: 'org1', role: 'owner', created_at: '2026-07-28T00:00:00+00:00', last_active_at: null,
      timezone: null, preferred_ai_model: null, default_branch_prefix: null, notification_preferences: {},
      api_key: 'fbot_supersecret',
    }
    vi.mocked(api.createUser).mockResolvedValue(created)

    renderRegister()

    fireEvent.change(screen.getByLabelText('Organization name'), { target: { value: 'Acme' } })
    fireEvent.click(screen.getByText('Next'))

    await waitFor(() => expect(screen.getByLabelText('Display Name')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Display Name'), { target: { value: 'Seth' } })
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'seth' } })
    fireEvent.click(screen.getByText('Create Account'))

    await waitFor(() => expect(api.createOrganization).toHaveBeenCalledWith('Acme'))
    expect(api.createUser).toHaveBeenCalledWith(expect.objectContaining({
      display_name: 'Seth', username: 'seth', org_id: 'org1', role: 'owner',
    }))

    await waitFor(() => expect(screen.getByText('fbot_supersecret')).toBeInTheDocument())
  })

  it('joining an existing org registers the user as member', async () => {
    const existingOrg: Organization = { id: 'org2', name: 'Widgets', created_at: '2026-07-28T00:00:00+00:00' }
    vi.mocked(api.getOrganizations).mockResolvedValue([existingOrg])
    const created: UserMe = {
      id: 'u2', display_name: 'Bob', username: 'bob', email: null, avatar_url: null,
      org_id: 'org2', role: 'member', created_at: '2026-07-28T00:00:00+00:00', last_active_at: null,
      timezone: null, preferred_ai_model: null, default_branch_prefix: null, notification_preferences: {},
      api_key: 'fbot_bobkey',
    }
    vi.mocked(api.createUser).mockResolvedValue(created)

    renderRegister()

    fireEvent.click(screen.getByText('Join an existing organization'))
    await waitFor(() => expect(screen.getByLabelText('Select organization to join')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Select organization to join'), { target: { value: 'org2' } })
    fireEvent.click(screen.getByText('Next'))

    await waitFor(() => expect(screen.getByLabelText('Display Name')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Display Name'), { target: { value: 'Bob' } })
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'bob' } })
    fireEvent.click(screen.getByText('Create Account'))

    await waitFor(() => expect(api.createUser).toHaveBeenCalledWith(expect.objectContaining({
      org_id: 'org2', role: 'member',
    })))
    expect(api.createOrganization).not.toHaveBeenCalled()
  })

  it('requires a display name and username before submitting', async () => {
    vi.mocked(api.createOrganization).mockResolvedValue({ id: 'org1', name: 'Acme', created_at: '' })

    renderRegister()

    fireEvent.change(screen.getByLabelText('Organization name'), { target: { value: 'Acme' } })
    fireEvent.click(screen.getByText('Next'))

    await waitFor(() => expect(screen.getByLabelText('Display Name')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Create Account'))

    expect(await screen.findByRole('alert')).toHaveTextContent(/required/i)
    expect(api.createUser).not.toHaveBeenCalled()
  })
})
