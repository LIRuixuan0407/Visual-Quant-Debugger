import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { CorrelationCell, FactorRelationshipRecord } from '../../types/research'
import FactorRelationshipPage from './FactorRelationshipPage'
import PortfolioLabPage from './PortfolioLabPage'

const ids = ['factor-research-momentum', 'factor-research-reversal']
const cell = (
  left: string,
  right: string,
  semantic: CorrelationCell['semantic'],
  value: number,
): CorrelationCell => ({
  left_research_id: left,
  right_research_id: right,
  semantic,
  pearson: value,
  spearman: value - 0.02,
  observations: 240,
})

const matrix = (semantic: CorrelationCell['semantic']) => [
  cell(ids[0], ids[0], semantic, 1),
  cell(ids[0], ids[1], semantic, 0.8123),
  cell(ids[1], ids[0], semantic, 0.8123),
  cell(ids[1], ids[1], semantic, 1),
]

const record: FactorRelationshipRecord = {
  relationship_id: 'factor-relationship-test',
  name: 'Momentum × Reversal relationship',
  created_at: '2025-01-01T00:00:00Z',
  stage: 'VALIDATION',
  period: { start: '2023-01-01T00:00:00Z', end: '2023-06-30T00:00:00Z' },
  horizon: 20,
  rolling_window: 60,
  top_percent: 20,
  redundancy_threshold: 0.75,
  overlap_threshold: 0.6,
  dataset_id: 'dataset-test',
  dataset_fingerprint: 'sha256:dataset',
  universe: ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'META'],
  factor_research_ids: ids,
  factor_ids: ['momentum', 'reversal'],
  factor_names: ['Momentum', 'Reversal'],
  factor_revisions: ['1.0.0', '1.0.0'],
  value_correlations: matrix('FACTOR_VALUES'),
  rank_correlations: matrix('FACTOR_RANKS'),
  return_correlations: matrix('FACTOR_RETURNS'),
  rolling_correlations: [{
    left_research_id: ids[0],
    right_research_id: ids[1],
    semantic: 'FACTOR_RANKS',
    window: 60,
    points: [
      { timestamp: '2023-05-01T00:00:00Z', pearson: 0.78, spearman: 0.74, observations: 300 },
      { timestamp: '2023-06-01T00:00:00Z', pearson: 0.81, spearman: 0.79, observations: 300 },
    ],
  }],
  redundancy: [{
    left_research_id: ids[0],
    right_research_id: ids[1],
    status: 'HIGH_REDUNDANCY',
    rank_correlation: 0.8123,
    top_quantile_overlap: 0.75,
    reason: 'Review the pair; nothing is removed or reweighted.',
  }],
  exposure_overlap: [{
    left_research_id: ids[0],
    right_research_id: ids[1],
    top_percent: 20,
    mean_intersection_count: 2,
    mean_union_count: 3,
    mean_overlap: 0.75,
    mean_jaccard: 0.6667,
    timestamps: 30,
    points: [{ timestamp: '2023-06-01T00:00:00Z', intersection_count: 2, union_count: 3, overlap_percent: 0.75, jaccard: 0.6667 }],
  }],
  incremental_information: [{
    base_research_id: ids[0],
    added_research_id: ids[1],
    normalization: 'DIRECTION_ADJUSTED_PERCENTILE_RANK_AVERAGE',
    base_rank_ic: 0.04,
    composite_rank_ic: 0.05,
    rank_ic_delta: 0.01,
    base_spread: 0.02,
    composite_spread: 0.025,
    spread_delta: 0.005,
    base_coverage: 0.92,
    composite_coverage: 0.90,
    coverage_delta: -0.02,
    base_turnover: 0.25,
    composite_turnover: 0.22,
    turnover_delta: -0.03,
    base_portfolio_return: 0.012,
    composite_portfolio_return: 0.015,
    portfolio_effect: 0.003,
  }],
  clusters: [{ cluster_id: 'cluster-1', factor_research_ids: ids, rule: 'Connected component at 0.75' }],
  correlation_methodology: 'Three correlation semantics are computed separately by the backend.',
  incremental_disclosure: 'These are associations, not causal improvement claims.',
  crowding_disclosure: 'Internal VQD portfolio overlap is not market crowding evidence.',
}

const factors = [
  { research_id: ids[0], name: 'Momentum study', created_at: '2025-01-01T00:00:00Z', dataset_id: 'dataset-test', factor_id: 'momentum', symbols: 5, revealed_stage: 'HOLDOUT', research_ic: 0.04, research_rank_ic: 0.05, factor_category: 'PRICE_VOLUME', data_source: 'MARKET', factor_origin: 'BUILT_IN', direction: 'HIGH' },
  { research_id: ids[1], name: 'Reversal study', created_at: '2025-01-01T00:00:00Z', dataset_id: 'dataset-test', factor_id: 'reversal', symbols: 5, revealed_stage: 'HOLDOUT', research_ic: 0.03, research_rank_ic: 0.04, factor_category: 'PRICE_VOLUME', data_source: 'MARKET', factor_origin: 'BUILT_IN', direction: 'HIGH' },
]

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it('renders all backend relationship evidence and submits both thresholds', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    const body = init?.method === 'POST'
      ? record
      : url.includes('/api/factor-research') ? factors : [record]
    return Promise.resolve(new Response(JSON.stringify(body), { status: init?.method === 'POST' ? 201 : 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<I18nProvider><FactorRelationshipPage /></I18nProvider>)

  expect((await screen.findAllByText('Momentum × Reversal relationship')).length).toBeGreaterThanOrEqual(2)
  expect(screen.getByText('Correlation Matrix')).toBeInTheDocument()
  expect(screen.getAllByText('Factor Values').length).toBeGreaterThanOrEqual(1)
  expect(screen.getAllByText('Rank Correlation').length).toBeGreaterThanOrEqual(1)
  expect(screen.getAllByText('Factor Return Correlation').length).toBeGreaterThanOrEqual(1)
  expect(screen.getAllByText('Rolling Correlation').length).toBeGreaterThanOrEqual(1)
  expect(screen.getByText('HIGH REDUNDANCY')).toBeInTheDocument()
  expect(screen.getByText('Top Quantile Overlap & Jaccard')).toBeInTheDocument()
  expect(screen.getByText('Incremental Information')).toBeInTheDocument()
  expect(screen.getByText('Factor Cluster')).toBeInTheDocument()
  expect(screen.getByText('Internal VQD portfolio overlap is not market crowding evidence.')).toBeInTheDocument()

  screen.getAllByRole('checkbox').forEach((checkbox) => fireEvent.click(checkbox))
  fireEvent.click(screen.getByRole('button', { name: 'Run Relationship Research' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/factor-relationships',
    expect.objectContaining({ method: 'POST' }),
  ))
  const postCall = fetchMock.mock.calls.find((call) => call[1]?.method === 'POST')
  expect(JSON.parse(String(postCall?.[1]?.body))).toMatchObject({
    redundancy_threshold: 0.75,
    overlap_threshold: 0.6,
    factor_research_ids: ids,
  })
})

it('warns Portfolio Lab without changing factor selection or weights', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
    const url = String(input)
    const body = url.includes('/api/factor-relationships')
      ? [record]
      : url.includes('/api/factor-research') ? factors : []
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  }))
  render(<I18nProvider><PortfolioLabPage onOpenReplay={() => undefined} onRunComplete={() => undefined} /></I18nProvider>)

  const momentumCheckbox = await screen.findByLabelText(/Momentum study/)
  const reversalCheckbox = await screen.findByLabelText(/Reversal study/)
  fireEvent.click(momentumCheckbox)
  fireEvent.click(reversalCheckbox)
  expect(await screen.findByText('High-redundancy warning')).toBeInTheDocument()
  expect(screen.getByText('Portfolio Lab does not remove, reweight, or optimize these factors.')).toBeInTheDocument()
  expect(momentumCheckbox).toBeChecked()
  expect(reversalCheckbox).toBeChecked()
})
