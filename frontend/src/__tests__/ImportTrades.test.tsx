import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ImportTrades from '../pages/ImportTrades'
import * as api from '../api'
import type { ClientProfile, ImportHistoryOut, ImportUploadResponse, JobOut } from '../types'

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
    listClientProfiles: vi.fn(),
    createClientProfile: vi.fn(),
    uploadImportFile: vi.fn(),
    confirmImport: vi.fn(),
    cancelImportStaging: vi.fn(),
    listImportHistory: vi.fn(),
    getJob: vi.fn(),
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

function profile(overrides: Partial<ClientProfile> = {}): ClientProfile {
  return { id: 'p1', name: 'john-doe', notes: null, created_at: '2026-01-01T00:00:00Z', ...overrides }
}

function uploadResponse(overrides: Partial<ImportUploadResponse> = {}): ImportUploadResponse {
  return {
    import_id: 'stg1', profile_id: 'p1', filename: 'trades.csv', detected_format: 'tradovate',
    raw_headers: ['Order ID', 'Account', 'Contract', 'B/S', 'Filled Qty', 'Fill Time', 'Avg Fill Price'],
    suggested_mapping: {
      timestamp: 'Fill Time', symbol: 'Contract', side: 'B/S', quantity: 'Filled Qty', price: 'Avg Fill Price',
      commission: null, realized_pnl: null, account: 'Account', fill_id: 'Order ID',
    },
    total_rows: 2, duplicate_count: 0, error_count: 0, matched_trade_count: 1, errors: [], warnings: [],
    preview_fill_rows: [
      { row: 1, timestamp: '2024-01-01T09:00:00Z', symbol: 'MESZ5', side: 'buy', quantity: '2', price: '5000', commission: '1.24', realized_pnl: null },
    ],
    preview_trades: [
      { entry_time: '2024-01-01T09:00:00Z', exit_time: '2024-01-01T10:00:00Z', symbol: 'MESZ5', side: 'long', quantity: '1', entry_price: '5000', exit_price: '5010' },
    ],
    ...overrides,
  }
}

function historyRow(overrides: Partial<ImportHistoryOut> = {}): ImportHistoryOut {
  return {
    id: 'imp1', profile_id: 'p1', filename: 'trades.csv', detected_format: 'tradovate', status: 'completed',
    total_fill_rows: 2, imported_fill_count: 2, duplicate_fill_count: 0, error_count: 0, trades_created: 1,
    errors: [], warnings: [], job_id: 'job1', error_message: null, created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function jobOut(overrides: Partial<JobOut> = {}): JobOut {
  return {
    id: 'job1', kind: 'client_import', status: 'queued', progress_current: 0, progress_total: 0,
    progress_message: null, request: {}, result_id: null, result_payload: null, error_message: null,
    created_at: '2026-01-01T00:00:00Z', started_at: null, completed_at: null,
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ImportTrades />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  FakeEventSource.instances = []
  vi.mocked(api.listClientProfiles).mockResolvedValue([profile()])
  vi.mocked(api.listImportHistory).mockResolvedValue([])
})

describe('ImportTrades', () => {
  it('lists existing client profiles and defaults to the first one', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Profile')).toBeInTheDocument())
    expect(screen.getByRole('option', { name: 'john-doe' })).toBeInTheDocument()
  })

  it('creates a new client profile', async () => {
    vi.mocked(api.createClientProfile).mockResolvedValue(profile({ id: 'p2', name: 'alice' }))
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('New profile name')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('New profile name'), { target: { value: 'alice' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create Profile' }))

    await waitFor(() => expect(api.createClientProfile).toHaveBeenCalledWith({ name: 'alice' }))
  })

  it('uploading a file shows the detected format, stats, and mapping wizard', async () => {
    vi.mocked(api.uploadImportFile).mockResolvedValue(uploadResponse())
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Profile')).toBeInTheDocument())

    const file = new File(['col1,col2\na,b'], 'trades.csv', { type: 'text/csv' })
    await waitFor(() => expect(document.querySelector('input[type="file"]')).not.toBeNull())
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => expect(api.uploadImportFile).toHaveBeenCalledWith('p1', file))
    expect(await screen.findByText('tradovate')).toBeInTheDocument()
    expect(screen.getAllByText('2').length).toBeGreaterThan(0) // total rows stat tile (and/or preview qty)
    expect(screen.getByLabelText('Timestamp *')).toHaveValue('Fill Time')
    expect(screen.getByLabelText('Symbol / Contract *')).toHaveValue('Contract')
  })

  it('lets the user override a mapping field before confirming', async () => {
    vi.mocked(api.uploadImportFile).mockResolvedValue(uploadResponse())
    vi.mocked(api.confirmImport).mockResolvedValue(jobOut())
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Profile')).toBeInTheDocument())

    const file = new File(['x'], 'trades.csv', { type: 'text/csv' })
    await waitFor(() => expect(document.querySelector('input[type="file"]')).not.toBeNull())
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    await screen.findByText('tradovate')

    fireEvent.change(screen.getByLabelText('Account'), { target: { value: 'Order ID' } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Import' }))

    await waitFor(() => expect(api.confirmImport).toHaveBeenCalledWith(
      'stg1',
      expect.objectContaining({ account: 'Order ID', timestamp: 'Fill Time' }),
    ))
  })

  it('streams job progress to completion and shows a link to Trade Explorer', async () => {
    vi.mocked(api.uploadImportFile).mockResolvedValue(uploadResponse())
    vi.mocked(api.confirmImport).mockResolvedValue(jobOut())
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Profile')).toBeInTheDocument())

    const file = new File(['x'], 'trades.csv', { type: 'text/csv' })
    await waitFor(() => expect(document.querySelector('input[type="file"]')).not.toBeNull())
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    await screen.findByText('tradovate')

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Import' }))
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0))

    FakeEventSource.instances[0].emit(jobOut({
      status: 'completed', progress_current: 3, progress_total: 3, result_id: 'stg1',
    }))

    await waitFor(() => expect(screen.getByText(/view these trades in Trade Explorer/)).toBeInTheDocument())
  })

  it('renders import history rows', async () => {
    vi.mocked(api.listImportHistory).mockResolvedValue([historyRow()])
    renderPage()
    expect(await screen.findByText('trades.csv')).toBeInTheDocument()
    expect(screen.getByText('completed')).toBeInTheDocument()
  })
})
