import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { StrategyDriftReport, StrategyDriftSummary } from '../../types/strategyDrift'
import StrategyDriftPage from './StrategyDriftPage'

const dimensions = ['FACTOR', 'SIGNAL', 'TURNOVER', 'EXPOSURE', 'PERFORMANCE'] as const

const report: StrategyDriftReport = {
  drift_report_id: 'drift-report-0123456789abcdef012345',
  drift_rule_version: '1.0',
  baseline_type: 'RUN',
  baseline_id: 'run-baseline',
  observed_type: 'PAPER_RUN',
  observed_id: 'run-paper',
  created_at: '2026-08-29T02:00:00Z',
  window_bars: 20,
  baseline: {
    source_type: 'RUN', source_id: 'run-baseline', resolved_run_id: 'run-baseline', trace_id: 'trace-baseline', strategy_id: 'pairs-trading', strategy_fingerprint: 'sha256:same', parameters: { lookback: 20 }, execution_model: 'next-close@1.0', runtime: 'python', dataset_id: 'dataset-r1', dataset_revision: 'sha256:r1', sample_size: 100, observed_until: '2025-12-31T00:00:00Z', status: 'COMPLETED',
  },
  observed: {
    source_type: 'PAPER_RUN', source_id: 'run-paper', resolved_run_id: 'run-paper', trace_id: 'trace-paper', strategy_id: 'pairs-trading', strategy_fingerprint: 'sha256:same', parameters: { lookback: 20 }, execution_model: 'paper-next-close@1.0', runtime: 'python', dataset_id: 'recorded-feed:paper', dataset_revision: 'sha256:paper', sample_size: 40, observed_until: '2026-08-29T01:30:00Z', status: 'COMPLETED',
  },
  comparability: 'CONTEXTUALLY_COMPARABLE',
  comparability_checks: [
    { field: 'strategy_id', baseline_value: 'pairs-trading', observed_value: 'pairs-trading', same: true, blocking: true },
    { field: 'strategy_fingerprint', baseline_value: 'sha256:same', observed_value: 'sha256:same', same: true, blocking: true },
    { field: 'parameters', baseline_value: '{}', observed_value: '{}', same: true, blocking: true },
    { field: 'execution_model', baseline_value: 'next-close@1.0', observed_value: 'paper-next-close@1.0', same: false, blocking: false },
    { field: 'runtime', baseline_value: 'python', observed_value: 'python', same: true, blocking: false },
  ],
  overall_status: 'DRIFT',
  dimensions: dimensions.map((dimension) => ({
    dimension,
    status: dimension === 'FACTOR' ? 'INSUFFICIENT_EVIDENCE' : dimension === 'SIGNAL' ? 'DRIFT' : 'STABLE',
    metrics: [{ metric: `${dimension.toLowerCase()}_metric`, baseline_value: 1, observed_value: dimension === 'SIGNAL' ? 4 : 1.1, relative_change: 0.1, normalized_distance: dimension === 'SIGNAL' ? 2.5 : 0.2, status: dimension === 'FACTOR' ? 'INSUFFICIENT_EVIDENCE' : dimension === 'SIGNAL' ? 'DRIFT' : 'STABLE' }],
    first_drift_at: dimension === 'SIGNAL' ? '2026-08-29T01:30:00Z' : null,
    first_drift_event_id: dimension === 'SIGNAL' ? 'event-first-drift' : null,
    evidence: dimension === 'FACTOR' ? ['No unique explicit Factor Research lineage and canonical recorded Factor evidence were available.'] : ['Trace evidence'],
  })),
  timeline: [
    { window_index: 1, start_at: '2026-08-29T00:00:00Z', end_at: '2026-08-29T00:30:00Z', end_event_id: 'event-window-1', sample_size: 20, complete: true, dimensions: dimensions.map((dimension) => ({ dimension, status: dimension === 'FACTOR' ? 'INSUFFICIENT_EVIDENCE' : 'STABLE', maximum_normalized_distance: dimension === 'FACTOR' ? null : 0.5 })) },
    { window_index: 2, start_at: '2026-08-29T00:31:00Z', end_at: '2026-08-29T01:30:00Z', end_event_id: 'event-first-drift', sample_size: 20, complete: true, dimensions: dimensions.map((dimension) => ({ dimension, status: dimension === 'SIGNAL' ? 'DRIFT' : dimension === 'FACTOR' ? 'INSUFFICIENT_EVIDENCE' : 'STABLE', maximum_normalized_distance: dimension === 'SIGNAL' ? 2.5 : null })) },
  ],
  first_drift_at: '2026-08-29T01:30:00Z',
  first_drift_dimension: 'SIGNAL',
  first_drift_event_id: 'event-first-drift',
  disclosure: 'Strategy Drift detects and locates deterministic distribution and behavior changes in recorded evidence. It does not explain causes, predict future performance, optimize thresholds, or declare that a strategy has permanently failed.',
}

const summary: StrategyDriftSummary = {
  drift_report_id: report.drift_report_id,
  baseline_type: report.baseline_type,
  baseline_id: report.baseline_id,
  observed_type: report.observed_type,
  observed_id: report.observed_id,
  created_at: report.created_at,
  comparability: report.comparability,
  overall_status: report.overall_status,
  first_drift_at: report.first_drift_at,
  first_drift_dimension: report.first_drift_dimension,
  sample_size: report.observed.sample_size,
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
  window.history.replaceState({}, '', '/')
})

it('shows five drift dimensions, timeline, first drift, insufficient evidence, and Replay deep link', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/strategy-drift') return new Response(JSON.stringify([summary]), { status: 200 })
    if (url.includes(report.drift_report_id)) return new Response(JSON.stringify(report), { status: 200 })
    return new Response('{}', { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const openReplay = vi.fn()

  render(<I18nProvider><StrategyDriftPage initialReportId={report.drift_report_id} onOpenReplay={openReplay} onOpenForward={vi.fn()} /></I18nProvider>)

  expect(await screen.findByRole('heading', { name: 'Overall Drift' })).toBeInTheDocument()
  for (const dimension of dimensions) expect(screen.getByRole('heading', { name: `${dimension} Drift` })).toBeInTheDocument()
  expect(screen.getByText('Insufficient evidence')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Drift Timeline' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: /^SIGNAL ·/ })).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Open Replay' }))
  expect(openReplay).toHaveBeenCalledWith('trace-paper', 'event-first-drift')
})

it('creates reports from source IDs without sending backend-derived drift fields', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const requests: RequestInit[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/strategy-drift' && init?.method === 'POST') {
      requests.push(init)
      return new Response(JSON.stringify(report), { status: 201 })
    }
    if (url === '/api/strategy-drift') return new Response(JSON.stringify([]), { status: 200 })
    return new Response(JSON.stringify(report), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<I18nProvider><StrategyDriftPage onOpenReplay={vi.fn()} onOpenForward={vi.fn()} /></I18nProvider>)
  await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  fireEvent.change(screen.getByPlaceholderText('run-… / snapshot-…'), { target: { value: 'run-baseline' } })
  fireEvent.change(screen.getByPlaceholderText('forward-… / paper-… / run-…'), { target: { value: 'run-paper' } })
  fireEvent.change(screen.getByLabelText('Observed type'), { target: { value: 'PAPER_RUN' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Report' }))

  await waitFor(() => expect(requests).toHaveLength(1))
  const payload = JSON.parse(String(requests[0].body)) as Record<string, unknown>
  expect(payload).toEqual({ baseline_type: 'RUN', baseline_id: 'run-baseline', observed_type: 'PAPER_RUN', observed_id: 'run-paper', window_bars: 20 })
  expect(payload).not.toHaveProperty('overall_status')
  expect(payload).not.toHaveProperty('first_drift_at')
})
