import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import MarketData from '../pages/MarketData'
import * as api from '../api'
import { ApiRequestError } from '../api'
import type { MarketDataOverview } from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getMarketDataOverview: vi.fn(),
    syncMarketDataNow: vi.fn(),
    backfillMarketData: vi.fn(),
    verifyMarketData: vi.fn(),
    repairMarketDataGaps: vi.fn(),
    startMarketDataScheduler: vi.fn(),
    stopMarketDataScheduler: vi.fn(),
  }
})

function makeOverview(overrides: Partial<MarketDataOverview> = {}): MarketDataOverview {
  return {
    total_bars: 141455,
    products: [{
      product_code: 'MES', contracts_stored: ['MESM6', 'MESU6'], bars_stored: 98765,
      earliest: '2024-07-21T00:00:00+00:00', latest: '2026-07-22T23:55:00+00:00', open_gaps: 0,
    }],
    total_open_gaps: 0,
    database_path: 'market_data.db',
    database_size_bytes: 10_000_000,
    last_sync_at: '2026-07-23 10:00:00',
    last_sync_status: 'completed',
    recent_rolls: [{ product_code: 'MES', from_contract: 'MESM6', to_contract: 'MESU6', rolled_at: '2026-06-18 00:00:00' }],
    scheduler_running: false,
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <MarketData />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('MarketData', () => {
  it('shows a loading state before data arrives', () => {
    vi.mocked(api.getMarketDataOverview).mockReturnValue(new Promise(() => {}))
    renderPage()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('renders overview stats and product coverage once loaded', async () => {
    vi.mocked(api.getMarketDataOverview).mockResolvedValue(makeOverview())
    renderPage()

    await waitFor(() => expect(screen.getByText('141,455')).toBeInTheDocument())
    expect(screen.getByText('MES')).toBeInTheDocument()
    expect(screen.getByText('MESM6, MESU6')).toBeInTheDocument()
    expect(screen.getByText(/MESM6 → MESU6/)).toBeInTheDocument()
  })

  it('shows an empty state when nothing has been synced yet', async () => {
    vi.mocked(api.getMarketDataOverview).mockResolvedValue(makeOverview({ products: [], total_bars: 0 }))
    renderPage()

    await waitFor(() => expect(screen.getByText(/No data synced yet/)).toBeInTheDocument())
  })

  it('shows an error state when the overview request fails', async () => {
    vi.mocked(api.getMarketDataOverview).mockRejectedValue(new ApiRequestError(500, 'Internal error'))
    renderPage()

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Internal error'))
  })

  it('syncs now and shows the result', async () => {
    vi.mocked(api.getMarketDataOverview).mockResolvedValue(makeOverview())
    vi.mocked(api.syncMarketDataNow).mockResolvedValue({
      id: 'run-1', product_code: 'MES', resolution: '5min', kind: 'incremental',
      status: 'completed', bars_fetched: 12, error_message: null,
      started_at: '2026-07-23 10:00:00', completed_at: '2026-07-23 10:00:01',
    })
    renderPage()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sync now' })).toBeInTheDocument())

    screen.getByRole('button', { name: 'Sync now' }).click()

    await waitFor(() => expect(screen.getByText(/12 new bar\(s\)/)).toBeInTheDocument())
    expect(api.syncMarketDataNow).toHaveBeenCalledWith({ product_code: 'MES', resolution: '5min' })
  })

  it('starts the scheduler', async () => {
    vi.mocked(api.getMarketDataOverview).mockResolvedValue(makeOverview({ scheduler_running: false }))
    vi.mocked(api.startMarketDataScheduler).mockResolvedValue({
      running: true, targets: ['MES:5min'], last_cycle_at: null, last_result: null, last_error: null, cycles_completed: 0,
    })
    renderPage()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start scheduler' })).toBeInTheDocument())

    screen.getByRole('button', { name: 'Start scheduler' }).click()

    await waitFor(() => expect(screen.getByText(/Scheduler started/)).toBeInTheDocument())
  })
})
