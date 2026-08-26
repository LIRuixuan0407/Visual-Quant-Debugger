import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { ResearchHypothesis } from '../../types/research'
import DiscoveryWorkspacePage from './DiscoveryWorkspacePage'

const factorResearchId = 'factor-research-momentum'

const factor = {
  research_id: factorResearchId,
  name: 'Momentum research',
  created_at: '2026-01-01T00:00:00Z',
  dataset_id: 'dataset-real',
  factor_id: 'momentum',
  symbols: 6,
  revealed_stage: 'HOLDOUT',
  research_ic: 0.03,
  research_rank_ic: 0.04,
  factor_category: 'PRICE_VOLUME',
  data_source: 'MARKET',
  factor_origin: 'BUILT_IN',
  direction: 'HIGH',
}

function hypothesis(status: ResearchHypothesis['status'] = 'VALIDATED'): ResearchHypothesis {
  return {
    hypothesis_id: 'hypothesis-test',
    family_id: 'hypothesis-family-test',
    parent_hypothesis_id: null,
    revision: 1,
    title: 'Momentum stability hypothesis',
    description: 'Test a fixed, interpretable candidate without optimization.',
    dataset_id: 'dataset-real',
    dataset_fingerprint: 'sha256:dataset',
    universe: ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'GOOGL'],
    factor_research_ids: [factorResearchId],
    expected_relationship: 'Momentum may remain useful after explicit validation.',
    holding_horizon: '20 trading days',
    rebalance_idea: 'MONTHLY',
    risk_assumptions: ['Long-only', 'No optimization'],
    created_at: '2026-01-02T00:00:00Z',
    status,
    outcome: status === 'VALIDATED' ? 'MIXED' : 'INSUFFICIENT_EVIDENCE',
    created_with_known_stage: 'HOLDOUT',
    source_revealed_stages: { [factorResearchId]: 'HOLDOUT' },
    evidence: [
      {
        evidence_id: 'factor:evidence',
        source_type: 'FACTOR',
        source_id: factorResearchId,
        stage: 'RESEARCH',
        stance: 'SUPPORTING',
        label: 'Momentum · RESEARCH',
        detail: 'Saved Factor evidence known when the hypothesis was created.',
        metrics: { rank_ic: 0.04, coverage: 0.95 },
      },
      {
        evidence_id: 'portfolio:validation',
        source_type: 'PORTFOLIO',
        source_id: 'portfolio-test',
        stage: 'VALIDATION',
        stance: 'CONTRADICTING',
        label: 'Candidate Portfolio · VALIDATION',
        detail: 'Validation result from Portfolio Lab.',
        metrics: { net_return: -0.01, turnover: 0.6 },
      },
    ],
    candidate: {
      combination: 'RANK_AVERAGE',
      selection: 'TOP_PERCENT',
      top_percent: 20,
      weighting: 'EQUAL_WEIGHT',
      max_single_position_weight: 1,
      rebalance: 'MONTHLY',
      long_only: true,
      portfolio_research_id: 'portfolio-test',
    },
    lineage: {
      factor_research_ids: [factorResearchId],
      factor_ids: ['momentum'],
      relationship_ids: ['factor-relationship-test'],
      walk_forward_ids: ['walk-forward-test'],
      portfolio_research_id: 'portfolio-test',
      strategy_id: null,
      run_ids: [],
      trace_ids: [],
    },
    revision_reason: null,
    ai_boundary: (
      'Optional AI may summarize already revealed evidence but cannot calculate quantitative '
      + 'metrics, read sealed Holdout evidence, optimize, or claim alpha.'
    ),
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it('renders evidence, candidate contract, lineage, and neutral research-idea language', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const record = hypothesis()
  vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
    const url = String(input)
    const body = url.includes('/api/factor-research')
      ? [factor]
      : url.includes('/api/hypotheses/suggestions')
        ? [{
            label: 'RESEARCH IDEA',
            factor_research_ids: [factorResearchId],
            rationale: 'Investigate this evidence; this is not a recommendation.',
            source_relationship_id: 'factor-relationship-test',
          }]
        : [record]
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
  }))

  render(
    <I18nProvider>
      <DiscoveryWorkspacePage onOpenReplay={() => undefined} onRunComplete={() => undefined} />
    </I18nProvider>,
  )

  expect((await screen.findAllByText('Momentum stability hypothesis')).length).toBeGreaterThan(0)
  expect(screen.getByText('Evidence')).toBeInTheDocument()
  expect(screen.getByText('Supporting')).toBeInTheDocument()
  expect(screen.getByText('Contradicting')).toBeInTheDocument()
  expect(screen.getByText('RANK_AVERAGE')).toBeInTheDocument()
  expect(screen.getByText('Lineage')).toBeInTheDocument()
  expect(screen.getByText('RESEARCH IDEA')).toBeInTheDocument()
  expect(screen.queryByText(/Recommended Strategy/i)).not.toBeInTheDocument()
  expect(screen.getByText('Reveal Holdout')).toBeInTheDocument()
})

it('never reveals Holdout until the user explicitly clicks the Holdout action', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const before = hypothesis('VALIDATED')
  const after = { ...before, status: 'HOLDOUT_REVEALED' as const, outcome: 'MIXED' as const }
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    const isReveal = url.endsWith('/reveal-holdout') && init?.method === 'POST'
    const body = isReveal
      ? after
      : url.includes('/api/factor-research')
        ? [factor]
        : url.includes('/api/hypotheses/suggestions')
          ? []
          : [before]
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: isReveal ? 200 : 200,
      headers: { 'Content-Type': 'application/json' },
    }))
  })
  vi.stubGlobal('fetch', fetchMock)

  render(
    <I18nProvider>
      <DiscoveryWorkspacePage onOpenReplay={() => undefined} onRunComplete={() => undefined} />
    </I18nProvider>,
  )

  const button = await screen.findByRole('button', { name: 'Reveal Holdout' })
  expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith('/reveal-holdout'))).toBe(false)
  fireEvent.click(button)
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/hypotheses/hypothesis-test/reveal-holdout',
    expect.objectContaining({ method: 'POST' }),
  ))
})
