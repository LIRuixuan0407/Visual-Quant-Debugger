import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import App from './App'
import { autopsyReport } from './test/fixtures/autopsy'
import { goldenTrace } from './test/fixtures/goldenTrace'
import { demoParameters, strategyDefinition } from './test/fixtures/strategyDefinition'
import type { RunDetail, RunListItem } from './types/run'

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const dataset = {
  dataset_id: 'pairs-sample-v1', name: 'Pairs Daily Sample', source_type: 'BUILT_IN',
  timezone: 'UTC', frequency: '1D', symbols: ['ASSET_A', 'ASSET_B'], fields: ['close'],
  row_count: 80, synchronized_bar_count: 40, start_time: '2024-01-01T16:00:00Z',
  end_time: '2024-02-09T16:00:00Z', created_at: '2024-01-01T00:00:00Z',
  content_fingerprint: 'sha256:data', source_timezone: 'UTC', column_mapping: {},
  quality: { status: 'VALID', rows: 80, symbols: 2, start: '2024-01-01T16:00:00Z', end: '2024-02-09T16:00:00Z', duplicates: 0, missing_required_values: 0, rows_reordered: 0, alignment_gaps: 0, timezone: 'UTC', issues: [] },
}

const created = {
  run_id: 'run-000000000000000000000001', run_fingerprint: 'sha256:run',
  trace_id: 'trace-custom', trace_version: '1.0', status: 'COMPLETED',
  summary: { total_return: 0.01, net_pnl: 1000, max_drawdown: -0.03, timeline_events: 40, signals: 6 },
}

const context = {
  run_id: 'run-000000000000000000000001', trace_id: 'trace-custom', strategy_id: 'pairs-trading', strategy_version: '0.1',
  strategy_fingerprint: 'sha256:strategy', dataset_id: 'pairs-sample-v1',
  dataset_fingerprint: 'sha256:data', parameters: demoParameters,
  execution_model: 'signal at close(t); execute at close(t+1)', execution_model_id: 'next-close', execution_model_version: '1.0', created_at: '2024-01-01T00:00:00Z',
  research_cutoff: null, status: 'COMPLETED',
}

const historicalRun: RunListItem = {
  run_id: 'run-000000000000000000000001', run_type: 'BACKTEST', trace_id: 'trace-custom', status: 'COMPLETED',
  created_at: '2024-01-01T00:00:00Z', completed_at: '2024-01-01T00:01:00Z',
  strategy_id: 'pairs-trading', strategy_name: 'Pairs Trading', strategy_fingerprint: 'sha256:strategy',
  dataset_id: 'pairs-sample-v1', dataset_name: 'Pairs Daily Sample', dataset_fingerprint: 'sha256:data',
  parameters: demoParameters, period: { start: '2024-01-01T16:00:00Z', end: '2024-02-09T16:00:00Z', cutoff: null },
  metrics: { total_return: 0.01, sharpe: 0.4, max_drawdown: -0.03, turnover: 0.2, trades: 3, final_equity: 101000, fees: 10, slippage: 8, net_pnl: 1000 },
  run_fingerprint: 'sha256:run', reproduced_from_run_id: null,
  annotations: { display_name: 'Persistent baseline', note: '', tags: ['baseline'] },
}

const historicalDetail: RunDetail = {
  manifest: {
    run_version: '1.0', run_id: historicalRun.run_id, run_type: 'BACKTEST', run_fingerprint: historicalRun.run_fingerprint,
    status: 'COMPLETED', created_at: historicalRun.created_at, completed_at: historicalRun.completed_at,
    strategy: { strategy_id: 'pairs-trading', name: 'Pairs Trading', version: '0.1', class_name: 'PairsTradingStrategy', source_fingerprint: 'sha256:strategy', original_source_path: '/app/strategies/pairs.py' },
    dataset: { dataset_id: 'pairs-sample-v1', name: 'Pairs Daily Sample', content_fingerprint: 'sha256:data', source_timezone: 'UTC' },
    period: historicalRun.period, parameters: demoParameters,
    execution_model: { execution_model_id: 'next-close', version: '1.0', description: 'signal at close(t); execute at close(t+1)' },
    engine: { python_version: '3.12', platform: 'Linux', vqd_version: '0.1.0' }, trace_version: '1.0', trace_id: 'trace-custom', metrics: historicalRun.metrics,
    artifacts: { strategy_source_sha256: 'sha256:source', trace_sha256: 'sha256:trace', diagnostics_sha256: null, pnl_autopsy_sha256: null }, failure: null, reproduced_from_run_id: null,
  },
  annotations: historicalRun.annotations, artifacts: { strategy_source: true, trace: true, diagnostics: false, pnl_autopsy: false }, integrity: 'VERIFIED', current_strategy_fingerprint: 'sha256:strategy', current_source_matches: true,
}

afterEach(() => {
  vi.restoreAllMocks()
  window.history.replaceState({}, '', '/')
})

function installApi(options: { trace?: unknown; backtest?: unknown; strategyFailureOnce?: boolean } = {}) {
  let strategyCalls = 0
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url === '/api/strategies') {
      strategyCalls += 1
      if (options.strategyFailureOnce && strategyCalls === 1) return jsonResponse({ detail: 'Definition service unavailable' }, 503)
      return jsonResponse([strategyDefinition])
    }
    if (url === '/api/datasets') return jsonResponse([dataset])
    if (url === '/api/compatibility-checks') return jsonResponse({ strategy_id: 'pairs-trading', dataset_id: 'pairs-sample-v1', compatible: true, required_fields: ['close'], provided_fields: ['close'], required_symbol_count: 2, provided_symbol_count: 2, required_symbols: ['ASSET_A', 'ASSET_B'], missing_symbols: [], minimum_history: 119, synchronized_bar_count: 40, reasons: [] })
    if (url === '/api/backtests' && init?.method === 'POST') return jsonResponse(options.backtest ?? created, 201)
    if (url.startsWith('/api/runs?')) return jsonResponse({ items: [historicalRun], total: 1, limit: 100, offset: 0 })
    if (url === `/api/runs/${historicalRun.run_id}`) return jsonResponse(historicalDetail)
    if (url.startsWith('/api/traces/') && url.endsWith('/context')) return jsonResponse(context)
    if (url.endsWith('/pnl-autopsy')) return jsonResponse(autopsyReport)
    if (url.startsWith('/api/traces/')) return jsonResponse(options.trace ?? goldenTrace)
    throw new Error(`Unexpected fetch ${url}`)
  })
}

test('loads Strategy and Dataset libraries before running anything', async () => {
  const fetchMock = installApi(); render(<App />)
  expect(screen.getByRole('heading', { name: 'Loading strategy anatomy…' })).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: 'Pairs Trading' })).toBeInTheDocument()
  expect(screen.getByLabelText('Lookback')).toHaveValue(demoParameters.lookback)
  expect(screen.getByLabelText('Strategy selector')).toHaveValue('pairs-trading')
  expect(screen.getByLabelText('Dataset selector')).toHaveValue('pairs-sample-v1')
  expect(fetchMock).toHaveBeenCalledWith('/api/strategies')
  expect(fetchMock).toHaveBeenCalledWith('/api/datasets')
})

test('keeps Diagnose empty until a Strategy run establishes the active trace', async () => {
  installApi(); render(<App />)
  await screen.findByRole('heading', { name: 'Pairs Trading' })
  fireEvent.click(screen.getByRole('button', { name: 'Diagnose' }))
  expect(screen.getByText('Run a backtest from Strategy before diagnosing it.')).toBeInTheDocument()
})

test('keeps P&L Autopsy bound to the latest Strategy run and jumps its event into Replay', async () => {
  const fetchMock = installApi(); render(<App />)
  await screen.findByRole('heading', { name: 'Pairs Trading' })
  fireEvent.click(screen.getByRole('button', { name: 'Run Backtest' }))
  await screen.findByRole('heading', { name: 'Backtest summary' })
  fireEvent.click(screen.getByRole('button', { name: 'P&L Autopsy' }))
  expect(await screen.findByRole('heading', { name: 'P&L Autopsy' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Replay trough' }))
  expect(await screen.findByRole('heading', { name: 'Hold' })).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/api/traces/trace-custom/pnl-autopsy')
  expect(fetchMock).toHaveBeenCalledWith('/api/traces/trace-custom')
})

test('opens Demo Replay using the backend preset and explicit dataset', async () => {
  const fetchMock = installApi({ backtest: { ...created, trace_id: 'trace-demo' } }); render(<App />)
  await screen.findByRole('heading', { name: 'Pairs Trading' })
  fireEvent.click(screen.getByRole('button', { name: 'Replay' }))
  expect(await screen.findByRole('heading', { name: 'Replay' })).toBeInTheDocument()
  const backtestCall = fetchMock.mock.calls.find(([url]) => url === '/api/backtests')
  const request = backtestCall?.[1] as RequestInit
  expect(JSON.parse(request.body as string)).toEqual({ strategy_id: 'pairs-trading', dataset_id: 'pairs-sample-v1', parameters: demoParameters })
  expect(fetchMock).toHaveBeenCalledWith('/api/traces/trace-demo')
})

test('opens the exact custom trace generated from Strategy instead of rerunning Demo', async () => {
  const customTrace = { ...goldenTrace, parameters: { ...goldenTrace.parameters, lookback: 10 } }
  const fetchMock = installApi({ trace: customTrace }); render(<App />)
  await screen.findByRole('heading', { name: 'Pairs Trading' })
  fireEvent.change(screen.getByLabelText('Lookback'), { target: { value: '10' } })
  fireEvent.click(screen.getByRole('button', { name: 'Run Backtest' }))
  await screen.findByRole('heading', { name: 'Backtest summary' })
  fireEvent.click(screen.getByRole('button', { name: /Open Replay/ }))
  expect(await screen.findByRole('heading', { name: 'Replay' })).toBeInTheDocument()
  expect(screen.getByLabelText('Trace parameters')).toHaveTextContent('lookback10')
  expect(fetchMock).toHaveBeenCalledWith('/api/traces/trace-custom')
})

test('shows precise Strategy Library and malformed Trace errors', async () => {
  const malformed = { trace_version: '1.0', timeline: [{ event_id: 'incomplete' }] }
  installApi({ strategyFailureOnce: true, trace: malformed, backtest: { ...created, trace_id: 'trace-bad' } })
  render(<App />)
  expect(await screen.findByRole('heading', { name: 'Could not load strategy definition.' })).toBeInTheDocument()
  expect(screen.getByText('GET /api/strategies returned 503. Definition service unavailable')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await screen.findByRole('heading', { name: 'Pairs Trading' })
  fireEvent.click(screen.getByRole('button', { name: 'Replay' }))
  expect(await screen.findByRole('heading', { name: 'Could not load trace.' })).toBeInTheDocument()
  expect(screen.getByText(/malformed Trace 1.0 payload/)).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument())
})

test('restores a persistent Run Inspector from a deep link after refresh', async () => {
  window.history.replaceState({}, '', `/runs/${historicalRun.run_id}`)
  const fetchMock = installApi()
  render(<App />)
  expect(await screen.findByRole('heading', { name: 'Research Runs' })).toBeInTheDocument()
  expect(await screen.findByText('RUN INSPECTOR')).toBeInTheDocument()
  expect(screen.getAllByText('Persistent baseline')).toHaveLength(2)
  expect(fetchMock).toHaveBeenCalledWith(`/api/runs/${historicalRun.run_id}`)
  expect(window.location.pathname).toBe(`/runs/${historicalRun.run_id}`)
  fireEvent.click(screen.getByRole('button', { name: 'Load config into Strategy' }))
  expect(await screen.findByRole('heading', { name: 'Pairs Trading' })).toBeInTheDocument()
  expect(screen.getByLabelText('Lookback')).toHaveValue(demoParameters.lookback)
  expect(window.location.pathname).toBe('/')
})
