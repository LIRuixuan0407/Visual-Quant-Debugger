import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { DataAuditDetail, DataAuditSummary } from '../../types/dataAudit'
import DataAuditPage from './DataAuditPage'

const summary: DataAuditSummary = {
  audit_id: 'data-audit-0123456789abcdef0123',
  root_type: 'FACTOR_RESEARCH',
  root_id: 'factor-research-momentum',
  created_at: '2026-08-28T08:00:00Z',
  status: 'VIOLATION',
  finding_count: 4,
  violation_count: 1,
  warning_count: 1,
}

const detail: DataAuditDetail = {
  audit: {
    audit_version: '1.0',
    audit_id: summary.audit_id,
    root_type: summary.root_type,
    root_id: summary.root_id,
    created_at: summary.created_at,
    source_fingerprints: {
      'factor_research:factor-research-momentum': 'sha256:factor',
      'dataset:market-dataset': 'sha256:dataset',
    },
    status: summary.status,
    findings: [
      { code: 'DATASET_DUPLICATES', severity: 'PASS', subject: 'market-dataset', reason: 'No duplicate symbol/timestamp rows were reported by Dataset validation.', evidence: [], checked_count: 1200, affected_count: 0 },
      { code: 'DEPENDENCY_LOOK_AHEAD', severity: 'VIOLATION', subject: summary.root_id, reason: 'Some dependencies were used before they became available.', evidence: ['price:AAPL@2024-01-03 available_at=2024-01-04 used_at=2024-01-03'], checked_count: 4800, affected_count: 1 },
      { code: 'AVAILABLE_FUTURE_TARGET_OUTSIDE_STAGE', severity: 'INFO', subject: summary.root_id, reason: 'Some future targets exist outside the stage but were excluded.', evidence: ['RESEARCH 20D AAPL@2024-04-01'], checked_count: 3600, affected_count: 4 },
      { code: 'UNIVERSE_SURVIVORSHIP_DISCLOSURE', severity: 'WARNING', subject: 'universe-static', reason: 'The selected Universe is not survivorship-bias free.', evidence: ['Static current members'], checked_count: 1, affected_count: 1 },
    ],
    checked_observations: 1200,
    checked_dependencies: 4800,
    checked_future_returns: 3600,
    checked_fundamental_inputs: 600,
    disclosures: ['Backend evidence is immutable.'],
  },
  source_state: 'MATCHES',
  current_source_fingerprints: {
    'factor_research:factor-research-momentum': 'sha256:factor',
    'dataset:market-dataset': 'sha256:dataset',
  },
}

afterEach(() => {
  window.history.replaceState({}, '', '/')
  window.localStorage.clear()
})

it('renders immutable audit counts, grouped findings, severity, evidence, and source drift', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const verify = vi.fn().mockResolvedValue({
    audit_id: summary.audit_id,
    source_state: 'CHANGED',
    recorded_source_fingerprints: detail.audit.source_fingerprints,
    current_source_fingerprints: { 'dataset:market-dataset': 'sha256:new' },
    newer_dataset_revision_available: true,
    latest_dataset_id: 'market-dataset-r2',
    latest_dataset_revision: 2,
  })
  render(<I18nProvider><DataAuditPage services={{
    list: vi.fn().mockResolvedValue([summary]),
    get: vi.fn().mockResolvedValue(detail),
    verify,
  }} /></I18nProvider>)

  expect(await screen.findByRole('heading', { name: 'Data Quality & PIT Audit' })).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: 'PIT Dependencies' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Future Return' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Universe' })).toBeInTheDocument()
  expect(screen.getByText('4800')).toBeInTheDocument()
  expect(screen.getByText('DEPENDENCY LOOK AHEAD')).toBeInTheDocument()
  expect(screen.getByText(/available_at=2024-01-04/)).toBeInTheDocument()
  expect(screen.getByText('VIOLATION', { selector: '.audit-severity' })).toBeInTheDocument()
  expect(screen.getByText('MATCHES')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Verify current source' }))
  await waitFor(() => expect(verify).toHaveBeenCalledWith(summary.audit_id))
  expect(await screen.findByText('CHANGED')).toBeInTheDocument()
  expect(screen.getByText('NEWER REVISION AVAILABLE')).toBeInTheDocument()
  expect(screen.getByText(/r2 · market-dataset-r2/)).toBeInTheDocument()
})

it('creates an audit for an explicit recorded root', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const create = vi.fn().mockResolvedValue(detail)
  render(<I18nProvider><DataAuditPage services={{
    list: vi.fn().mockResolvedValue([]),
    create,
  }} /></I18nProvider>)

  await screen.findByText('No Data Audits yet.')
  fireEvent.change(screen.getByLabelText('Root type'), { target: { value: 'FACTOR_RESEARCH' } })
  fireEvent.change(screen.getByLabelText('Root ID'), { target: { value: summary.root_id } })
  fireEvent.click(screen.getByRole('button', { name: 'Run Data Audit' }))

  await waitFor(() => expect(create).toHaveBeenCalledWith('FACTOR_RESEARCH', summary.root_id))
  expect(await screen.findByText('Backend evidence is immutable.')).toBeInTheDocument()
})

it('shows loading, empty, and backend error states without inventing evidence', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  let rejectList: (reason: Error) => void = () => undefined
  const pending = new Promise<DataAuditSummary[]>((_, reject) => { rejectList = reject })
  render(<I18nProvider><DataAuditPage services={{ list: vi.fn(() => pending) }} /></I18nProvider>)

  expect(screen.getByText('Loading Data Audits…')).toBeInTheDocument()
  rejectList(new Error('Audit store is unavailable.'))
  expect(await screen.findByRole('alert')).toHaveTextContent('Audit store is unavailable.')
  expect(screen.queryByText('DEPENDENCY LOOK AHEAD')).not.toBeInTheDocument()
})

it('provides the audit workspace in Chinese', async () => {
  window.localStorage.setItem('vqd-language', 'zh')
  render(<I18nProvider><DataAuditPage services={{ list: vi.fn().mockResolvedValue([]) }} /></I18nProvider>)

  expect(await screen.findByRole('heading', { name: '数据质量与时点审计' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '运行数据审计' })).toBeInTheDocument()
  expect(screen.getByText('还没有数据审计。')).toBeInTheDocument()
})
