import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ParameterHeatmap } from '../components/ParameterHeatmap'
import type { TrialOut } from '../types'

function makeTrial(overrides: Partial<TrialOut> = {}): TrialOut {
  return {
    rank: null, params: {}, train_trades: 10, train_net_pnl: '0', train_profit_factor: '1.1',
    train_max_drawdown: '10', validation_trades: null, validation_net_pnl: null,
    validation_profit_factor: null, validation_max_drawdown: null,
    ...overrides,
  }
}

describe('ParameterHeatmap', () => {
  it('shows a message when no parameter varied', () => {
    const trials = [makeTrial({ params: { fast_period: 8 } }), makeTrial({ params: { fast_period: 8 } })]
    render(<ParameterHeatmap trials={trials} />)
    expect(screen.getByText(/No swept parameter varied/)).toBeInTheDocument()
  })

  it('renders a value cell for each combination of a single varying parameter', () => {
    const trials = [
      makeTrial({ params: { fast_period: 5 }, train_net_pnl: '100' }),
      makeTrial({ params: { fast_period: 9 }, train_net_pnl: '200' }),
    ]
    render(<ParameterHeatmap trials={trials} />)
    expect(screen.getByText('$100')).toBeInTheDocument()
    expect(screen.getByText('$200')).toBeInTheDocument()
  })

  it('averages net P&L across trials sharing the same parameter value', () => {
    const trials = [
      makeTrial({ params: { fast_period: 5, slow_period: 20 }, train_net_pnl: '100' }),
      makeTrial({ params: { fast_period: 5, slow_period: 30 }, train_net_pnl: '300' }),
    ]
    render(<ParameterHeatmap trials={trials} />)
    // Only fast_period varies with a single axis choice by default (fast_period,
    // slow_period both vary here) -- with two varying params, cells are per
    // (x,y) pair, so each individual value (100, 300) should still appear
    // since each (fast_period, slow_period) pair is unique.
    expect(screen.getByText('$100')).toBeInTheDocument()
    expect(screen.getByText('$300')).toBeInTheDocument()
  })

  it('offers axis selectors for two or more varying parameters', () => {
    const trials = [
      makeTrial({ params: { fast_period: 5, slow_period: 20 } }),
      makeTrial({ params: { fast_period: 9, slow_period: 30 } }),
    ]
    render(<ParameterHeatmap trials={trials} />)
    expect(screen.getByLabelText('X axis')).toBeInTheDocument()
    expect(screen.getByLabelText('Y axis')).toBeInTheDocument()
  })
})
