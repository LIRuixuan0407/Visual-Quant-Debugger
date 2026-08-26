import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { WalkForwardResearchRecord } from '../../types/research'
import WalkForwardPage from './WalkForwardPage'

const metrics = {
  observation_count: 120,
  cross_section_count: 24,
  ic: 0.08,
  rank_ic: 0.06,
  quantile_returns: [0.001, 0.002, 0.003, 0.004, 0.006],
  spread: 0.005,
  coverage: 0.96,
  turnover: 0.18,
  monotonic: true,
}

const record: WalkForwardResearchRecord = {
  walk_forward_id: 'walk-forward-test',
  name: 'Momentum stability',
  created_at: '2025-01-01T00:00:00Z',
  factor_research_id: 'factor-research-test',
  factor_id: 'momentum',
  factor_revision: '1.0.0',
  strategy_id: 'factor-strategy-test',
  strategy_revision: 'sha256:strategy',
  dataset_id: 'dataset-test',
  dataset_fingerprint: 'sha256:dataset',
  config: { research_months: 12, validation_months: 3, forward_months: 3, step_months: 3, start: null, end: null },
  horizon: 20,
  initial_cash: 100_000,
  fee_bps: 5,
  slippage_bps: 5,
  windows: [{
    definition: {
      index: 2,
      research: { start: '2022-01-01T00:00:00Z', end: '2022-12-31T23:59:59Z' },
      validation: { start: '2023-01-01T00:00:00Z', end: '2023-03-31T23:59:59Z' },
      forward: { start: '2023-04-01T00:00:00Z', end: '2023-06-30T23:59:59Z' },
    },
    research: metrics,
    validation: metrics,
    forward: { ...metrics, rank_ic: -0.03, monotonic: false },
    forward_strategy: { total_return: -0.04, sharpe: -0.7, max_drawdown: -0.09, trades: 8, fees: 21, slippage: 18, net_costs: 39 },
  }],
  stability: {
    positive_ic_window_ratio: 0.5,
    rank_ic_distribution: { count: 1, mean: -0.03, std: 0, minimum: -0.03, maximum: -0.03 },
    factor_sign_consistency: 1,
    quantile_monotonicity_stability: 0,
    turnover_stability: 1,
    strategy_return_distribution: { count: 1, mean: -0.04, std: 0, minimum: -0.04, maximum: -0.04 },
  },
  first_degradation: {
    window_index: 2,
    timestamp: '2023-04-01T00:00:00Z',
    reasons: ['FORWARD_RANK_IC_NEGATIVE'],
    factor_research_id: 'factor-research-test',
    strategy_id: 'factor-strategy-test',
    run_id: 'run-test',
    historical_market_path: '/historical-market?window=2',
    factor_lab_path: '/factor-lab?window=2',
    replay_path: '/runs/run-test/replay?window=2',
  },
  run_id: 'run-test',
  trace_id: 'trace-test',
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it('renders stability, all three phases, strategy costs, and degradation navigation', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
    const url = String(input)
    const body = url.includes('/api/factor-research')
      ? [{ research_id: 'factor-research-test', name: 'Momentum', created_at: '2025-01-01T00:00:00Z', dataset_id: 'dataset-test', factor_id: 'momentum', symbols: 6, revealed_stage: 'HOLDOUT', research_ic: 0.08, research_rank_ic: 0.06, factor_category: 'PRICE_VOLUME', data_source: 'MARKET', factor_origin: 'BUILT_IN', direction: 'HIGH' }]
      : [record]
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  }))
  const openHistorical = vi.fn()
  const openFactor = vi.fn()
  const openReplay = vi.fn()
  render(<I18nProvider><WalkForwardPage strategies={[]} onOpenHistorical={openHistorical} onOpenFactor={openFactor} onOpenReplay={openReplay} onRunComplete={() => undefined} /></I18nProvider>)

  expect((await screen.findAllByText('Momentum stability')).length).toBeGreaterThanOrEqual(2)
  expect(screen.getByText('Positive IC windows')).toBeInTheDocument()
  expect(screen.getByText('Research')).toBeInTheDocument()
  expect(screen.getByText('Validation')).toBeInTheDocument()
  expect(screen.getByText('Forward')).toBeInTheDocument()
  expect(screen.getByText('Net costs')).toBeInTheDocument()
  expect(screen.getByText('FORWARD_RANK_IC_NEGATIVE')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Open Historical Market' }))
  fireEvent.click(screen.getByRole('button', { name: 'Open Factor Lab' }))
  fireEvent.click(screen.getByRole('button', { name: 'Open Replay' }))
  expect(openHistorical).toHaveBeenCalledWith(record.first_degradation?.historical_market_path)
  expect(openFactor).toHaveBeenCalledWith(record.first_degradation?.factor_lab_path)
  expect(openReplay).toHaveBeenCalledWith('trace-test', record.first_degradation?.replay_path)
})
