import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { diagnosisReport } from '../../test/fixtures/diagnosis'
import DiagnosePage from './DiagnosePage'

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

afterEach(() => vi.restoreAllMocks())

test('renders trace-bound train/test, real rerun comparisons, and deterministic observations', async () => {
  const onOpenReplay = vi.fn()
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(diagnosisReport))
  render(<DiagnosePage traceId="trace-custom" onOpenReplay={onOpenReplay} />)

  expect(await screen.findByRole('heading', { name: 'Diagnose' })).toBeInTheDocument()
  const trainTestHeading = screen.getByRole('heading', { name: 'Train / Test · 70 / 30' })
  expect(trainTestHeading).toBeInTheDocument()
  expect(trainTestHeading.closest('section')?.querySelector('.train-test-table')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: /Train and test Sharpe/ })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Cost stress' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Execution delay' })).toBeInTheDocument()
  expect(screen.getByText('20 bps')).toBeInTheDocument()
  const delayedExecution = screen.getByText('close(t+3)').closest('.dense-row')
  expect(delayedExecution).toHaveTextContent('2')
  expect(screen.getByText('Execution timing changes are measured, not inferred')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/api/diagnostics', expect.objectContaining({ method: 'POST' }))

  fireEvent.click(screen.getByRole('button', { name: 'Open in Replay →' }))
  expect(onOpenReplay).toHaveBeenCalledTimes(1)
})

test('shows explicit no-trace and retryable API error states', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ detail: 'Run context missing' }, 404))
  const { rerender } = render(<DiagnosePage traceId={null} onOpenReplay={() => undefined} />)
  expect(screen.getByText('Run a backtest from Strategy before diagnosing it.')).toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalled()

  rerender(<DiagnosePage traceId="trace-missing" onOpenReplay={() => undefined} />)
  expect(await screen.findByRole('heading', { name: 'Could not diagnose this trace.' })).toBeInTheDocument()
  expect(screen.getByText('POST /api/diagnostics returned 404. Run context missing')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
})

test('labels unsupported framework reruns instead of rendering empty diagnostics as results', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({
    ...diagnosisReport,
    lookback_sensitivity: [], cost_stress: [], execution_delay: [],
    support: { train_test: 'AVAILABLE', parameter_sensitivity: 'NOT_SUPPORTED', cost_stress: 'NOT_SUPPORTED', execution_delay: 'NOT_SUPPORTED' },
  }))
  render(<DiagnosePage traceId="trace-framework" onOpenReplay={() => undefined} />)
  expect(await screen.findAllByText('Not supported for this run')).toHaveLength(3)
  expect(screen.getByText('Execution delay cannot be inferred from persisted results.')).toBeInTheDocument()
  expect(screen.queryByRole('img', { name: /Train and test Sharpe/ })).not.toBeInTheDocument()
})
