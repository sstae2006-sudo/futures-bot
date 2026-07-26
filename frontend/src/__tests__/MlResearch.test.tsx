import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import MlResearch from '../pages/MlResearch'
import * as api from '../api'
import type {
  CorrelationRow, DatasetHealth, DeploymentStatus, MlModelOut, PredictionResult, StrategyInfo, TradeOut,
} from '../types'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    listStrategies: vi.fn(),
    getMlDatasetHealth: vi.fn(),
    getFeatureDistribution: vi.fn(),
    getCorrelation: vi.fn(),
    listModels: vi.fn(),
    getModelVersions: vi.fn(),
    getDeployment: vi.fn(),
    listDatasets: vi.fn(),
    listTrades: vi.fn(),
    predictTrade: vi.fn(),
    submitModelTraining: vi.fn(),
  }
})

function strategy(name = 'trend_pullback'): StrategyInfo {
  return { name, parameters: [] }
}

function health(overrides: Partial<DatasetHealth> = {}): DatasetHealth {
  return {
    status: 'READY', reasons: [], trade_count: 120, win_count: 60, loss_count: 60, win_rate: 0.5,
    date_range: ['2024-01-01T00:00:00Z', '2024-06-01T00:00:00Z'], feature_count: 5,
    missing_value_count: 0, missing_value_ratio: 0,
    total_rows: 130, unique_timestamps: 125, duplicate_market_events: 10,
    ...overrides,
  }
}

function correlationRows(): CorrelationRow[] {
  return [
    { feature: 'rsi', corr_vs_win: 0.42, corr_vs_pnl: 0.31, corr_vs_r: 0.28 },
    { feature: 'adx', corr_vs_win: -0.15, corr_vs_pnl: -0.10, corr_vs_r: null },
  ]
}

function model(overrides: Partial<MlModelOut> = {}): MlModelOut {
  return {
    id: 'm1', model_family: 'trend_pullback:random_forest', version: 1, strategy: 'trend_pullback',
    model_type: 'random_forest', status: 'finished', job_id: 'j1',
    feature_columns: ['rsi', 'adx'], hyperparameters: { n_estimators: 200 },
    evaluation_mode: 'chronological_split', dataset_size: 120, dataset_version: 'abc123def456',
    metrics: {
      train: { accuracy: 0.9, precision: 0.88, recall: 0.85, f1: 0.86, roc_auc: 0.91, trade_count: 84 },
      validation: { accuracy: 0.78, precision: 0.75, recall: 0.7, f1: 0.72, roc_auc: 0.8, trade_count: 18 },
      test: { accuracy: 0.76, precision: 0.74, recall: 0.71, f1: 0.72, roc_auc: 0.79, trade_count: 18 },
    },
    feature_importance: [{ feature: 'rsi', importance: 0.6 }, { feature: 'adx', importance: 0.2 }],
    overfit_warning: false, overfit_note: null, derived_backtest_metrics: null,
    artifact_path: 'ml_models/m1', app_version: '0.7.0', git_commit: 'deadbeef', notes: null,
    archived: false, error_message: null, created_at: '2026-01-01T00:00:00Z', completed_at: '2026-01-01T00:01:00Z',
    ...overrides,
  }
}

function deploymentStatus(overrides: Partial<DeploymentStatus> = {}): DeploymentStatus {
  return { strategy: 'trend_pullback', current: null, history: [], ...overrides }
}

function trade(overrides: Partial<TradeOut> = {}): TradeOut {
  return {
    id: 1, run_id: 'r1', contract: 'MES', strategy: 'trend_pullback', strategy_params: {},
    entry_time: '2024-01-01T09:00:00Z', exit_time: '2024-01-01T09:30:00Z', side: 'long',
    entry_price: '100', exit_price: '105', gross_pnl: '50', commission: '1.24', net_pnl: '48.76',
    holding_minutes: 30, exit_reason: 'take_profit', session_date: '2024-01-01', day_of_week: 'Monday',
    hour: 9, entry_reason: 'test', entry_metadata: { rsi: 60 }, outcome: 'win',
    mfe_points: null, mae_points: null, efficiency: null, regime_trend: null, regime_volatility: null, regime_session: null,
    trade_id: 't1', stop_loss: null, take_profit: null, entry_slippage: '0', exit_slippage: '0',
    ...overrides,
  }
}

function prediction(overrides: Partial<PredictionResult> = {}): PredictionResult {
  return {
    model_id: 'm1', probability: 0.72, confidence: 0.72, expected_win_probability: 0.72,
    expected_value_r: 0.85, similar_trade_count: 15, similar_trade_win_rate: 0.6,
    calibration_bucket: { predicted: 0.7, actual_win_rate: 0.65 },
    top_reasons: [{ feature: 'rsi', value: 65, contribution: 0.4 }],
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listStrategies).mockResolvedValue([strategy()])
  vi.mocked(api.getMlDatasetHealth).mockResolvedValue(health())
  vi.mocked(api.getCorrelation).mockResolvedValue(correlationRows())
  vi.mocked(api.listModels).mockResolvedValue([model()])
  vi.mocked(api.getModelVersions).mockResolvedValue([model()])
  vi.mocked(api.getDeployment).mockResolvedValue(deploymentStatus())
  vi.mocked(api.listDatasets).mockResolvedValue([])
  vi.mocked(api.listTrades).mockResolvedValue([trade()])
  vi.mocked(api.getFeatureDistribution).mockResolvedValue({
    feature: 'rsi', bins: [0, 50, 100], counts: [5, 10], win_counts: [2, 6], loss_counts: [3, 4],
    mean: 55, std: 12, min: 10, max: 95, win_average: 60, loss_average: 45,
  })
})

async function selectTab(label: string) {
  const buttons = await screen.findAllByRole('button', { name: label })
  buttons[0].click()
}

describe('MlResearch', () => {
  it('Dataset tab: shows dataset health once loaded', async () => {
    render(<MlResearch />)
    await waitFor(() => expect(screen.getByText('READY')).toBeInTheDocument())
    expect(screen.getAllByText('120')).toHaveLength(2) // total trades + final training dataset size
    expect(screen.getByText('130')).toBeInTheDocument() // total rows across all runs
    expect(screen.getByText('10')).toBeInTheDocument() // duplicate market events
    expect(api.getMlDatasetHealth).toHaveBeenCalledWith('trend_pullback')
  })

  it('Dataset tab: surfaces a NOT_ENOUGH_DATA verdict distinctly', async () => {
    vi.mocked(api.getMlDatasetHealth).mockResolvedValue(
      health({ status: 'NOT_ENOUGH_DATA', reasons: ['Only 10 trades (need at least 60).'], trade_count: 10 }),
    )
    render(<MlResearch />)
    await waitFor(() => expect(screen.getByText('NOT ENOUGH DATA')).toBeInTheDocument())
    expect(screen.getByText(/Only 10 trades/)).toBeInTheDocument()
  })

  it('Correlation tab: renders the heatmap and ranked tables once selected', async () => {
    render(<MlResearch />)
    await waitFor(() => expect(screen.getByText('READY')).toBeInTheDocument())
    await selectTab('Correlation')
    await waitFor(() => expect(api.getCorrelation).toHaveBeenCalledWith('trend_pullback'))
    expect((await screen.findAllByText('rsi')).length).toBeGreaterThan(0)
  })

  it('Feature Explorer tab: clicking a feature loads its distribution', async () => {
    render(<MlResearch />)
    await waitFor(() => expect(screen.getByText('READY')).toBeInTheDocument())
    await selectTab('Feature Explorer')
    const rsiButtons = await screen.findAllByRole('button', { name: 'rsi' })
    rsiButtons[0].click()
    await waitFor(() => expect(api.getFeatureDistribution).toHaveBeenCalledWith('trend_pullback', 'rsi'))
    await waitFor(() => expect(screen.getByText('60.000')).toBeInTheDocument()) // win average
  })

  it('Models & Training tab: lists an existing model with in-sample/out-of-sample metrics separated', async () => {
    render(<MlResearch />)
    await waitFor(() => expect(screen.getByText('READY')).toBeInTheDocument())
    await selectTab('Models & Training')
    await waitFor(() => expect(api.listModels).toHaveBeenCalled())
    expect(await screen.findByText('In-Sample (Train)')).toBeInTheDocument()
    expect(screen.getByText('Out-of-Sample (Validation)')).toBeInTheDocument()
    expect(screen.getByText('finished')).toBeInTheDocument()
  })

  it('Models & Training tab: flags an overfit model with a warning banner', async () => {
    vi.mocked(api.listModels).mockResolvedValue([model({
      overfit_warning: true, overfit_note: 'Train accuracy 0.95 vs validation 0.60 -- likely overfit.',
    })])
    render(<MlResearch />)
    await waitFor(() => expect(screen.getByText('READY')).toBeInTheDocument())
    await selectTab('Models & Training')
    expect(await screen.findByText('Overfitting Warning')).toBeInTheDocument()
    expect(screen.getByText(/likely overfit/)).toBeInTheDocument()
  })

  it('Comparison tab: highlights the best-accuracy model', async () => {
    vi.mocked(api.listModels).mockResolvedValue([
      model({ id: 'm1', version: 1 }),
      model({
        id: 'm2', version: 2,
        metrics: {
          train: { accuracy: 0.95, precision: 0.9, recall: 0.9, f1: 0.9, roc_auc: 0.95, trade_count: 84 },
          validation: { accuracy: 0.9, precision: 0.88, recall: 0.85, f1: 0.86, roc_auc: 0.92, trade_count: 18 },
        },
      }),
    ])
    render(<MlResearch />)
    await waitFor(() => expect(screen.getByText('READY')).toBeInTheDocument())
    await selectTab('Comparison')
    expect(await screen.findByText('best')).toBeInTheDocument()
  })

  it('Prediction Sandbox tab: predicting shows the model output', async () => {
    vi.mocked(api.predictTrade).mockResolvedValue(prediction())
    render(<MlResearch />)
    await waitFor(() => expect(screen.getByText('READY')).toBeInTheDocument())
    await selectTab('Prediction Sandbox')

    const tradeSelect = await screen.findByLabelText('Historical trade')
    const tradeOption = (tradeSelect as HTMLSelectElement).options[1].value
    ;(tradeSelect as HTMLSelectElement).value = tradeOption
    tradeSelect.dispatchEvent(new Event('change', { bubbles: true }))

    const predictButton = screen.getByRole('button', { name: 'Predict' })
    predictButton.click()

    await waitFor(() => expect(api.predictTrade).toHaveBeenCalledWith('m1', 1))
    expect(await screen.findAllByText('72.0%')).toHaveLength(2) // Confidence + Win Probability
    expect(screen.getByText('0.85')).toBeInTheDocument() // Expected Value (R)
  })

  it('Terminal tab: shows an empty state before any training has run', async () => {
    render(<MlResearch />)
    await waitFor(() => expect(screen.getByText('READY')).toBeInTheDocument())
    await selectTab('Terminal')
    expect(await screen.findByText(/Nothing logged yet/)).toBeInTheDocument()
  })
})
