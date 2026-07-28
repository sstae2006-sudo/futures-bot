import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import WorkRegistryPanel from '../components/mission-control/WorkRegistryPanel'
import { SessionProvider } from '../session'
import * as api from '../api'
import type { WorkItem, WorkItemCreated } from '../types'

// WorkRegistryPanel reads useSession() (to scope work items to the
// signed-in user's organization) -- every render needs a SessionProvider
// ancestor. No localStorage user id is set in these tests, so the
// session resolves to "no current user, no organization" without any
// network call -- getWorkItems(undefined, undefined) is still exactly
// what the mock below expects.
function renderPanel() {
  return render(<SessionProvider><WorkRegistryPanel /></SessionProvider>)
}

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getWorkItems: vi.fn(),
    createWorkItem: vi.fn(),
    claimWorkItem: vi.fn(),
    releaseWorkItem: vi.fn(),
    completeWorkItem: vi.fn(),
  }
})

function makeItem(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    id: 'w1', title: 'Fix login bug', description: null, owner_user_id: null, owner_type: 'human', branch: null,
    status: 'open', estimated_files: ['src/auth.py'], priority: 'medium', org_id: null,
    created_at: '2026-07-28T00:00:00+00:00', updated_at: '2026-07-28T00:00:00+00:00',
    ...overrides,
  }
}

describe('WorkRegistryPanel', () => {
  it('lists active work items', async () => {
    vi.mocked(api.getWorkItems).mockResolvedValue([makeItem()])

    renderPanel()

    await waitFor(() => expect(screen.getByText('Fix login bug')).toBeInTheDocument())
    expect(screen.getByText('open')).toBeInTheDocument()
  })

  it('shows a graceful empty state', async () => {
    vi.mocked(api.getWorkItems).mockResolvedValue([])

    renderPanel()

    await waitFor(() => expect(screen.getByText('No active work items.')).toBeInTheDocument())
  })

  it('creates a work item and surfaces overlap warnings', async () => {
    vi.mocked(api.getWorkItems).mockResolvedValue([])
    const created: WorkItemCreated = {
      work_item: makeItem({ id: 'w2', title: 'New task' }),
      overlap_warnings: [{
        work_item_id: 'w1', title: 'Fix login bug', owner_user_id: null,
        overlapping_files: ['src/auth.py'], risk: 'high', reason: '1 file(s) also touched: src/auth.py',
      }],
    }
    vi.mocked(api.createWorkItem).mockResolvedValue(created)

    renderPanel()
    await waitFor(() => expect(screen.getByText('No active work items.')).toBeInTheDocument())

    const titleInput = screen.getByLabelText('Task title')
    titleInput.closest('form')?.querySelector('button[type="submit"]')

    const { fireEvent } = await import('@testing-library/react')
    fireEvent.change(titleInput, { target: { value: 'New task' } })
    fireEvent.submit(titleInput.closest('form')!)

    await waitFor(() => expect(api.createWorkItem).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'New task' }),
    ))
    await waitFor(() => expect(screen.getByText(/1 file\(s\) also touched/)).toBeInTheDocument())
    expect(screen.getByText('high')).toBeInTheDocument()
  })
})
