import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LoadingState, ErrorState, EmptyState, StatTile, Badge, VerdictBadge } from '../components/UI'

describe('LoadingState', () => {
  it('renders the default label', () => {
    render(<LoadingState />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading…')
  })

  it('renders a custom label', () => {
    render(<LoadingState label="Fetching trades…" />)
    expect(screen.getByRole('status')).toHaveTextContent('Fetching trades…')
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
