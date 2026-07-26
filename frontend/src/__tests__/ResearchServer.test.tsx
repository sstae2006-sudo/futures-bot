import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ResearchServer from '../pages/ResearchServer'
import * as api from '../api'
import { ApiRequestError } from '../api'
import type { ConfigDeploymentOut, DatasetInfo, InsightOut, JobOut, ResearchServerStatus } from '../types'

class FakeEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  static instances: FakeEventSource[] = []
  constructor() {
    FakeEventSource.instances.push(this)
  }
  emit(job: JobOut) {
    this.onmessage?.({ data: JSON.stringify(job) } as MessageEvent)
  }
}

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getResearchServerStatus: vi.fn(),
    startResearchServer: vi.fn(),
    stopResearchServer: vi.fn(),
    runNightlyBatchNow: vi.fn(),
    getResearchServerFindings: vi.fn(),
    deployFinding: vi.fn(),
    rollbackConfigDeployment: vi.fn(),
    listConfigDeployments: vi.fn(),
    testFindingParams: vi.fn(),
    listDatasets: vi.fn(),
    streamJob: vi.fn((_jobId: string, onUpdate: (j: JobOut) => void, onDone?: (j: JobOut) => void) => {
      const source = new FakeEventSource()
      source.onmessage = (event) => {
        const job = JSON.parse(event.data as string) as JobOut
        onUpdate(job)
        if (job.status === 'completed' || job.status === 'failed') onDone?.(job)
      }
      return source as unknown as EventSource
    }),
  }
})

function makeStatus(overrides: Partial<ResearchServerStatus> = {}): ResearchServerStatus {
  return {
    running: false,
    started_at: null,
    uptime_seconds: null,
    data_scheduler: { running: false, targets: [], last_cycle_at: null, last_result: null, last_error: null, cycles_completed: 0 },
    paper_trader: { running: false, live_symbol: null, last_feed_error: null, strategies: {} },
    nightly_jobs: { running: false, last_run_date: null, last_run_summary: null, last_error: null },
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ResearchServer />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getResearchServerFindings).mockResolvedValue([])
  vi.mocked(api.listConfigDeployments).mockResolvedValue([])
  vi.mocked(api.listDatasets).mockResolvedValue([])
})

describe('ResearchServer', () => {
  it('shows a loading state before data arrives', () => {
    vi.mocked(api.getResearchServerStatus).mockReturnValue(new Promise(() => {}))
    renderPage()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('shows stopped status and a start button when not running', async () => {
    vi.mocked(api.getResearchServerStatus).mockResolvedValue(makeStatus())
    renderPage()

    await waitFor(() => expect(screen.getByRole('button', { name: /Start server/ })).toBeInTheDocument())
    expect(screen.getAllByText('stopped').length).toBeGreaterThan(0)
  })

  it('shows active strategies and a stop button when running', async () => {
    vi.mocked(api.getResearchServerStatus).mockResolvedValue(makeStatus({
      running: true, uptime_seconds: 3725,
      data_scheduler: { running: true, targets: ['MES:5min'], last_cycle_at: null, last_result: null, last_error: null, cycles_completed: 1 },
      paper_trader: {
        running: true, live_symbol: 'MESU6', last_feed_error: null,
        strategies: {
          ema_crossover: {
            status: 'running', run_id: 'r1', strategy: 'ema_crossover', position: null,
            session_pnl: '42.50', trade_count_today: 3, halted: false, halt_reason: null, error_message: null,
          },
        },
      },
      nightly_jobs: { running: true, last_run_date: '2026-07-23', last_run_summary: '6 job(s) submitted for 2026-07-23', last_error: null },
    }))
    renderPage()

    await waitFor(() => expect(screen.getByText('ema_crossover')).toBeInTheDocument())
    expect(screen.getByText('$42.50')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Stop server/ })).toBeInTheDocument()
    expect(screen.getByText(/6 job\(s\) submitted/)).toBeInTheDocument()
  })

  it('shows an error state when the status request fails', async () => {
    vi.mocked(api.getResearchServerStatus).mockRejectedValue(new ApiRequestError(500, 'Internal error'))
    renderPage()

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Internal error'))
  })

  it('renders findings from the discoveries feed', async () => {
    vi.mocked(api.getResearchServerStatus).mockResolvedValue(makeStatus())
    const finding: InsightOut = { strategy: 'ema_crossover', category: 'degradation', message: 'live expectancy is negative', severity: 'warning', details: null }
    vi.mocked(api.getResearchServerFindings).mockResolvedValue([finding])
    renderPage()

    await waitFor(() => expect(screen.getByText(/live expectancy is negative/)).toBeInTheDocument())
  })

  it('starts the server', async () => {
    vi.mocked(api.getResearchServerStatus).mockResolvedValue(makeStatus())
    vi.mocked(api.startResearchServer).mockResolvedValue(makeStatus({ running: true }))
    renderPage()
    await waitFor(() => expect(screen.getByRole('button', { name: /Start server/ })).toBeInTheDocument())

    screen.getByRole('button', { name: /Start server/ }).click()

    await waitFor(() => expect(screen.getByText('Research server started.')).toBeInTheDocument())
    expect(api.startResearchServer).toHaveBeenCalled()
  })

  describe('finding detail window', () => {
    function recommendationFinding(overrides: Partial<InsightOut> = {}): InsightOut {
      return {
        strategy: 'vwap_reversion', category: 'recommendation', severity: 'info',
        message: 'vwap_reversion: the latest nightly optimizer run found better params.',
        details: {
          run_id: 'opt1', current_params: { min_bars: 10 }, recommended_params: { min_bars: 20 },
          train_net_pnl: '500', is_deployable: true,
        },
        ...overrides,
      }
    }

    function deployment(overrides: Partial<ConfigDeploymentOut> = {}): ConfigDeploymentOut {
      return {
        id: 'd1', strategy: 'vwap_reversion', action: 'deploy', params: { min_bars: 20 },
        backup_path: 'config_backups/x.yaml', created_at: '2026-01-01T00:00:00Z',
        ...overrides,
      }
    }

    beforeEach(() => {
      vi.mocked(api.getResearchServerStatus).mockResolvedValue(makeStatus())
      FakeEventSource.instances = []
    })

    it('clicking a finding opens its detail window with structured details', async () => {
      vi.mocked(api.getResearchServerFindings).mockResolvedValue([recommendationFinding()])
      renderPage()
      await waitFor(() => expect(screen.getByText(/latest nightly optimizer run/)).toBeInTheDocument())

      fireEvent.click(screen.getByText(/latest nightly optimizer run/))

      expect(await screen.findByText('Finding Detail')).toBeInTheDocument()
      expect(screen.getByText('run_id')).toBeInTheDocument()
      expect(screen.getByText('opt1')).toBeInTheDocument()
    })

    it('a non-deployable recommendation explains why instead of showing deploy controls', async () => {
      vi.mocked(api.getResearchServerFindings).mockResolvedValue([
        recommendationFinding({ details: { ...recommendationFinding().details, is_deployable: false } }),
      ])
      renderPage()
      await waitFor(() => expect(screen.getByText(/latest nightly optimizer run/)).toBeInTheDocument())
      fireEvent.click(screen.getByText(/latest nightly optimizer run/))

      expect(await screen.findByText(/not config.yaml's active/)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Test More' })).not.toBeInTheDocument()
    })

    it('Test More runs a comparison job and shows the diff on completion', async () => {
      vi.mocked(api.getResearchServerFindings).mockResolvedValue([recommendationFinding()])
      vi.mocked(api.listDatasets).mockResolvedValue([{ filename: 'data.csv', size_bytes: 1, bars_hint: null } as DatasetInfo])
      vi.mocked(api.testFindingParams).mockResolvedValue({
        id: 'job1', kind: 'params_comparison', status: 'queued', progress_current: 0, progress_total: 0,
        progress_message: null, request: {}, result_id: null, result_payload: null, error_message: null,
        created_at: '2026-01-01T00:00:00Z', started_at: null, completed_at: null,
      })
      renderPage()
      await waitFor(() => expect(screen.getByText(/latest nightly optimizer run/)).toBeInTheDocument())
      fireEvent.click(screen.getByText(/latest nightly optimizer run/))
      await screen.findByText('Finding Detail')

      fireEvent.change(await screen.findByLabelText('Dataset'), { target: { value: 'data.csv' } })
      fireEvent.click(screen.getByRole('button', { name: 'Test More' }))

      await waitFor(() => expect(api.testFindingParams).toHaveBeenCalledWith({
        strategy: 'vwap_reversion', dataset: 'data.csv', recommended_params: { min_bars: 20 },
      }))
      await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0))

      FakeEventSource.instances[0].emit({
        id: 'job1', kind: 'params_comparison', status: 'completed', progress_current: 2, progress_total: 2,
        progress_message: 'Done', request: {}, result_id: null, error_message: null,
        result_payload: {
          current: { id: 'r1', kind: 'backtest', status: 'completed', strategy: 'vwap_reversion', contract: 'MES', trade_count: 10, net_pnl: '100', profit_factor: '1.2', win_rate: '50', sharpe_ratio: '0.5', max_drawdown: '20', validation_net_pnl: null, walk_forward: false, created_at: '', completed_at: null },
          recommended: { id: 'r2', kind: 'backtest', status: 'completed', strategy: 'vwap_reversion', contract: 'MES', trade_count: 12, net_pnl: '150', profit_factor: '1.4', win_rate: '55', sharpe_ratio: '0.7', max_drawdown: '15', validation_net_pnl: null, walk_forward: false, created_at: '', completed_at: null },
          improvement_pct: 50.0,
          trade_count_retained: 12, trade_count_retained_pct: 120.0,
          pnl_improvement: '50', profit_factor_improvement: '0.2', expectancy_improvement: '2.5', drawdown_reduction: '5',
        },
        created_at: '2026-01-01T00:00:00Z', started_at: null, completed_at: null,
      })

      expect(await screen.findByText('$50.00')).toBeInTheDocument()
      expect(await screen.findByText('12 (120%)')).toBeInTheDocument()
    })

    it('deploying requires a confirmation step before calling deployFinding', async () => {
      vi.mocked(api.getResearchServerFindings).mockResolvedValue([recommendationFinding()])
      vi.mocked(api.deployFinding).mockResolvedValue(deployment())
      renderPage()
      await waitFor(() => expect(screen.getByText(/latest nightly optimizer run/)).toBeInTheDocument())
      fireEvent.click(screen.getByText(/latest nightly optimizer run/))
      await screen.findByText('Finding Detail')

      fireEvent.click(screen.getByRole('button', { name: 'Deploy Recommended Params' }))
      expect(api.deployFinding).not.toHaveBeenCalled()
      expect(await screen.findByText('Confirm deploy')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: 'Confirm Deploy' }))
      await waitFor(() => expect(api.deployFinding).toHaveBeenCalledWith({
        strategy: 'vwap_reversion', params: { min_bars: 20 }, run_id: 'opt1',
      }))
      expect(await screen.findByText(/config.yaml has been updated/)).toBeInTheDocument()
    })

    it('shows deployment history and can roll back a prior deploy', async () => {
      vi.mocked(api.getResearchServerFindings).mockResolvedValue([recommendationFinding()])
      vi.mocked(api.listConfigDeployments).mockResolvedValue([deployment()])
      vi.mocked(api.rollbackConfigDeployment).mockResolvedValue(deployment({ id: 'd2', action: 'rollback' }))
      renderPage()
      await waitFor(() => expect(screen.getByText(/latest nightly optimizer run/)).toBeInTheDocument())
      fireEvent.click(screen.getByText(/latest nightly optimizer run/))

      expect(await screen.findByText('deploy')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: 'Undo this change' }))

      await waitFor(() => expect(api.rollbackConfigDeployment).toHaveBeenCalledWith('d1'))
    })
  })
})
