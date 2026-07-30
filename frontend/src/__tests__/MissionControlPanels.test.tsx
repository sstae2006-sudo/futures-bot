import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import ActivityFeed from '../components/mission-control/ActivityFeed'
import AlertCenter from '../components/mission-control/AlertCenter'
import AutomationPanel from '../components/mission-control/AutomationPanel'
import HealthGrid from '../components/mission-control/HealthGrid'
import InfrastructurePanel from '../components/mission-control/InfrastructurePanel'
import IntegrationQueuePanel from '../components/mission-control/IntegrationQueuePanel'
import { DatabaseSummaryCard, ResearchSummaryCard } from '../components/mission-control/SummaryCards'
import StatusBar from '../components/mission-control/StatusBar'
import TeamPanel from '../components/mission-control/TeamPanel'
import WorkforcePanel from '../components/mission-control/WorkforcePanel'
import { MemoryRouter } from 'react-router-dom'
import { SessionProvider } from '../session'
import * as api from '../api'
import type {
  AutomationStatus, BranchInfo, Infrastructure, IntegrationQueueEntry, IntegrationReview, LiveSessionStatus,
  MarketDataOverview, ResearchServerStatus, SystemHealth, SystemOverview, TimelineEntry, User, Worker, WorkItem,
} from '../types'

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
    getWorkers: vi.fn(),
    getIntegrationQueue: vi.fn(),
    getIntegrationReviews: vi.fn(),
    generateIntegrationReview: vi.fn(),
    getSystemOverview: vi.fn(),
    getMarketDataOverview: vi.fn(),
    listExperiments: vi.fn(),
    getLiveStatus: vi.fn(),
    getResearchServerStatus: vi.fn(),
    getTimeline: vi.fn(),
    getBranchInfo: vi.fn(),
  }
})

function renderWithProviders(node: ReactElement) {
  return render(<MemoryRouter><SessionProvider>{node}</SessionProvider></MemoryRouter>)
}

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

function makeSystemOverview(overrides: Partial<SystemOverview> = {}): SystemOverview {
  return {
    version: '0.7.0', strategies_available: ['ema_crossover', 'vwap_reversion'],
    total_backtests: 2, total_optimizer_runs: 0, total_trades_analyzed: 15, total_reports_generated: 0,
    last_optimization_run: null, last_report_generated: null, database_path: 'research.db', database_status: 'ok',
    avg_profit_factor: '1.85', avg_expectancy: '42.50', best_strategy: 'vwap_reversion',
    latest_backtest_strategy: 'vwap_reversion', latest_backtest_net_pnl: '640.00',
    latest_backtest_completed_at: '2026-07-30 01:00:00',
    ...overrides,
  }
}

function makeMarketDataOverview(overrides: Partial<MarketDataOverview> = {}): MarketDataOverview {
  return {
    total_bars: 4_800_000,
    products: [{ product_code: 'MES', contracts_stored: ['MESZ25'], bars_stored: 4_800_000, earliest: null, latest: null, open_gaps: 2 }],
    total_open_gaps: 2, database_path: 'market_data.db', database_size_bytes: 1_200_000_000,
    last_sync_at: '2026-07-29 18:00:00', last_sync_status: 'ok', recent_rolls: [], scheduler_running: true,
    ...overrides,
  }
}

function makeLiveStatus(overrides: Partial<LiveSessionStatus> = {}): LiveSessionStatus {
  return {
    status: 'stopped', run_id: null, strategy: null, contract: null, broker: null, live_symbol: null,
    resolution: null, poll_seconds: null, position: null, session_pnl: null, trade_count_today: null,
    halted: false, halt_reason: null, last_bar_time: null, last_bar_close: null, last_feed_error: null,
    error_message: null, started_at: null, stopped_at: null, warnings: [],
    ...overrides,
  }
}

function makeResearchServerStatus(overrides: Partial<ResearchServerStatus> = {}): ResearchServerStatus {
  return {
    running: false, started_at: null, uptime_seconds: null,
    data_scheduler: { running: false, targets: [], last_cycle_at: null, last_result: null, last_error: null, cycles_completed: 0 },
    paper_trader: { running: false, live_symbol: null, last_feed_error: null, strategies: {} },
    nightly_jobs: { running: false, last_run_date: null, last_run_summary: null, last_error: null },
    ...overrides,
  }
}

function makeTimelineEntry(overrides: Partial<TimelineEntry> = {}): TimelineEntry {
  return {
    kind: 'work_item', timestamp: '2026-07-30 01:00:00', title: 'created', detail: 'Task created',
    actor: 'alice', work_item_id: 'wi1',
    ...overrides,
  }
}

function makeBranchInfo(overrides: Partial<BranchInfo> = {}): BranchInfo {
  return {
    branch: 'main', is_detached: false, base_branch: null, branch_age_days: null, ahead: null, behind: null,
    last_commit: { hash: 'b676726abcdef', short_hash: 'b676726', subject: 'Fix', author: 'dev', authored_at: null },
    notes: [],
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

function makeWorker(overrides: Partial<Worker> = {}): Worker {
  return {
    id: 'w1', worker_type: 'claude_code_session', display_name: 'Claude Session 1', user_id: null, org_id: null,
    status: 'online', current_work_item_id: null, subsystem: null, capabilities: [],
    last_heartbeat_at: '2026-07-29T00:00:00+00:00', created_at: '2026-07-29T00:00:00+00:00',
    updated_at: '2026-07-29T00:00:00+00:00', is_stale: false, seconds_since_heartbeat: 5,
    ...overrides,
  }
}

function makeIntegrationQueueEntry(overrides: Partial<IntegrationQueueEntry> = {}): IntegrationQueueEntry {
  return {
    work_item: {
      id: 'w1', title: 'Ready item', description: null, owner_user_id: null, owner_type: 'human', branch: null,
      status: 'ready_for_review', estimated_files: ['a.py'], priority: 'medium', org_id: null, is_draft: false,
      created_at: '2026-07-29T00:00:00+00:00', updated_at: '2026-07-29T00:00:00+00:00',
    },
    merge_readiness: {
      score: 90, level: 'ready', test_status: 'unknown',
      factors: [{ name: 'overlap', penalty: 0, explanation: 'No conflicts.' }],
      branch_info: { branch: 'main', is_detached: false, base_branch: null, branch_age_days: null, ahead: null, behind: null, last_commit: null, notes: [] },
      overlap_warnings: [],
    },
    readiness_note: null,
    ...overrides,
  }
}

function makeIntegrationReview(overrides: Partial<IntegrationReview> = {}): IntegrationReview {
  return {
    id: 'rev1', work_item_id: 'w1', worker_id: null, branch: 'main', status_at_review: 'ready_for_review',
    confidence_score: 90, risk_level: 'no_risk', level: 'ready', related_work_item_ids: [],
    affected_subsystems: ['Risk Management'],
    conflict_resolutions: [],
    validation_recommendation: { recommended_tests: ['tests/test_risk.py'], unmapped_files: [], recommend_full_suite: false, frontend_validation_recommended: false },
    readiness_note: null, summary: "'Ready item' is currently ready_for_review with a merge-readiness score of 90/100 (ready).",
    recommendation: 'Recommend proceeding to integration -- no significant blockers detected.',
    created_at: '2026-07-29T00:00:00+00:00',
    ...overrides,
  }
}

describe('WorkforcePanel', () => {
  it('lists workers with their status and capabilities', async () => {
    vi.mocked(api.getWorkers).mockResolvedValue([makeWorker({ capabilities: ['backend', 'testing'] })])

    render(<WorkforcePanel />)

    await waitFor(() => expect(screen.getByText('Claude Session 1')).toBeInTheDocument())
    expect(screen.getByText('online')).toBeInTheDocument()
    expect(screen.getByText('backend')).toBeInTheDocument()
    expect(screen.getByText('testing')).toBeInTheDocument()
  })

  it('shows a graceful empty state with no workers', async () => {
    vi.mocked(api.getWorkers).mockResolvedValue([])

    render(<WorkforcePanel />)

    await waitFor(() => expect(screen.getByText('No workers have reported in yet.')).toBeInTheDocument())
  })

  it('shows "stale" instead of the raw status for a stale worker', async () => {
    vi.mocked(api.getWorkers).mockResolvedValue([makeWorker({ status: 'online', is_stale: true })])

    render(<WorkforcePanel />)

    await waitFor(() => expect(screen.getByText('stale')).toBeInTheDocument())
    expect(screen.queryByText('online')).not.toBeInTheDocument()
  })
})

describe('IntegrationQueuePanel', () => {
  it('shows a graceful empty state with nothing queued', async () => {
    vi.mocked(api.getIntegrationQueue).mockResolvedValue([])

    render(<IntegrationQueuePanel />)

    await waitFor(() => expect(screen.getByText('Nothing in testing or ready for review.')).toBeInTheDocument())
  })

  it('shows each entry with its score', async () => {
    vi.mocked(api.getIntegrationQueue).mockResolvedValue([makeIntegrationQueueEntry()])

    render(<IntegrationQueuePanel />)

    await waitFor(() => expect(screen.getByText('Ready item')).toBeInTheDocument())
    expect(screen.getByText('90/100')).toBeInTheDocument()
  })

  it('shows the readiness_note when estimated_files was used as a proxy', async () => {
    vi.mocked(api.getIntegrationQueue).mockResolvedValue([
      makeIntegrationQueueEntry({ readiness_note: 'Score computed from estimated_files, not a real git diff.' }),
    ])

    render(<IntegrationQueuePanel />)

    await waitFor(() => expect(screen.getByText(/Score computed from estimated_files/)).toBeInTheDocument())
  })

  it('expands to show the merge-readiness factor breakdown on click', async () => {
    vi.mocked(api.getIntegrationQueue).mockResolvedValue([makeIntegrationQueueEntry()])
    vi.mocked(api.getIntegrationReviews).mockResolvedValue([])

    render(<IntegrationQueuePanel />)
    await waitFor(() => expect(screen.getByText('Ready item')).toBeInTheDocument())
    expect(screen.queryByText(/No conflicts\./)).not.toBeInTheDocument()

    screen.getByText('Ready item').click()

    await waitFor(() => expect(screen.getByText(/No conflicts\./)).toBeInTheDocument())
  })

  it('expanding loads and shows no-reviews-yet when none exist', async () => {
    vi.mocked(api.getIntegrationQueue).mockResolvedValue([makeIntegrationQueueEntry()])
    vi.mocked(api.getIntegrationReviews).mockResolvedValue([])

    render(<IntegrationQueuePanel />)
    await waitFor(() => expect(screen.getByText('Ready item')).toBeInTheDocument())
    screen.getByText('Ready item').click()

    await waitFor(() => expect(screen.getByText('No Integration Reviews generated yet.')).toBeInTheDocument())
    expect(screen.getByText('Generate Integration Review')).toBeInTheDocument()
  })

  it('expanding shows the latest review\'s summary, recommendation, and affected subsystems', async () => {
    vi.mocked(api.getIntegrationQueue).mockResolvedValue([makeIntegrationQueueEntry()])
    vi.mocked(api.getIntegrationReviews).mockResolvedValue([makeIntegrationReview()])

    render(<IntegrationQueuePanel />)
    await waitFor(() => expect(screen.getByText('Ready item')).toBeInTheDocument())
    screen.getByText('Ready item').click()

    await waitFor(() => expect(screen.getByText(/Recommend proceeding to integration/)).toBeInTheDocument())
    expect(screen.getByText(/Risk Management/)).toBeInTheDocument()
    expect(screen.getByText(/tests\/test_risk\.py/)).toBeInTheDocument()
  })

  it('shows a suggested resolution for each conflict resolution', async () => {
    vi.mocked(api.getIntegrationQueue).mockResolvedValue([makeIntegrationQueueEntry()])
    vi.mocked(api.getIntegrationReviews).mockResolvedValue([
      makeIntegrationReview({
        conflict_resolutions: [{
          work_item_id: 'w2', title: 'Other work', risk: 'high', confidence: 60,
          reason: '1 file(s) shared.', architecture_components_affected: ['Risk Management'],
          suggested_resolution: 'High conflict risk -- recommend integrating first.',
        }],
      }),
    ])

    render(<IntegrationQueuePanel />)
    await waitFor(() => expect(screen.getByText('Ready item')).toBeInTheDocument())
    screen.getByText('Ready item').click()

    await waitFor(() => expect(screen.getByText(/recommend integrating first/)).toBeInTheDocument())
    expect(screen.getByText('high')).toBeInTheDocument()
  })

  it('shows the confidence trend when more than one review exists', async () => {
    vi.mocked(api.getIntegrationQueue).mockResolvedValue([makeIntegrationQueueEntry()])
    vi.mocked(api.getIntegrationReviews).mockResolvedValue([
      makeIntegrationReview({ id: 'rev2', confidence_score: 90 }),
      makeIntegrationReview({ id: 'rev1', confidence_score: 50 }),
    ])

    render(<IntegrationQueuePanel />)
    await waitFor(() => expect(screen.getByText('Ready item')).toBeInTheDocument())
    screen.getByText('Ready item').click()

    await waitFor(() => expect(screen.getByText(/Confidence trend/)).toBeInTheDocument())
    expect(screen.getByText(/50 → 90/)).toBeInTheDocument()
  })

  it('clicking "Generate Integration Review" calls the API and refreshes history', async () => {
    vi.mocked(api.getIntegrationQueue).mockResolvedValue([makeIntegrationQueueEntry()])
    vi.mocked(api.getIntegrationReviews).mockResolvedValue([])
    vi.mocked(api.generateIntegrationReview).mockResolvedValue(makeIntegrationReview())

    render(<IntegrationQueuePanel />)
    await waitFor(() => expect(screen.getByText('Ready item')).toBeInTheDocument())
    screen.getByText('Ready item').click()
    await waitFor(() => expect(screen.getByText('Generate Integration Review')).toBeInTheDocument())

    vi.mocked(api.getIntegrationReviews).mockResolvedValue([makeIntegrationReview()])
    screen.getByText('Generate Integration Review').click()

    await waitFor(() => expect(api.generateIntegrationReview).toHaveBeenCalledWith('w1'))
    await waitFor(() => expect(screen.getByText(/Recommend proceeding to integration/)).toBeInTheDocument())
  })
})

// KNOWN_ISSUES.md ISSUE-040 -- every describe block below covers a
// Mission Control component that used to render a hardcoded placeholder
// from missionControlData.ts. Each test asserts the component renders
// the REAL mocked API response, and none of the old fake literals
// (1.42, 187, 18.6, "280d52b", "17m ago") appear anywhere.
describe('ResearchSummaryCard', () => {
  it('renders real aggregate values from the system overview', async () => {
    vi.mocked(api.getSystemOverview).mockResolvedValue(makeSystemOverview())
    vi.mocked(api.listExperiments).mockResolvedValue([])

    renderWithProviders(<ResearchSummaryCard />)

    await waitFor(() => expect(screen.getByText('vwap_reversion', { selector: '.v' })).toBeInTheDocument())
    expect(screen.getByText('1.85')).toBeInTheDocument()
    expect(screen.getByText('$42.50')).toBeInTheDocument()
    expect(screen.queryByText('1.42')).not.toBeInTheDocument()
    expect(screen.queryByText('187')).not.toBeInTheDocument()
  })

  it('shows a dash, never a fabricated number, when there are no completed backtests', async () => {
    vi.mocked(api.getSystemOverview).mockResolvedValue(makeSystemOverview({
      total_backtests: 0, avg_profit_factor: null, avg_expectancy: null, best_strategy: null,
      latest_backtest_strategy: null, latest_backtest_net_pnl: null, latest_backtest_completed_at: null,
    }))
    vi.mocked(api.listExperiments).mockResolvedValue([])

    renderWithProviders(<ResearchSummaryCard />)

    await waitFor(() => expect(screen.getAllByText('—').length).toBeGreaterThan(0))
  })
})

describe('DatabaseSummaryCard', () => {
  it('renders real values from the market data overview', async () => {
    vi.mocked(api.getMarketDataOverview).mockResolvedValue(makeMarketDataOverview())

    renderWithProviders(<DatabaseSummaryCard />)

    await waitFor(() => expect(screen.getByText('4,800,000')).toBeInTheDocument())
    expect(screen.getByText('1.20 GB')).toBeInTheDocument()
    expect(screen.getByText('Running')).toBeInTheDocument()
  })
})

describe('HealthGrid', () => {
  beforeEach(() => {
    vi.mocked(api.getLiveStatus).mockResolvedValue(makeLiveStatus())
    vi.mocked(api.getResearchServerStatus).mockResolvedValue(makeResearchServerStatus())
    vi.mocked(api.getSystemOverview).mockResolvedValue(makeSystemOverview())
  })

  it('shows the backend as operational once real health data loads', async () => {
    vi.mocked(api.getSystemHealth).mockResolvedValue(makeHealth())

    renderWithProviders(<HealthGrid />)

    await waitFor(() => expect(screen.getByText('Backend')).toBeInTheDocument())
    expect(screen.getByText('OPERATIONAL')).toBeInTheDocument()
    // The old fake grid always showed exactly these 10 static names --
    // AI Services had no real data source and was dropped, not faked.
    expect(screen.queryByText('AI Services')).not.toBeInTheDocument()
  })

  it('shows the real team database card only when team mode is configured', async () => {
    vi.mocked(api.getSystemHealth).mockResolvedValue(
      makeHealth({ database: { configured: true, ok: true, latency_ms: 12.5, error: null } }),
    )

    renderWithProviders(<HealthGrid />)

    await waitFor(() => expect(screen.getByText('Team Database (TimescaleDB)')).toBeInTheDocument())
  })
})

describe('ActivityFeed', () => {
  it('renders real timeline entries', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue([makeTimelineEntry({ title: 'claimed', detail: 'Real work item event' })])

    renderWithProviders(<ActivityFeed />)

    await waitFor(() => expect(screen.getByText('claimed')).toBeInTheDocument())
  })

  it('shows a graceful empty state, not fake boot-sequence entries', async () => {
    vi.mocked(api.getTimeline).mockResolvedValue([])

    renderWithProviders(<ActivityFeed />)

    await waitFor(() => expect(screen.getByText('No recent activity.')).toBeInTheDocument())
    expect(screen.queryByText(/futures-bot startup/)).not.toBeInTheDocument()
  })
})

describe('AlertCenter', () => {
  it('shows nothing to report when every scheduler is clean', async () => {
    vi.mocked(api.getAutomationStatus).mockResolvedValue(makeAutomationStatus())
    vi.mocked(api.getSystemHealth).mockResolvedValue(makeHealth())

    renderWithProviders(<AlertCenter />)

    await waitFor(() => expect(screen.getAllByText('Nothing to report.').length).toBe(3))
  })

  it('surfaces a real scheduler error as a critical alert', async () => {
    vi.mocked(api.getAutomationStatus).mockResolvedValue(makeAutomationStatus({
      git_watcher: {
        running: true, last_cycle_at: '2026-07-30 01:00:00', last_result: null,
        last_error: 'permission denied', cycles_completed: 3, drafts_created_count: 0,
      },
    }))
    vi.mocked(api.getSystemHealth).mockResolvedValue(makeHealth())

    renderWithProviders(<AlertCenter />)

    await waitFor(() => expect(screen.getByText(/Git watcher: permission denied/)).toBeInTheDocument())
  })

  it('surfaces an unreachable team database as a critical alert', async () => {
    vi.mocked(api.getAutomationStatus).mockResolvedValue(makeAutomationStatus())
    vi.mocked(api.getSystemHealth).mockResolvedValue(
      makeHealth({ database: { configured: true, ok: false, latency_ms: null, error: 'connection refused' } }),
    )

    renderWithProviders(<AlertCenter />)

    await waitFor(() => expect(screen.getByText(/Team database \(TimescaleDB\) unreachable/)).toBeInTheDocument())
  })
})

describe('StatusBar', () => {
  it('renders the real current branch and commit, not a frozen placeholder', async () => {
    vi.mocked(api.getSystemHealth).mockResolvedValue(makeHealth())
    vi.mocked(api.getBranchInfo).mockResolvedValue(makeBranchInfo({ branch: 'feature/audit-fix' }))

    renderWithProviders(<StatusBar />)

    await waitFor(() => expect(screen.getByText('feature/audit-fix')).toBeInTheDocument())
    expect(screen.getByText('b676726')).toBeInTheDocument()
    expect(screen.queryByText('280d52b')).not.toBeInTheDocument()
    expect(screen.queryByText('17m ago')).not.toBeInTheDocument()
  })
})
