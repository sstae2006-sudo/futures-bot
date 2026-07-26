import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Dashboard from '../pages/Dashboard'
import * as api from '../api'
import { ApiRequestError } from '../api'
import type { RunSummary, SystemOverview } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getSystemOverview: vi.fn(), listBacktests: vi.fn() }
})

const overview: SystemOverview = {
  version: '0.6.0',
  strategies_available: ['ema_crossover', 'vwap_reversion'],
  total_backtests: 3,
  total_optimizer_runs: 1,
  total_trades_analyzed: 12,
  total_reports_generated: 1,
  last_optimization_run: '2026-07-23 04:02:45',
  last_report_generated: '2026-07-23 04:02:46',
  database_path: 'research.db',
  database_status: 'ok',
}

function makeRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: 'run-1', kind: 'backtest', status: 'completed', strategy: 'vwap_reversion', contract: 'MES',
    trade_count: 3, net_pnl: '126.28', profit_factor: '2.1', win_rate: '66.6', sharpe_ratio: '0.5',
    max_drawdown: '27.49', validation_net_pnl: null, walk_forward: false,
    created_at: '2026-07-23 04:02:45', completed_at: '2026-07-23 04:02:46',
    ...overrides,
  }
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Dashboard', () => {
  it('shows a loading state before data arrives', () => {
    vi.mocked(api.getSystemOverview).mockReturnValue(new Promise(() => {}))
    vi.mocked(api.listBacktests).mockReturnValue(new Promise(() => {}))
    renderDashboard()
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0)
  })

  it('renders system overview stats once loaded', async () => {
    vi.mocked(api.getSystemOverview).mockResolvedValue(overview)
    vi.mocked(api.listBacktests).mockResolvedValue([])
    renderDashboard()

    await waitFor(() => expect(screen.getByText('0.6.0')).toBeInTheDocument())
    expect(screen.getByText('ok')).toBeInTheDocument()
  })

  it('shows an error state when the overview request fails', async () => {
    vi.mocked(api.getSystemOverview).mockRejectedValue(new ApiRequestError(500, 'Internal error'))
    vi.mocked(api.listBacktests).mockResolvedValue([])
    renderDashboard()

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Internal error'))
  })

  it('builds a leaderboard of the best completed run per strategy', async () => {
    vi.mocked(api.getSystemOverview).mockResolvedValue(overview)
    vi.mocked(api.listBacktests).mockResolvedValue([
      makeRun({ id: 'run-1', strategy: 'vwap_reversion', net_pnl: '50' }),
      makeRun({ id: 'run-2', strategy: 'vwap_reversion', net_pnl: '150' }), // better -- should win
      makeRun({ id: 'run-3', strategy: 'ema_crossover', net_pnl: '10' }),
      makeRun({ id: 'run-4', strategy: 'ema_crossover', status: 'failed', net_pnl: null }),
    ])
    renderDashboard()

    await waitFor(() => expect(screen.getByText('vwap_reversion')).toBeInTheDocument())
    // Only the better vwap_reversion run's P&L should be shown.
    expect(screen.getByText('$150.00')).toBeInTheDocument()
    expect(screen.queryByText('$50.00')).not.toBeInTheDocument()
  })

  it('shows an empty state when there are no completed backtests', async () => {
    vi.mocked(api.getSystemOverview).mockResolvedValue(overview)
    vi.mocked(api.listBacktests).mockResolvedValue([])
    renderDashboard()

    await waitFor(() => expect(screen.getByText(/No completed backtests yet/)).toBeInTheDocument())
  })
})
