import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import WorkItemTable from '../components/mission-control/WorkItemTable'
import { SessionProvider } from '../session'
import * as api from '../api'
import type { WorkItem } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    claimWorkItem: vi.fn(),
    releaseWorkItem: vi.fn(),
    completeWorkItem: vi.fn(),
    updateWorkItemStatus: vi.fn(),
  }
})

function makeItem(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    id: 'w1', title: 'Task', description: null, owner_user_id: null, owner_type: 'human', branch: null,
    status: 'open', estimated_files: [], priority: 'medium', org_id: null, is_draft: false,
    created_at: '2026-07-28T00:00:00+00:00', updated_at: '2026-07-28T00:00:00+00:00',
    ...overrides,
  }
}

// WorkItemTable reads useSession() (to default the claim prompt to the
// signed-in user) -- every render needs a SessionProvider ancestor. No
// localStorage user id is ever set in these tests, so the session
// resolves to "no current user" without any network call.
function renderTable(props: Parameters<typeof WorkItemTable>[0]) {
  return render(<SessionProvider><WorkItemTable {...props} /></SessionProvider>)
}

describe('WorkItemTable', () => {
  it('shows the empty message when there are no items', () => {
    renderTable({ items: [], onRefetch: vi.fn(), emptyMessage: 'Nothing here.' })
    expect(screen.getByText('Nothing here.')).toBeInTheDocument()
  })

  it('shows a Claim button only for unclaimed items', () => {
    renderTable({ items: [makeItem({ status: 'open' })], onRefetch: vi.fn() })
    expect(screen.getByText('Claim')).toBeInTheDocument()
  })

  it('shows an advance button matching the current lifecycle stage', () => {
    renderTable({ items: [makeItem({ status: 'in_progress', owner_user_id: 'alice' })], onRefetch: vi.fn() })
    expect(screen.getByText('Mark testing')).toBeInTheDocument()
  })

  it('advancing calls updateWorkItemStatus with the next stage', async () => {
    const onRefetch = vi.fn()
    renderTable({ items: [makeItem({ status: 'claimed', owner_user_id: 'alice' })], onRefetch })

    fireEvent.click(screen.getByText('Start'))

    await waitFor(() => expect(api.updateWorkItemStatus).toHaveBeenCalledWith('w1', 'in_progress'))
    await waitFor(() => expect(onRefetch).toHaveBeenCalled())
  })

  it('completed items show no action buttons', () => {
    renderTable({ items: [makeItem({ status: 'completed', owner_user_id: 'alice' })], onRefetch: vi.fn() })
    expect(screen.queryByText('Claim')).not.toBeInTheDocument()
    expect(screen.queryByText('Release')).not.toBeInTheDocument()
    expect(screen.queryByText('Complete')).not.toBeInTheDocument()
  })

  it('shows an AI badge for owner_type=ai items', () => {
    renderTable({ items: [makeItem({ owner_type: 'ai' })], onRefetch: vi.fn() })
    expect(screen.getByText('AI')).toBeInTheDocument()
  })

  it('surfaces a failed action instead of silently doing nothing', async () => {
    // Regression test (Stabilization Mode, 2026-07-28): every action
    // handler here used to `await` its API call with no try/catch and no
    // error boundary exists in this app -- a rejected call (e.g. a claim
    // that loses a real race against another claimant, now that
    // collaboration/store.py's claim is atomic) previously vanished with
    // no feedback. `runAction` now catches and displays it.
    vi.mocked(api.updateWorkItemStatus).mockRejectedValue(new api.ApiRequestError(400, 'already claimed by bob'))
    const onRefetch = vi.fn()
    renderTable({ items: [makeItem({ status: 'claimed', owner_user_id: 'alice' })], onRefetch })

    fireEvent.click(screen.getByText('Start'))

    expect(await screen.findByRole('alert')).toHaveTextContent('already claimed by bob')
    await waitFor(() => expect(onRefetch).toHaveBeenCalled())
  })
})
