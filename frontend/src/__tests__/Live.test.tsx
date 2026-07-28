import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Live from '../pages/Live'
import * as api from '../api'
import { ApiRequestError } from '../api'
import type { LiveSessionStatus } from '../types'

class FakeEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  static instances: FakeEventSource[] = []
  constructor() {
    FakeEventSource.instances.push(this)
  }
  emit(status: LiveSessionStatus) {
    this.onmessage?.({ data: JSON.stringify(status) } as MessageEvent)
  }
}

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    startLiveSession: vi.fn(),
    stopLiveSession: vi.fn(),
    getLiveStatus: vi.fn(),
    streamLiveStatus: vi.fn((onUpdate: (s: LiveSessionStatus) => void) => {
      const source = new FakeEventSource()
      source.onmessage = (event) => onUpdate(JSON.parse(event.data as string))
      return source as unknown as EventSource
    }),
  }
})

function stoppedStatus(overrides: Partial<LiveSessionStatus> = {}): LiveSessionStatus {
  return {
    status: 'stopped', run_id: null, strategy: null, contract: null, broker: null, live_symbol: null,
    resolution: null, poll_seconds: null, position: null, session_pnl: null,
    trade_count_today: null, halted: false, halt_reason: null, last_bar_time: null,
    last_bar_close: null, last_feed_error: null, error_message: null, started_at: null,
    stopped_at: null, warnings: [],
    ...overrides,
  }
}

function renderLive() {
  return render(
    <MemoryRouter>
      <Live />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  FakeEventSource.instances = []
})

describe('Live', () => {
  it('shows a loading state before the first status frame arrives', () => {
    renderLive()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('shows the start form once stopped status arrives', async () => {
    renderLive()
    FakeEventSource.instances[0].emit(stoppedStatus())

    await waitFor(() => expect(screen.getByText('stopped')).toBeInTheDocument())
    expect(screen.getByLabelText('Live symbol')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Start session/ })).toBeInTheDocument()
  })

  it('shows session details and a stop button while running', async () => {
    renderLive()
    FakeEventSource.instances[0].emit(stoppedStatus({
      status: 'running', strategy: 'ema_crossover', contract: 'MES', broker: 'paper',
      live_symbol: 'MESH6', resolution: '5min', poll_seconds: 30,
      session_pnl: '125.50', trade_count_today: 3,
      position: {
        side: 'long', quantity: 1, entry_price: '5000.00', stop_loss: '4995.00',
        take_profit: '5010.00', unrealized_pnl: '25.00',
      },
    }))

    await waitFor(() => expect(screen.getByText('running')).toBeInTheDocument())
    expect(screen.getByText('$125.50')).toBeInTheDocument()
    expect(screen.getByText('$25.00')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Stop session/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Start session/ })).not.toBeInTheDocument()
  })

  it('shows a starting indicator while the session is still coming up', async () => {
    renderLive()
    FakeEventSource.instances[0].emit(stoppedStatus({ status: 'starting' }))

    await waitFor(() => expect(screen.getByText('starting')).toBeInTheDocument())
    expect(screen.getByText(/Starting the live session/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Stop session/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Start session/ })).not.toBeInTheDocument()
  })

  it('surfaces a halt reason', async () => {
    renderLive()
    FakeEventSource.instances[0].emit(stoppedStatus({ status: 'running', halted: true, halt_reason: 'daily max loss hit' }))

    await waitFor(() => expect(screen.getByText('daily max loss hit')).toBeInTheDocument())
  })

  it('submits the start form', async () => {
    vi.mocked(api.startLiveSession).mockResolvedValue(stoppedStatus({ status: 'starting' }))
    renderLive()
    FakeEventSource.instances[0].emit(stoppedStatus())
    await waitFor(() => expect(screen.getByRole('button', { name: /Start session/ })).toBeInTheDocument())

    screen.getByRole('button', { name: /Start session/ }).closest('form')?.requestSubmit()

    await waitFor(() => expect(api.startLiveSession).toHaveBeenCalledWith({
      live_symbol: 'MESH6', resolution: '5min', poll_seconds: 30,
    }))
  })

  it('shows an error if starting fails', async () => {
    vi.mocked(api.startLiveSession).mockRejectedValue(new ApiRequestError(400, 'Refusing to start with a non-paper broker.'))
    renderLive()
    FakeEventSource.instances[0].emit(stoppedStatus())
    await waitFor(() => expect(screen.getByRole('button', { name: /Start session/ })).toBeInTheDocument())

    screen.getByRole('button', { name: /Start session/ }).closest('form')?.requestSubmit()

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Refusing to start with a non-paper broker.'))
  })
})
