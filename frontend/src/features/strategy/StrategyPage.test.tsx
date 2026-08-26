import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'

import type { BacktestCreated } from '../../types/trace'
import type { DatasetDefinition } from '../../types/dataset'
import { demoParameters, strategyDefinition, strategyDefaults } from '../../test/fixtures/strategyDefinition'
import StrategyPage from './StrategyPage'

const created: BacktestCreated = {
  run_id: 'run-000000000000000000000001',
  run_fingerprint: 'sha256:run',
  trace_id: 'trace-custom',
  trace_version: '1.0',
  summary: { total_return: 0.0123, net_pnl: 1230, max_drawdown: -0.04, timeline_events: 40, signals: 6 },
}

test('renders the backend-driven anatomy and inspects the Z-score node', () => {
  render(<StrategyPage definition={strategyDefinition} onOpenReplay={() => undefined} />)

  for (const name of ['Market Data', 'Hedge Ratio', 'Spread', 'Z-score', 'Signal Rules', 'Execution']) {
    expect(screen.getByRole('button', { name: new RegExp(name) })).toBeInTheDocument()
  }
  fireEvent.click(screen.getByRole('button', { name: /Z-score/ }))
  expect(screen.getByRole('heading', { name: 'Z-score' })).toBeInTheDocument()
  expect(screen.getByText('Calculation details').closest('details')).toHaveAttribute('open')
  expect(screen.getByText('(spread - rolling_mean) / rolling_std')).toBeInTheDocument()
  expect(screen.getAllByText('Lookback').length).toBeGreaterThan(1)
  expect(screen.getByText('Entry decisions / Exit decisions')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /Execution/ }))
  expect(screen.getByText('close(t)')).toBeInTheDocument()
  expect(screen.getByText('close(t+1)')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /Import Strategy/ }))
  expect(screen.getByRole('heading', { name: 'Register an existing VQDStrategy' })).toBeInTheDocument()
  expect(screen.getByLabelText('Strategy source path')).toBeInTheDocument()
})

test('preset and parameter edits change only the draft until Run Backtest', () => {
  const runBacktest = vi.fn<(parameters: typeof strategyDefaults) => Promise<BacktestCreated>>().mockResolvedValue(created)
  render(<StrategyPage definition={strategyDefinition} onOpenReplay={() => undefined} runBacktest={runBacktest} />)

  fireEvent.change(screen.getByLabelText('Strategy preset'), { target: { value: 'demo-active-signals' } })
  expect(screen.getByLabelText('Lookback')).toHaveValue(demoParameters.lookback)
  expect(screen.getByText('Parameters changed')).toBeInTheDocument()
  expect(runBacktest).not.toHaveBeenCalled()

  fireEvent.change(screen.getByLabelText('Lookback'), { target: { value: '10' } })
  expect(screen.getByLabelText('Lookback')).toHaveValue(10)
  expect(runBacktest).not.toHaveBeenCalled()
})

test('blocks invalid parameters and shows specific validation errors', () => {
  render(<StrategyPage definition={strategyDefinition} onOpenReplay={() => undefined} />)
  const runButton = screen.getByRole('button', { name: 'Run Backtest' })

  fireEvent.change(screen.getByLabelText('Fee'), { target: { value: '-1' } })
  expect(screen.getByText('Fee must be at least 0.')).toBeInTheDocument()
  expect(runButton).toBeDisabled()

  fireEvent.change(screen.getByLabelText('Fee'), { target: { value: '5' } })
  fireEvent.change(screen.getByLabelText('Lookback'), { target: { value: '1' } })
  expect(screen.getByText('Lookback must be at least 2.')).toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('Lookback'), { target: { value: '10' } })
  fireEvent.change(screen.getByLabelText('Exit Z'), { target: { value: '2' } })
  expect(screen.getByText('Exit Z must be smaller than Entry Z.')).toBeInTheDocument()
})

test('runs the exact draft, retains last-run summary, and opens its trace', async () => {
  const runBacktest = vi.fn<(parameters: typeof strategyDefaults) => Promise<BacktestCreated>>().mockResolvedValue(created)
  const onOpenReplay = vi.fn()
  render(<StrategyPage definition={strategyDefinition} onOpenReplay={onOpenReplay} runBacktest={runBacktest} />)

  fireEvent.change(screen.getByLabelText('Lookback'), { target: { value: '10' } })
  fireEvent.click(screen.getByRole('button', { name: 'Run Backtest' }))
  expect(await screen.findByRole('heading', { name: 'Backtest summary' })).toBeInTheDocument()
  expect(runBacktest).toHaveBeenCalledWith({ ...strategyDefaults, lookback: 10 })
  expect(screen.getByText('Trace matches this draft')).toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('Lookback'), { target: { value: '12' } })
  expect(screen.getByText('Parameters changed')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Open Replay/ }))
  expect(onOpenReplay).toHaveBeenCalledWith('trace-custom')
})

test('retains the draft and reports a precise backtest failure', async () => {
  const runBacktest = vi.fn<(parameters: typeof strategyDefaults) => Promise<BacktestCreated>>()
    .mockRejectedValue(new Error('POST /api/backtests returned 503. Engine unavailable'))
  render(<StrategyPage definition={strategyDefinition} onOpenReplay={() => undefined} runBacktest={runBacktest} />)

  fireEvent.change(screen.getByLabelText('Lookback'), { target: { value: '10' } })
  fireEvent.click(screen.getByRole('button', { name: 'Run Backtest' }))

  expect(await screen.findByText('Backtest failed')).toBeInTheDocument()
  expect(screen.getByText('POST /api/backtests returned 503. Engine unavailable')).toBeInTheDocument()
  expect(screen.getByLabelText('Lookback')).toHaveValue(10)
})

test('shows registered strategies, generated parameters, requirements, compatibility, and runs without strategy-specific UI', async () => {
  const nativeDefinition = {
    ...strategyDefinition,
    strategy_id: 'user.sma-cross', name: 'SMA Cross', trace_fidelity: 'FULL' as const,
    parameters: [
      { ...strategyDefinition.parameters[0], key: 'fast_window', label: 'Fast Window', default_value: 3, minimum: 1 },
      { ...strategyDefinition.parameters[0], key: 'slow_window', label: 'Slow Window', default_value: 5, minimum: 2 },
      strategyDefinition.parameters[3], strategyDefinition.parameters[4],
    ],
    presets: [{ preset_id: 'strategy-default', name: 'Strategy Default', description: 'Native defaults', parameters: { fast_window: 3, slow_window: 5, fee_bps: 5, slippage_bps: 5 } }],
    validation_rules: [],
    data_requirements: { required_fields: ['close'], symbol_count: 1, symbols: ['AAPL'], minimum_history: 5 },
  }
  const dataset = {
    dataset_id: 'dataset-aapl', name: 'AAPL Data', source_type: 'CSV', timezone: 'UTC', frequency: '1D',
    symbols: ['AAPL'], fields: ['close'], row_count: 100, synchronized_bar_count: 100,
    start_time: '2025-01-01T00:00:00Z', end_time: '2025-04-10T00:00:00Z', created_at: '2025-01-01T00:00:00Z',
    content_fingerprint: 'sha256:data', source_timezone: 'UTC', column_mapping: {},
    quality: { status: 'VALID', rows: 100, symbols: 1, start: '2025-01-01T00:00:00Z', end: '2025-04-10T00:00:00Z', duplicates: 0, missing_required_values: 0, rows_reordered: 0, alignment_gaps: 0, timezone: 'UTC', issues: [] },
  } satisfies DatasetDefinition
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: true, status: 200, json: async () => ({ strategy_id: 'user.sma-cross', dataset_id: 'dataset-aapl', compatible: true, required_fields: ['close'], provided_fields: ['close'], required_symbol_count: 1, provided_symbol_count: 1, required_symbols: ['AAPL'], missing_symbols: [], minimum_history: 5, synchronized_bar_count: 100, reasons: [] }) } as Response)
  const onStrategyChange = vi.fn()
  const runBacktest = vi.fn().mockResolvedValue(created)
  render(<StrategyPage definition={nativeDefinition} strategies={[strategyDefinition, nativeDefinition]} datasets={[dataset]} selectedDatasetId="dataset-aapl" onStrategyChange={onStrategyChange} onOpenReplay={() => undefined} runBacktest={runBacktest} />)
  expect(screen.getByLabelText('Strategy selector')).toHaveTextContent('Pairs Trading')
  expect(screen.getByLabelText('Strategy selector')).toHaveTextContent('SMA Cross')
  expect(screen.getByLabelText('Fast Window')).toHaveValue(3)
  expect(screen.getByText('AAPL')).toBeInTheDocument()
  expect(await screen.findByText('COMPATIBLE')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Strategy selector'), { target: { value: 'pairs-trading' } })
  expect(onStrategyChange).toHaveBeenCalledWith('pairs-trading')
  fireEvent.click(screen.getByRole('button', { name: 'Run Backtest' }))
  expect(await screen.findByRole('heading', { name: 'Backtest summary' })).toBeInTheDocument()
  expect(runBacktest).toHaveBeenCalledWith({ fast_window: 3, slow_window: 5, fee_bps: 5, slippage_bps: 5 })
})
