import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LoadingState, ErrorState, EmptyState, StatTile, Badge, VerdictBadge, ApiErrorState, StaleBanner } from '../components/UI'

describe('LoadingState', () => {
  it('renders the default label', () => {
    render(<LoadingState />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading…')
  })

  it('renders a custom label', () => {
    render(<LoadingState label="Fetching trades…" />)
    expect(screen.getByRole('status')).toHaveTextContent('Fetching trades…')
  })

  it('renders a skeleton shaped like the content, keeping the label for screen readers', () => {
    const { container } = render(<LoadingState label="Loading overview…" variant="tiles" />)
    expect(container.querySelectorAll('.skeleton-tiles .skeleton-block')).toHaveLength(4)
    expect(screen.getByText('Loading overview…')).toHaveClass('sr-only')
  })

  it('keeps the label visible in the text variant', () => {
    const { container } = render(<LoadingState label="Parsing file…" variant="text" />)
    expect(container.querySelector('.skeleton')).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent('Parsing file…')
  })
})

describe('ApiErrorState', () => {
  const base = { refetch: vi.fn(), updatedAt: null }

  it('renders nothing without an error', () => {
    const { container } = render(<ApiErrorState api={{ ...base, data: null, error: null }} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the full error state when there is no earlier data', () => {
    const { container } = render(<ApiErrorState api={{ ...base, data: null, error: 'Backend down' }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Backend down')
    expect(container.querySelector('.stale-banner')).toBeNull()
  })

  it('renders a stale banner over earlier data, with its age and a retry', () => {
    const refetch = vi.fn()
    const updatedAt = new Date(2026, 9, 28, 10, 42)
    render(<ApiErrorState api={{ data: [1], error: 'Backend down', updatedAt, refetch }} />)
    const banner = screen.getByRole('alert')
    expect(banner).toHaveClass('stale-banner')
    expect(banner).toHaveTextContent('Refresh failed: Backend down')
    expect(banner).toHaveTextContent(updatedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it('treats an empty list as data worth keeping', () => {
    render(<ApiErrorState api={{ ...base, data: [], error: 'Backend down' }} />)
    expect(screen.getByRole('alert')).toHaveClass('stale-banner')
  })
})

describe('StaleBanner', () => {
  it('says "an earlier load" when the time is unknown', () => {
    render(<StaleBanner message="timeout" updatedAt={null} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Showing data from an earlier load')
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('ErrorState', () => {
  it('renders the error message', () => {
    render(<ErrorState message="Could not reach the API." />)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not reach the API.')
  })

  it('does not render a retry button when onRetry is omitted', () => {
    render(<ErrorState message="boom" />)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('calls onRetry when the retry button is clicked', () => {
    const onRetry = vi.fn()
    render(<ErrorState message="boom" onRetry={onRetry} />)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalledOnce()
  })
})

describe('EmptyState', () => {
  it('renders the given label', () => {
    render(<EmptyState label="No trades match these filters." />)
    expect(screen.getByText('No trades match these filters.')).toBeInTheDocument()
  })

  it('renders an optional quiet hint', () => {
    render(<EmptyState label="No jobs yet." hint={<a href="/backtest">Run a backtest</a>} />)
    expect(screen.getByRole('link', { name: 'Run a backtest' })).toBeInTheDocument()
  })
})

describe('StatTile', () => {
  it('renders label and value', () => {
    render(<StatTile label="Net P&L" value="$126.28" />)
    expect(screen.getByText('Net P&L')).toBeInTheDocument()
    expect(screen.getByText('$126.28')).toBeInTheDocument()
  })

  it('applies the tone class', () => {
    render(<StatTile label="Net P&L" value="$126.28" tone="good" />)
    expect(screen.getByText('$126.28')).toHaveClass('tone-good')
  })

  it('renders an optional sub-label', () => {
    render(<StatTile label="Max DD" value="$50" sub="2.0% of equity" />)
    expect(screen.getByText('2.0% of equity')).toBeInTheDocument()
  })

  it('marks a featured tile', () => {
    render(<StatTile label="Net P&L" value="$4,812" featured />)
    expect(screen.getByText('$4,812').parentElement).toHaveClass('stat-tile', 'featured')
  })
})

describe('Badge / VerdictBadge', () => {
  it('renders badge text with the right tone class', () => {
    render(<Badge tone="bad">loss</Badge>)
    expect(screen.getByText('loss')).toHaveClass('badge-bad')
  })

  it('maps overfit verdict levels to the right tone', () => {
    const { rerender } = render(<VerdictBadge level="green" label="Validated" />)
    expect(screen.getByText('Validated')).toHaveClass('badge-good')

    rerender(<VerdictBadge level="yellow" label="Questionable" />)
    expect(screen.getByText('Questionable')).toHaveClass('badge-warn')

    rerender(<VerdictBadge level="red" label="Likely overfit" />)
    expect(screen.getByText('Likely overfit')).toHaveClass('badge-bad')
  })
})
