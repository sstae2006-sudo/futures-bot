import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import WorkItemTable from '../components/mission-control/WorkItemTable'
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
    status: 'open', estimated_files: [], priority: 'medium',
    created_at: '2026-07-28T00:00:00+00:00', updated_at: '2026-07-28T00:00:00+00:00',
    ...overrides,
  }
}

describe('WorkItemTable', () => {
  it('shows the empty message when there are no items', () => {
    render(<WorkItemTable items={[]} onRefetch={vi.fn()} emptyMessage="Nothing here." />)
    expect(screen.getByText('Nothing here.')).toBeInTheDocument()
  })

  it('shows a Claim button only for unclaimed items', () => {
    render(<WorkItemTable items={[makeItem({ status: 'open' })]} onRefetch={vi.fn()} />)
    expect(screen.getByText('Claim')).toBeInTheDocument()
  })

  it('shows an advance button matching the current lifecycle stage', () => {
    render(<WorkItemTable items={[makeItem({ status: 'in_progress', owner_user_id: 'alice' })]} onRefetch={vi.fn()} />)
    expect(screen.getByText('Mark testing')).toBeInTheDocument()
  })

  it('advancing calls updateWorkItemStatus with the next stage', async () => {
    const onRefetch = vi.fn()
    render(<WorkItemTable items={[makeItem({ status: 'claimed', owner_user_id: 'alice' })]} onRefetch={onRefetch} />)

    fireEvent.click(screen.getByText('Start'))

    await waitFor(() => expect(api.updateWorkItemStatus).toHaveBeenCalledWith('w1', 'in_progress'))
    await waitFor(() => expect(onRefetch).toHaveBeenCalled())
  })

  it('completed items show no action buttons', () => {
    render(<WorkItemTable items={[makeItem({ status: 'completed', owner_user_id: 'alice' })]} onRefetch={vi.fn()} />)
    expect(screen.queryByText('Claim')).not.toBeInTheDocument()
    expect(screen.queryByText('Release')).not.toBeInTheDocument()
    expect(screen.queryByText('Complete')).not.toBeInTheDocument()
  })

  it('shows an AI badge for owner_type=ai items', () => {
    render(<WorkItemTable items={[makeItem({ owner_type: 'ai' })]} onRefetch={vi.fn()} />)
    expect(screen.getByText('AI')).toBeInTheDocument()
  })
})
