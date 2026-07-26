import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JobProgressBar } from '../components/JobProgress'
import type { JobOut } from '../types'

function makeJob(overrides: Partial<JobOut> = {}): JobOut {
  return {
    id: 'job-1', kind: 'backtest', status: 'running', progress_current: 0, progress_total: 0,
    progress_message: null, request: {}, result_id: null, result_payload: null, error_message: null,
    created_at: '2026-01-01T00:00:00Z', started_at: null, completed_at: null,
    ...overrides,
  }
}

describe('JobProgressBar', () => {
  it('shows the queued/running status badge', () => {
    render(<JobProgressBar job={makeJob({ status: 'running' })} />)
    expect(screen.getByText('running')).toBeInTheDocument()
  })

  it('computes the correct percentage from current/total', () => {
    render(<JobProgressBar job={makeJob({ status: 'running', progress_current: 25, progress_total: 100 })} />)
    expect(screen.getByText('25%')).toBeInTheDocument()
  })

  it('shows the progress message when present', () => {
    render(<JobProgressBar job={makeJob({ progress_message: '50/200 bars processed' })} />)
    expect(screen.getByText('50/200 bars processed')).toBeInTheDocument()
  })

  it('shows a waiting placeholder before any progress message arrives', () => {
    render(<JobProgressBar job={makeJob({ progress_message: null })} />)
    expect(screen.getByText('Waiting to start…')).toBeInTheDocument()
  })

  it('shows the error message instead of the progress message when failed', () => {
    render(<JobProgressBar job={makeJob({
      status: 'failed', error_message: 'No bars loaded', progress_message: 'this should not show',
    })} />)
    expect(screen.getByText('No bars loaded')).toBeInTheDocument()
    expect(screen.queryByText('this should not show')).not.toBeInTheDocument()
  })

  it('caps the fill percentage at 100 even if current exceeds total', () => {
    const { container } = render(<JobProgressBar job={makeJob({ progress_current: 150, progress_total: 100 })} />)
    const fill = container.querySelector('.job-progress-fill') as HTMLElement
    expect(fill.style.width).toBe('100%')
  })
})
