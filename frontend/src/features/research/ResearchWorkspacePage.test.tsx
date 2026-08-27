import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { ResearchWorkspace, ResearchWorkspaceSummary, WorkspaceAction } from '../../types/research'
import ResearchWorkspacePage from './ResearchWorkspacePage'

const stageKeys = ['DATA', 'FACTOR', 'PORTFOLIO', 'VALIDATION', 'HYPOTHESIS', 'STRATEGY', 'RUN'] as const

function makeWorkspace(action: WorkspaceAction = 'BUILD_CANDIDATE'): ResearchWorkspace {
  const completed = action === 'BUILD_CANDIDATE' ? 2 : action === 'REVEAL_HOLDOUT' ? 4 : 6
  return {
    workspace_version: '1.0',
    idea_id: 'hypothesis-idea-27',
    family_id: 'family-27',
    parent_idea_id: null,
    title: 'Stable multi-factor idea',
    description: 'Combine independent evidence without changing the underlying engines.',
    revision: 2,
    lifecycle_status: action === 'REVEAL_HOLDOUT' ? 'VALIDATED' : action === 'RUN_BACKTEST' ? 'STRATEGY_CREATED' : 'DRAFT',
    outcome: 'OPEN',
    expected_relationship: 'Higher composite score should precede higher forward return.',
    holding_horizon: '5 trading days',
    rebalance_idea: 'MONTHLY',
    risk_assumptions: ['Point-in-time inputs only'],
    created_at: '2026-08-27T00:00:00Z',
    updated_at: '2026-08-27T01:00:00Z',
    dataset_id: 'dataset-daily',
    dataset_name: 'Daily research sample',
    dataset_revision: 'dataset-revision-27',
    dataset_period: ['2022-01-01T00:00:00Z', '2025-12-31T00:00:00Z'],
    factors: [{ research_id: 'factor-research-27', factor_id: 'momentum', name: 'Momentum evidence', revealed_stage: 'RESEARCH', revision: 'factor-revision-27' }],
    portfolio: action === 'BUILD_CANDIDATE' ? null : { portfolio_research_id: 'portfolio-27', name: 'Candidate portfolio', revealed_stage: 'VALIDATION', combination: 'WEIGHTED_SUM', rebalance: 'MONTHLY', net_return: 0.18, turnover: 0.24 },
    strategy: action === 'RUN_BACKTEST' ? { strategy_id: 'research-portfolio-27', source_fingerprint: 'strategy-revision-27' } : null,
    runs: [],
    snapshot_ids: ['snapshot-27'],
    integrity_status: 'PASS',
    integrity_violations: 0,
    integrity_warnings: 0,
    stages: stageKeys.map((key, index) => ({
      key,
      status: index < completed ? 'COMPLETE' : index === completed ? 'CURRENT' : 'BLOCKED',
      summary: index < completed ? `${key} evidence is recorded.` : `${key} remains pending.`,
      artifact_ids: index < completed ? [`${key.toLowerCase()}-27`] : [],
    })),
    next_action: {
      action,
      label: action === 'BUILD_CANDIDATE' ? 'Create candidate Portfolio' : action === 'REVEAL_HOLDOUT' ? 'Reveal Holdout' : 'Run Backtest',
      requires_explicit_confirmation: action === 'REVEAL_HOLDOUT',
    },
    disclosure: 'The unified Research Workspace is a read model over existing research records; it does not reveal Holdout automatically or select a winner.',
  }
}

function summaryFor(workspace: ResearchWorkspace): ResearchWorkspaceSummary {
  return {
    idea_id: workspace.idea_id,
    family_id: workspace.family_id,
    title: workspace.title,
    revision: workspace.revision,
    lifecycle_status: workspace.lifecycle_status,
    outcome: workspace.outcome,
    dataset_id: workspace.dataset_id,
    factor_count: workspace.factors.length,
    completed_stage_count: workspace.stages.filter((stage) => stage.status === 'COMPLETE').length,
    total_stage_count: 7,
    integrity_status: workspace.integrity_status,
    next_action: workspace.next_action,
    updated_at: workspace.updated_at,
  }
}

const handlers = {
  onIdeaChange: vi.fn(),
  onOpenData: vi.fn(),
  onOpenFactors: vi.fn(),
  onOpenPortfolio: vi.fn(),
  onOpenHypothesis: vi.fn(),
  onOpenStrategy: vi.fn(),
  onOpenRun: vi.fn(),
  onOpenReplay: vi.fn(),
  onOpenIntegrity: vi.fn(),
  onOpenSnapshots: vi.fn(),
  onRunComplete: vi.fn(),
}

function renderWorkspace(workspace: ResearchWorkspace) {
  render(<I18nProvider><ResearchWorkspacePage {...handlers} initialIdeaId={workspace.idea_id} /></I18nProvider>)
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  window.localStorage.clear()
})

it('renders one Idea as a continuous seven-stage workspace using backend evidence', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const workspace = makeWorkspace()
  vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => Promise.resolve(new Response(
    JSON.stringify(String(input) === '/api/research-workspaces' ? [summaryFor(workspace)] : workspace),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  ))))

  renderWorkspace(workspace)

  expect(await screen.findByRole('heading', { name: 'Research Workspace' })).toBeInTheDocument()
  expect(handlers.onIdeaChange).toHaveBeenCalledWith('hypothesis-idea-27')
  expect(screen.getAllByText('Stable multi-factor idea').length).toBeGreaterThan(0)
  for (const stage of ['Data', 'Factor', 'Portfolio', 'Validation', 'Hypothesis', 'Strategy', 'Run']) {
    expect(screen.getByText(stage, { selector: '.workspace-stage-rail b' })).toBeInTheDocument()
  }
  expect(screen.getByRole('heading', { name: 'Create candidate Portfolio' })).toBeInTheDocument()
  expect(screen.getByText('Daily research sample')).toBeInTheDocument()
  expect(screen.getByText('Momentum evidence')).toBeInTheDocument()
  expect(screen.queryByText(/winner selection/i)).not.toBeInTheDocument()
})

it('requires a second explicit confirmation before revealing Holdout', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const workspace = makeWorkspace('REVEAL_HOLDOUT')
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => Promise.resolve(new Response(
    JSON.stringify(String(input) === '/api/research-workspaces' && init?.method !== 'POST' ? [summaryFor(workspace)] : workspace),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )))
  vi.stubGlobal('fetch', fetchMock)
  renderWorkspace(workspace)

  fireEvent.click(await screen.findByRole('button', { name: 'Reveal Holdout' }))
  expect(fetchMock.mock.calls.some((call) => call[1]?.method === 'POST')).toBe(false)
  expect(screen.getByText(/Holdout is sealed/)).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Confirm Reveal Holdout' }))
  await waitFor(() => expect(fetchMock.mock.calls.some((call) => String(call[0]) === '/api/hypotheses/hypothesis-idea-27/reveal-holdout' && call[1]?.method === 'POST')).toBe(true))
})

it('reuses the existing backtest and Run attachment APIs', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const workspace = makeWorkspace('RUN_BACKTEST')
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/backtests') return Promise.resolve(new Response(JSON.stringify({ run_id: 'run-27', run_fingerprint: 'run-fingerprint-27', trace_id: 'trace-27', trace_version: '1.0', status: 'COMPLETED', summary: null }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    return Promise.resolve(new Response(JSON.stringify(url === '/api/research-workspaces' && init?.method !== 'POST' ? [summaryFor(workspace)] : workspace), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)
  renderWorkspace(workspace)

  fireEvent.click(await screen.findByRole('button', { name: 'Run Backtest' }))

  await waitFor(() => expect(fetchMock.mock.calls.some((call) => String(call[0]) === '/api/hypotheses/hypothesis-idea-27/runs' && call[1]?.method === 'POST')).toBe(true))
  expect(handlers.onRunComplete).toHaveBeenCalledWith('trace-27', 'run-27')
  const backtestCall = fetchMock.mock.calls.find((call) => String(call[0]) === '/api/backtests')
  expect(JSON.parse(String(backtestCall?.[1]?.body))).toEqual({ strategy_id: 'research-portfolio-27', dataset_id: 'dataset-daily', parameters: {} })
})
