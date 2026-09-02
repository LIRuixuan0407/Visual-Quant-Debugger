import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { PortfolioResearchRecord, PortfolioResearchSummary } from '../../types/research'
import PortfolioLabPage from './PortfolioLabPage'

const portfolioId = 'portfolio-risk-test'
const summary: PortfolioResearchSummary = {
  portfolio_research_id: portfolioId,
  name: 'Risk decomposition portfolio',
  created_at: '2025-01-01T00:00:00Z',
  dataset_id: 'dataset-test',
  factor_count: 2,
  combination: 'RANK_AVERAGE',
  revealed_stage: 'VALIDATION',
  net_return: 0.12,
  turnover: 1.4,
}

const record: PortfolioResearchRecord = {
  portfolio_research_id: portfolioId,
  name: 'Risk decomposition portfolio',
  created_at: '2025-01-01T00:00:00Z',
  dataset_id: 'dataset-test',
  dataset_fingerprint: 'sha256:dataset-test',
  universe: ['AAPL', 'MSFT'],
  factor_refs: [
    { research_id: 'momentum', weight: 0.5, direction_override: null },
    { research_id: 'quality', weight: 0.5, direction_override: null },
  ],
  factor_ids: ['momentum', 'quality'],
  factor_names: ['Momentum', 'Quality'],
  combination: 'RANK_AVERAGE',
  filters: {
    minimum_liquidity: null,
    maximum_volatility: null,
    require_factor_availability: true,
    include_symbols: [],
    exclude_symbols: [],
  },
  construction: {
    selection: 'TOP_N', top_n: 2, top_percent: 20, weighting: 'EQUAL_WEIGHT', max_single_position_weight: 0.6,
  },
  rebalance: 'MONTHLY',
  gross_notional: 20_000,
  initial_cash: 100_000,
  fee_bps: 5,
  slippage_bps: 5,
  revealed_stage: 'VALIDATION',
  stages: [{
    stage: 'VALIDATION',
    period: { start: '2024-01-01T00:00:00Z', end: '2024-06-30T00:00:00Z' },
    factor_checks: [],
    snapshots: [{
      timestamp: '2024-06-28T00:00:00Z',
      stage: 'VALIDATION',
      eligible_count: 2,
      selected_symbols: ['AAPL', 'MSFT'],
      positions: [],
    }],
    cost_preview: {
      gross_return: 0.14, fees: 100, slippage: 80, net_return: 0.12, turnover: 1.4, max_drawdown: -0.08, positions: 2, rebalance_count: 6,
    },
    risk_decomposition: {
      status: 'AVAILABLE',
      verdict: 'LOW_WEIGHT_HIGH_RISK',
      snapshot_timestamp: '2024-06-28T00:00:00Z',
      dataset_frequency: '1Day',
      observations: 120,
      annualization_factor: 252,
      volatility_basis: 'ANNUALIZED',
      portfolio_volatility: 0.18,
      per_observation_volatility: 0.0113,
      historical_var_95: 0.021,
      expected_shortfall_95: 0.031,
      confidence_level: 0.95,
      covariance: { symbols: ['AAPL', 'MSFT'], values: [[0.0001, 0.00003], [0.00003, 0.0004]] },
      correlation: { symbols: ['AAPL', 'MSFT'], values: [[1, 0.15], [0.15, 1]] },
      contributions: [
        {
          symbol: 'AAPL', portfolio_weight: 0.3, invested_weight: 0.3, marginal_contribution_to_volatility: 0.12,
          component_contribution_to_volatility: 0.036, component_risk_share: 0.2, risk_weight_gap: -0.1, low_weight_high_risk: false,
        },
        {
          symbol: 'MSFT', portfolio_weight: 0.7, invested_weight: 0.7, marginal_contribution_to_volatility: 0.2057,
          component_contribution_to_volatility: 0.144, component_risk_share: 0.8, risk_weight_gap: 0.1, low_weight_high_risk: true,
        },
      ],
      calculation_details: ['Portfolio variance is w\'Σw using aligned close-to-close returns.'],
      boundary_disclosure: 'Risk decomposition is diagnostic evidence only; it is not an optimizer.',
    },
  }],
  strategy: null,
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
  window.history.replaceState({}, '', '/')
})

it('renders backend risk decomposition without optimizing weights', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  window.history.replaceState({}, '', `/?portfolio_research_id=${portfolioId}`)
  vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
    const url = String(input)
    let body: unknown = []
    if (url === '/api/portfolio-research') body = [summary]
    else if (url === `/api/portfolio-research/${portfolioId}`) body = record
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  }))

  render(<I18nProvider><PortfolioLabPage onOpenReplay={() => undefined} onRunComplete={() => undefined} /></I18nProvider>)

  expect(await screen.findByText('Risk Decomposition')).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: 'Evidence map' })).toHaveTextContent('Risk Decomposition')
  expect(screen.getByText('18.00%')).toBeInTheDocument()
  expect(screen.getByText('Expected Shortfall 95%')).toBeInTheDocument()
  expect(screen.getByText('Low weight · high risk')).toBeInTheDocument()
  expect(screen.getByText('Correlation')).toBeInTheDocument()
  expect(screen.getByText('Covariance')).toBeInTheDocument()
  expect(screen.getByText('Risk decomposition is diagnostic evidence only; it is not an optimizer.')).toBeInTheDocument()
})
