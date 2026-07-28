import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import OrganizationSettings from '../pages/OrganizationSettings'
import { SessionProvider } from '../session'
import * as api from '../api'
import type { Organization, User, UserMe } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual, getUserMe: vi.fn(), getOrganization: vi.fn(), getUsers: vi.fn(), updateOrganization: vi.fn(),
  }
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

function renderPage() {
  return render(<SessionProvider><OrganizationSettings /></SessionProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.setItem(SESSION_KEY, 'alice')
  vi.mocked(api.getOrganization).mockResolvedValue(ORG)
  vi.mocked(api.getUsers).mockResolvedValue([] as User[])
})

describe('OrganizationSettings', () => {
  it('an owner can rename the organization', async () => {
    vi.mocked(api.getUserMe).mockResolvedValue(makeMe('owner'))
    vi.mocked(api.updateOrganization).mockResolvedValue({ ...ORG, name: 'New Name' })

    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Organization name')).toHaveValue('Acme'))

    fireEvent.change(screen.getByLabelText('Organization name'), { target: { value: 'New Name' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => expect(api.updateOrganization).toHaveBeenCalledWith('org1', 'New Name'))
  })

  it('a viewer cannot rename the organization', async () => {
    vi.mocked(api.getUserMe).mockResolvedValue(makeMe('viewer'))

    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Organization name')).toBeInTheDocument())

    expect(screen.getByLabelText('Organization name')).toBeDisabled()
    expect(screen.queryByText('Save')).not.toBeInTheDocument()
    expect(screen.getByText(/Only an Owner or Admin/)).toBeInTheDocument()
  })
})
