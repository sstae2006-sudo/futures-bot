import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import AutomationPanel from '../components/mission-control/AutomationPanel'
import InfrastructurePanel from '../components/mission-control/InfrastructurePanel'
import TeamPanel from '../components/mission-control/TeamPanel'
import { SessionProvider } from '../session'
import * as api from '../api'
import type { AutomationStatus, Infrastructure, SystemHealth, User, WorkItem } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getInfrastructure: vi.fn(),
    getUsers: vi.fn(),
    getSystemHealth: vi.fn(),
    getUserMe: vi.fn(),
    getOrganization: vi.fn(),
    getAutomationStatus: vi.fn(),
    getDraftWorkItems: vi.fn(),
    approveDraftWorkItem: vi.fn(),
    discardDraftWorkItem: vi.fn(),
  }
})

const SESSION_KEY = 'futures-bot:session:user-id'

// TeamPanel reads useSession() (to scope the roster to the signed-in
// user's organization) -- every render needs a SessionProvider ancestor.
// No localStorage user id is set in these tests, so the session resolves
// to "no current user, no organization" without any network call --
// getUsers(undefined) is still exactly what the mock below expects.
function renderTeamPanel() {
  return render(<SessionProvider><TeamPanel /></SessionProvider>)
}

beforeEach(() => {
  window.localStorage.clear()
})

function makeInfrastructure(overrides: Partial<Infrastructure> = {}): Infrastructure {
  return {
    cpu_percent: 12.5,
    memory_used_mb: 8192,
    memory_total_mb: 16384,
    memory_percent: 50.0,
    disk_used_gb: 100,
    disk_total_gb: 500,
    disk_percent: 20.0,
    jobs_queued: 2,
    jobs_running: 1,
    ...overrides,
  }
}

function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 'u1', display_name: 'Seth', username: 'seth', email: null, avatar_url: null,
    org_id: 'org1', role: 'owner', created_at: '2026-07-28T00:00:00+00:00', last_active_at: null,
    timezone: null, preferred_ai_model: null, default_branch_prefix: null, notification_preferences: {},
    ...overrides,
  }
}

function makeHealth(overrides: Partial<SystemHealth> = {}): SystemHealth {
  return {
    status: 'ok', version: '0.7.0', environment: 'development', uptime_seconds: 100,
    database: { configured: false, ok: false, latency_ms: null, error: null },
    last_backup_at: null, connected_users: 3,
    ...overrides,
  }
}

function makeAutomationStatus(overrides: Partial<AutomationStatus> = {}): AutomationStatus {
  return {
    git_watcher: {
      running: false, last_cycle_at: null, last_result: null, last_error: null,
      cycles_completed: 0, drafts_created_count: 0,
    },
    maintenance: {
      running: false, last_cycle_at: null, last_result: null, last_error: null,
      cycles_completed: 0, stale_drafts_discarded_count: 0, last_db_health_ok: null,
    },
    git_sync: {
      running: false, last_cycle_at: null, last_result: null, last_error: null,
      cycles_completed: 0, pulls_applied_count: 0,
    },
    ...overrides,
  }
}

function makeDraft(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    id: 'd1', title: 'Uncommitted changes: src', description: null, owner_user_id: null, owner_type: 'human',
    branch: null, status: 'open', estimated_files: ['src/a.py'], priority: 'low', org_id: null, is_draft: true,
    created_at: '2026-07-29T00:00:00+00:00', updated_at: '2026-07-29T00:00:00+00:00',
    ...overrides,
  }
}

describe('InfrastructurePanel', () => {
  it('shows real CPU/memory/disk/queue metrics once loaded', async () => {
    vi.mocked(api.getInfrastructure).mockResolvedValue(makeInfrastructure())

    render(<InfrastructurePanel />)

    await waitFor(() => expect(screen.getByText(/8\.0 \/ 16\.0 GB/)).toBeInTheDocument())
    expect(screen.getByText(/100 \/ 500 GB/)).toBeInTheDocument()
    expect(screen.getByText('1 running · 2 queued', { exact: false })).toBeInTheDocument()
  })
})

describe('TeamPanel', () => {
  it('lists registered users and the connected-clients count', async () => {
    vi.mocked(api.getUsers).mockResolvedValue([makeUser({ display_name: 'Seth', role: 'owner' })])
    vi.mocked(api.getSystemHealth).mockResolvedValue(makeHealth({ connected_users: 3 }))

    renderTeamPanel()

    await waitFor(() => expect(screen.getByText('Seth')).toBeInTheDocument())
    expect(screen.getByText('owner')).toBeInTheDocument()
    expect(screen.getByText(/3 distinct clients/)).toBeInTheDocument()
  })

  it('shows a graceful empty state with no registered users', async () => {
    vi.mocked(api.getUsers).mockResolvedValue([])
    vi.mocked(api.getSystemHealth).mockResolvedValue(makeHealth())

    renderTeamPanel()

    await waitFor(() => expect(screen.getByText('No users registered yet.')).toBeInTheDocument())
  })

  it('scopes the roster to the signed-in user organization, not every user globally', async () => {
    // Regression test (Stabilization Mode, 2026-07-28): TeamPanel used to
    // call getUsers() with no org filter at all, showing every registered
    // user across every organization once multi-org support existed --
    // this was missed when session.tsx/org-scoping was added elsewhere.
    window.localStorage.setItem(SESSION_KEY, 'alice')
    const me = makeUser({ id: 'alice', display_name: 'Alice', org_id: 'org1' })
    vi.mocked(api.getUserMe).mockResolvedValue({ ...me, api_key: 'fbot_x' })
    vi.mocked(api.getOrganization).mockResolvedValue({ id: 'org1', name: 'Acme', created_at: '' })
    vi.mocked(api.getUsers).mockResolvedValue([me])
    vi.mocked(api.getSystemHealth).mockResolvedValue(makeHealth())

    renderTeamPanel()

    await waitFor(() => expect(api.getUsers).toHaveBeenCalledWith('org1'))
  })

  it('treats a SQLite-style timestamp (space separator, no timezone) as UTC, not local time', async () => {
    // Confirms timeAgo's explicit ISO-8601 normalization (T separator +
    // Z) produces the correct answer for SQLite's raw "YYYY-MM-DD
    // HH:MM:SS" format. Note this specific test does NOT distinguish the
    // fix from the un-normalized `${iso}Z` version it replaced -- checked
    // directly: Node/V8 (this test's own engine) happens to parse
    // "YYYY-MM-DD HH:MM:SSZ" (space, no "T") as UTC leniently either way,
    // so this test alone can't prove a regression here. The fix still
    // matters for spec-compliance/cross-engine portability (ISO 8601
    // requires "T", not a space -- other engines are not guaranteed to be
    // as lenient); this test guards the *intended* behavior going
    // forward, not a reproduced cross-browser failure.
    const fourHoursAgoUtc = new Date(Date.now() - 4 * 60 * 60 * 1000)
    const sqliteStyle = fourHoursAgoUtc.toISOString().slice(0, 19).replace('T', ' ')
    vi.mocked(api.getUsers).mockResolvedValue([makeUser({ last_active_at: sqliteStyle })])
    vi.mocked(api.getSystemHealth).mockResolvedValue(makeHealth())

    renderTeamPanel()

    await waitFor(() => expect(screen.getByText('4h ago')).toBeInTheDocument())
  })
})

describe('AutomationPanel', () => {
  it('shows all three schedulers as not running when automation is disabled', async () => {
    vi.mocked(api.getAutomationStatus).mockResolvedValue(makeAutomationStatus())
    vi.mocked(api.getDraftWorkItems).mockResolvedValue([])

    render(<AutomationPanel />)

    await waitFor(() => expect(screen.getAllByText('stopped')).toHaveLength(3))
    expect(screen.getByText('No drafts awaiting review.')).toBeInTheDocument()
  })

  it('shows the git-sync scheduler with its last result', async () => {
    vi.mocked(api.getAutomationStatus).mockResolvedValue(makeAutomationStatus({
      git_sync: {
        running: true, last_cycle_at: '2026-07-29T12:00:00+00:00', last_result: 'up to date',
        last_error: null, cycles_completed: 3, pulls_applied_count: 0,
      },
    }))
    vi.mocked(api.getDraftWorkItems).mockResolvedValue([])

    render(<AutomationPanel />)

    await waitFor(() => expect(screen.getByText('Git-sync (pull-only)')).toBeInTheDocument())
    expect(screen.getByText('up to date')).toBeInTheDocument()
    expect(screen.getByText('3 cycles completed', { exact: false })).toBeInTheDocument()
  })

  it('shows a running scheduler with its last result', async () => {
    vi.mocked(api.getAutomationStatus).mockResolvedValue(makeAutomationStatus({
      git_watcher: {
        running: true, last_cycle_at: '2026-07-29T12:00:00+00:00', last_result: 'no uncovered changes',
        last_error: null, cycles_completed: 5, drafts_created_count: 1,
      },
    }))
    vi.mocked(api.getDraftWorkItems).mockResolvedValue([])

    render(<AutomationPanel />)

    await waitFor(() => expect(screen.getByText('no uncovered changes')).toBeInTheDocument())
    expect(screen.getByText('5 cycles completed', { exact: false })).toBeInTheDocument()
  })

  it('lists draft work items with Approve/Discard actions', async () => {
    vi.mocked(api.getAutomationStatus).mockResolvedValue(makeAutomationStatus())
    vi.mocked(api.getDraftWorkItems).mockResolvedValue([makeDraft({ title: 'Uncommitted changes: src' })])

    render(<AutomationPanel />)

    await waitFor(() => expect(screen.getByText('Uncommitted changes: src')).toBeInTheDocument())
    expect(screen.getByText('Approve')).toBeInTheDocument()
    expect(screen.getByText('Discard')).toBeInTheDocument()
  })

  it('approving a draft calls the API and refetches the list', async () => {
    vi.mocked(api.getAutomationStatus).mockResolvedValue(makeAutomationStatus())
    vi.mocked(api.getDraftWorkItems).mockResolvedValue([makeDraft()])
    vi.mocked(api.approveDraftWorkItem).mockResolvedValue(makeDraft({ is_draft: false }))

    render(<AutomationPanel />)
    await waitFor(() => expect(screen.getByText('Approve')).toBeInTheDocument())
    // Mocks aren't cleared between `it()` blocks in this file, so an exact
    // call-count assertion would be fragile (it'd depend on test order) --
    // capture the baseline right before clicking and assert it increased.
    const callsBeforeClick = vi.mocked(api.getDraftWorkItems).mock.calls.length
    screen.getByText('Approve').click()

    await waitFor(() => expect(api.approveDraftWorkItem).toHaveBeenCalledWith('d1'))
    await waitFor(() => expect(vi.mocked(api.getDraftWorkItems).mock.calls.length).toBeGreaterThan(callsBeforeClick))
  })

  it('discarding a draft calls the API and refetches the list', async () => {
    vi.mocked(api.getAutomationStatus).mockResolvedValue(makeAutomationStatus())
    vi.mocked(api.getDraftWorkItems).mockResolvedValue([makeDraft()])
    vi.mocked(api.discardDraftWorkItem).mockResolvedValue({ discarded: true })

    render(<AutomationPanel />)
    await waitFor(() => expect(screen.getByText('Discard')).toBeInTheDocument())
    const callsBeforeClick = vi.mocked(api.getDraftWorkItems).mock.calls.length
    screen.getByText('Discard').click()

    await waitFor(() => expect(api.discardDraftWorkItem).toHaveBeenCalledWith('d1'))
    await waitFor(() => expect(vi.mocked(api.getDraftWorkItems).mock.calls.length).toBeGreaterThan(callsBeforeClick))
  })
})
