import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import CollaborationWorkspace from '../components/mission-control/CollaborationWorkspace'
import { SessionProvider } from '../session'
import * as api from '../api'
import type { ConflictPair, Organization, TimelineEntry, UserMe, WorkItem } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getWorkItems: vi.fn(),
    getTimeline: vi.fn(),
    getWorkItemConflicts: vi.fn(),
    claimWorkItem: vi.fn(),
    releaseWorkItem: vi.fn(),
    completeWorkItem: vi.fn(),
    updateWorkItemStatus: vi.fn(),
    getUserMe: vi.fn(),
    getOrganization: vi.fn(),
  }
})

// Mirrors session.tsx's own (module-private) localStorage key -- there's
// no exported constant to import, so it's duplicated here deliberately
// rather than exporting an internal implementation detail just for tests.
const SESSION_USER_ID_KEY = 'futures-bot:session:user-id'

function makeItem(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    id: 'w1', title: 'Fix login bug', description: null, owner_user_id: null, owner_type: 'human', branch: null,
    status: 'open', estimated_files: ['src/auth.py'], priority: 'medium', org_id: null, is_draft: false,
    created_at: '2026-07-28T00:00:00+00:00', updated_at: '2026-07-28T00:00:00+00:00',
    ...overrides,
  }
}

// CollaborationWorkspace reads useSession() -- every render needs a
// SessionProvider ancestor. No localStorage user id is set by default,
// so the session resolves to "no current user, no organization" without
// any network call; tests that need "signed in as alice" set the id in
// localStorage first and mock getUserMe/getOrganization accordingly.
function renderWorkspace() {
  return render(<SessionProvider><CollaborationWorkspace /></SessionProvider>)
}

beforeEach(() => {
  window.localStorage.clear()
  vi.mocked(api.getTimeline).mockResolvedValue([])
  vi.mocked(api.getWorkItemConflicts).mockResolvedValue([])
})

describe('CollaborationWorkspace', () => {
  it('defaults to the Team Active Work tab showing every non-completed item', async () => {
    vi.mocked(api.getWorkItems).mockResolvedValue([
      makeItem({ id: 'w1', title: 'Team task' }),
      makeItem({ id: 'w2', title: 'Done task', status: 'completed' }),
    ])

    renderWorkspace()

    await waitFor(() => expect(screen.getByText('Team task')).toBeInTheDocument())
    expect(screen.queryByText('Done task')).not.toBeInTheDocument()
  })

  it('AI Workers tab shows only owner_type=ai items', async () => {
    vi.mocked(api.getWorkItems).mockResolvedValue([
      makeItem({ id: 'w1', title: 'Human task', owner_type: 'human' }),
      makeItem({ id: 'w2', title: 'AI task', owner_type: 'ai' }),
    ])

    renderWorkspace()
    await waitFor(() => expect(screen.getByText('Human task')).toBeInTheDocument())

    const { fireEvent } = await import('@testing-library/react')
    fireEvent.click(screen.getByText(/AI Workers/))

    await waitFor(() => expect(screen.getByText('AI task')).toBeInTheDocument())
    expect(screen.queryByText('Human task')).not.toBeInTheDocument()
  })

  it('My Active Work filters by the signed-in user', async () => {
    const org: Organization = { id: 'org1', name: 'Acme', created_at: '2026-07-28T00:00:00+00:00' }
    const me: UserMe = {
      id: 'alice', display_name: 'Alice', username: 'alice', email: null, avatar_url: null,
      org_id: 'org1', role: 'member', created_at: '2026-07-28T00:00:00+00:00', last_active_at: null,
      timezone: null, preferred_ai_model: null, default_branch_prefix: null, notification_preferences: {},
      api_key: 'fbot_test',
    }
    window.localStorage.setItem(SESSION_USER_ID_KEY, 'alice')
    vi.mocked(api.getUserMe).mockResolvedValue(me)
    vi.mocked(api.getOrganization).mockResolvedValue(org)
    vi.mocked(api.getWorkItems).mockResolvedValue([
      makeItem({ id: 'w1', title: 'Mine', owner_user_id: 'alice', status: 'claimed' }),
      makeItem({ id: 'w2', title: 'Not mine', owner_user_id: 'bob', status: 'claimed' }),
    ])

    renderWorkspace()
    await waitFor(() => expect(screen.getByText('Mine')).toBeInTheDocument())

    const { fireEvent } = await import('@testing-library/react')
    fireEvent.click(screen.getByText(/My Active Work/))

    await waitFor(() => expect(screen.getByText('Mine')).toBeInTheDocument())
    expect(screen.queryByText('Not mine')).not.toBeInTheDocument()
  })

  it('Merge Queue shows testing and ready_for_review items only', async () => {
    vi.mocked(api.getWorkItems).mockResolvedValue([
      makeItem({ id: 'w1', title: 'Testing item', status: 'testing', owner_user_id: 'alice' }),
      makeItem({ id: 'w2', title: 'In progress item', status: 'in_progress', owner_user_id: 'alice' }),
    ])

    renderWorkspace()
    await waitFor(() => expect(screen.getByText('Testing item')).toBeInTheDocument())

    const { fireEvent } = await import('@testing-library/react')
    fireEvent.click(screen.getByText(/Merge Queue/))

    await waitFor(() => expect(screen.getByText('Testing item')).toBeInTheDocument())
    expect(screen.queryByText('In progress item')).not.toBeInTheDocument()
  })

  it('Recent Activity renders timeline entries', async () => {
    vi.mocked(api.getWorkItems).mockResolvedValue([])
    const entries: TimelineEntry[] = [
      { kind: 'work_item', timestamp: '2026-07-28 10:00:00', title: 'claimed', detail: null, actor: 'alice', work_item_id: 'w1' },
    ]
    vi.mocked(api.getTimeline).mockResolvedValue(entries)

    renderWorkspace()
    await waitFor(() => expect(screen.getByText('No active work items.')).toBeInTheDocument())

    const { fireEvent } = await import('@testing-library/react')
    fireEvent.click(screen.getByText(/Recent Activity/))

    await waitFor(() => expect(screen.getByText(/alice/)).toBeInTheDocument())
  })

  it('Conflict Warnings renders pairwise conflicts', async () => {
    vi.mocked(api.getWorkItems).mockResolvedValue([])
    const conflicts: ConflictPair[] = [{
      item_a: 'w1', item_a_title: 'Task A', item_b: 'w2', item_b_title: 'Task B',
      risk: 'critical', confidence: 90, factors: { shared_files: 3 }, reason: 'Shares 3 files.',
    }]
    vi.mocked(api.getWorkItemConflicts).mockResolvedValue(conflicts)

    renderWorkspace()
    await waitFor(() => expect(screen.getByText('No active work items.')).toBeInTheDocument())

    const { fireEvent } = await import('@testing-library/react')
    fireEvent.click(screen.getByText(/Conflict Warnings/))

    await waitFor(() => expect(screen.getByText(/Task A/)).toBeInTheDocument())
    expect(screen.getByText(/Task B/)).toBeInTheDocument()
  })

  it('Ready For Review shows only that status', async () => {
    vi.mocked(api.getWorkItems).mockResolvedValue([
      makeItem({ id: 'w1', title: 'Ready item', status: 'ready_for_review', owner_user_id: 'alice' }),
      makeItem({ id: 'w2', title: 'Merged item', status: 'merged', owner_user_id: 'alice' }),
    ])

    renderWorkspace()
    await waitFor(() => expect(screen.getByText('Ready item')).toBeInTheDocument())

    const { fireEvent } = await import('@testing-library/react')
    fireEvent.click(screen.getByText(/Ready For Review/))

    await waitFor(() => expect(screen.getByText('Ready item')).toBeInTheDocument())
    expect(screen.queryByText('Merged item')).not.toBeInTheDocument()
  })
})
