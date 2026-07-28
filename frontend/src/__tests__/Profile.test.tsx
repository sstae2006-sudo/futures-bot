import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import Profile from '../pages/Profile'
import { SessionProvider } from '../session'
import * as api from '../api'
import type { UserMe } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getUserMe: vi.fn(), getOrganization: vi.fn(), updateUser: vi.fn(), regenerateApiKey: vi.fn() }
})

const SESSION_KEY = 'futures-bot:session:user-id'

const ME: UserMe = {
  id: 'alice', display_name: 'Alice', username: 'alice', email: 'alice@example.com', avatar_url: null,
  org_id: 'org1', role: 'owner', created_at: '2026-07-28T00:00:00+00:00', last_active_at: null,
  timezone: null, preferred_ai_model: null, default_branch_prefix: null, notification_preferences: {},
  api_key: 'fbot_original',
}

function renderProfile() {
  return render(<SessionProvider><Profile /></SessionProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.setItem(SESSION_KEY, 'alice')
  vi.mocked(api.getUserMe).mockResolvedValue(ME)
  vi.mocked(api.getOrganization).mockResolvedValue({ id: 'org1', name: 'Acme', created_at: '' })
})

describe('Profile', () => {
  it('shows the current api key', async () => {
    renderProfile()
    await waitFor(() => expect(screen.getByText('fbot_original')).toBeInTheDocument())
  })

  it('regenerating the api key calls the endpoint and refetches', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(api.regenerateApiKey).mockResolvedValue({ ...ME, api_key: 'fbot_new' })

    renderProfile()
    await waitFor(() => expect(screen.getByText('fbot_original')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Regenerate'))

    await waitFor(() => expect(api.regenerateApiKey).toHaveBeenCalledWith('alice'))
  })

  it('saving preferences calls updateUser with the form values', async () => {
    vi.mocked(api.updateUser).mockResolvedValue(ME)

    renderProfile()
    await waitFor(() => expect(screen.getByLabelText('Time Zone')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Time Zone'), { target: { value: 'America/New_York' } })
    fireEvent.click(screen.getByText('Save Changes'))

    await waitFor(() => expect(api.updateUser).toHaveBeenCalledWith('alice', expect.objectContaining({
      timezone: 'America/New_York',
    })))
  })
})
