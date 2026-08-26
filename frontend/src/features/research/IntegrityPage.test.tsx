import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { HypothesisIntegrityReport, WorkspaceIntegrityReport } from '../../types/research'
import IntegrityPage from './IntegrityPage'

const disclosure = 'Research Integrity Guardrails audit recorded lineage, dataset revisions, time boundaries, and strategy semantics against the append-only research ledger.'

const report: HypothesisIntegrityReport = {
  report_version: '1.0',
  hypothesis_id: 'hypothesis-complete',
  family_id: 'hypothesis-family',
  title: 'Diversified signal stability',
  revision: 3,
  lifecycle_status: 'STRATEGY_CREATED',
  checked_at: '2026-08-27T00:00:00Z',
  findings: [
    { code: 'POST_HOLDOUT_MODIFICATION', severity: 'VIOLATION', subject: 'hypothesis-complete', reason: 'This experiment family was modified after Holdout had already been revealed.', evidence: ['CREATE_REVISION:hypothesis-late@2026-08-22T00:00:00Z'] },
    { code: 'FUTURE_DATA_LEAK', severity: 'WARNING', subject: 'hypothesis-complete', reason: 'Research or Run boundaries reach beyond the recorded dataset coverage.', evidence: ['run-0123456789abcdef01234567 period ends after the dataset coverage end'] },
    { code: 'DATASET_SILENT_CHANGE', severity: 'PASS', subject: 'hypothesis-complete', reason: 'The current dataset revision still matches the Hypothesis.', evidence: [] },
    { code: 'STRATEGY_SEMANTIC_MISMATCH', severity: 'PASS', subject: 'hypothesis-complete', reason: 'The Portfolio, Native Strategy, and attached Runs still express the recorded hypothesis semantics.', evidence: [] },
    { code: 'MISSING_LINEAGE', severity: 'PASS', subject: 'hypothesis-complete', reason: 'Every lineage reference resolves to a stored record.', evidence: [] },
    { code: 'MISSING_REVISION', severity: 'PASS', subject: 'hypothesis-complete', reason: 'All lineage records carry an exact revision identity.', evidence: [] },
  ],
  violation_count: 1,
  warning_count: 1,
  overall_status: 'VIOLATION',
  disclosure,
}

const overview: WorkspaceIntegrityReport = {
  report_version: '1.0',
  generated_at: '2026-08-27T00:00:00Z',
  hypotheses: [
    { hypothesis_id: 'hypothesis-complete', family_id: 'hypothesis-family', title: report.title, revision: 3, lifecycle_status: 'STRATEGY_CREATED', overall_status: 'VIOLATION', violation_count: 1, warning_count: 1 },
    { hypothesis_id: 'hypothesis-clean', family_id: 'hypothesis-family-clean', title: 'Clean momentum study', revision: 1, lifecycle_status: 'DRAFT', overall_status: 'PASS', violation_count: 0, warning_count: 0 },
  ],
  overall_status: 'VIOLATION',
  total_violations: 1,
  total_warnings: 1,
  disclosure,
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it('shows the workspace overview and explicit guardrail statuses with reasons', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input)
    const body = url === '/api/research-integrity' ? overview : report
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<I18nProvider><IntegrityPage /></I18nProvider>)

  expect(await screen.findByRole('heading', { name: 'Research Integrity' })).toBeInTheDocument()
  expect(screen.getByText('Clean momentum study')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Guardrail checks' })).toBeInTheDocument()
  expect(screen.getByText('POST HOLDOUT MODIFICATION')).toBeInTheDocument()
  expect(screen.getByText(/modified after Holdout had already been revealed/)).toBeInTheDocument()
  expect(screen.getByText(/CREATE_REVISION:hypothesis-late/)).toBeInTheDocument()
  const severities = screen.getAllByText('VIOLATION')
  expect(severities.length).toBeGreaterThan(0)
  expect(screen.getByText('FUTURE DATA LEAK')).toBeInTheDocument()
  expect(screen.queryByText(/winner/i)).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /Clean momentum study/ }))
  await waitFor(() => {
    const lastCall = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
    expect(String(lastCall?.[0])).toBe('/api/research-integrity/hypothesis-clean')
  })
})

it('shows an empty state when no hypotheses exist', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const empty: WorkspaceIntegrityReport = { ...overview, hypotheses: [], overall_status: 'PASS', total_violations: 0, total_warnings: 0 }
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify(empty), { status: 200, headers: { 'Content-Type': 'application/json' } }))))

  render(<I18nProvider><IntegrityPage /></I18nProvider>)

  expect(await screen.findByText('No hypotheses to audit yet.')).toBeInTheDocument()
  expect(screen.getByText('Create a hypothesis in Strategy Discovery first; the guardrails audit recorded research automatically.')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Guardrail checks' })).not.toBeInTheDocument()
})
