import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { autopsyReport } from '../../test/fixtures/autopsy'
import AutopsyPage from './AutopsyPage'

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

afterEach(() => vi.restoreAllMocks())

test('renders reconciled waterfall, period tabs, ranked trades, and drawdown Replay links', async () => {
  const onReplay = vi.fn()
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(autopsyReport))
  render(<AutopsyPage traceId="trace-custom" onReplay={onReplay} />)

  expect(await screen.findByRole('heading', { name: 'P&L Autopsy' })).toBeInTheDocument()
  expect(screen.getByText('Reconciled')).toBeInTheDocument()
  expect(screen.getByLabelText('Gross P&L less fees and slippage equals net P&L')).toHaveTextContent('Gross P&L')
  expect(screen.getByRole('table', { name: 'UTC period P&L attribution' })).toHaveTextContent('2024-01')

  fireEvent.click(screen.getByRole('tab', { name: 'quarter' }))
  expect(screen.getByRole('table')).toHaveTextContent('2024 Q1')
  fireEvent.click(screen.getByRole('tab', { name: 'Worst' }))
  expect(screen.getByText('−$240.00')).toBeInTheDocument()
  expect(screen.getByText('Unrecovered')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Replay entry →' }))
  expect(onReplay).toHaveBeenCalledWith('timeline-000013')
  fireEvent.click(screen.getByRole('button', { name: 'Replay trough' }))
  expect(onReplay).toHaveBeenCalledWith('timeline-000013')
})

test('shows no-active-trace and malformed API states without hidden demo data', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ report_version: 'bad' }))
  const { rerender } = render(<AutopsyPage traceId={null} onReplay={() => undefined} />)
  expect(screen.getByText('Run a backtest from Strategy before opening its P&L Autopsy.')).toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalled()

  rerender(<AutopsyPage traceId="trace-bad" onReplay={() => undefined} />)
  expect(await screen.findByRole('heading', { name: 'Could not open P&L Autopsy.' })).toBeInTheDocument()
  expect(screen.getByText(/malformed P&L Autopsy report/)).toBeInTheDocument()
})
