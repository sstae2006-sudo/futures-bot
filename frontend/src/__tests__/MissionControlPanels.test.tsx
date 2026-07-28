import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import InfrastructurePanel from '../components/mission-control/InfrastructurePanel'
import TeamPanel from '../components/mission-control/TeamPanel'
import * as api from '../api'
import type { Infrastructure, SystemHealth, User } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getInfrastructure: vi.fn(),
    getUsers: vi.fn(),
    getSystemHealth: vi.fn(),
  }
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

    render(<TeamPanel />)

    await waitFor(() => expect(screen.getByText('Seth')).toBeInTheDocument())
    expect(screen.getByText('owner')).toBeInTheDocument()
    expect(screen.getByText(/3 distinct clients/)).toBeInTheDocument()
  })

  it('shows a graceful empty state with no registered users', async () => {
    vi.mocked(api.getUsers).mockResolvedValue([])
    vi.mocked(api.getSystemHealth).mockResolvedValue(makeHealth())

    render(<TeamPanel />)

    await waitFor(() => expect(screen.getByText('No users registered yet.')).toBeInTheDocument())
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

    render(<TeamPanel />)

    await waitFor(() => expect(screen.getByText('4h ago')).toBeInTheDocument())
  })
})
