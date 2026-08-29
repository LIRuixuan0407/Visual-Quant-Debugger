import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'

import type { RunFilters } from '../../api/runs'
import { strategyDefinition } from '../../test/fixtures/strategyDefinition'
import type { BacktestCreated } from '../../types/trace'
import type { DatasetDefinition } from '../../types/dataset'
import type {
  RunAnnotations,
  RunComparisonReport,
  RunDetail,
  RunListItem,
  RunListResponse,
  RunValidationReport,
  StrategySourceArtifact,
} from '../../types/run'
import RunsPage from './RunsPage'

const runAId = 'run-00000000000000000000000a'
const runBId = 'run-00000000000000000000000b'
const paperRunId = 'run-00000000000000000000paper'

const dataset = { dataset_id: 'pairs-sample-v1', name: 'Pairs Daily Sample' } as DatasetDefinition

function runItem(runId: string, fast: number, name = ''): RunListItem {
  return {
    run_id: runId,
    run_type: 'BACKTEST',
    trace_id: `trace-${runId.at(-1)}`,
    status: 'COMPLETED',
    created_at: `2025-01-${String(fast).padStart(2, '0')}T16:00:00Z`,
    completed_at: `2025-01-${String(fast).padStart(2, '0')}T16:01:00Z`,
    strategy_id: 'pairs-trading',
    strategy_name: 'Pairs Trading',
    strategy_fingerprint: 'sha256:strategy-revision',
    dataset_id: 'pairs-sample-v1',
    dataset_name: 'Pairs Daily Sample',
    dataset_fingerprint: 'sha256:dataset-revision',
    parameters: { lookback: fast, entry_z: 1.5, exit_z: 0.25, fee_bps: 5 },
    period: { start: '2025-01-01T16:00:00Z', end: '2025-02-01T16:00:00Z', cutoff: null },
    metrics: { total_return: fast / 100, sharpe: fast / 10, max_drawdown: -0.04, turnover: 0.3, trades: fast, final_equity: 100_000 + fast * 100, fees: 10, slippage: 8, net_pnl: fast * 100 },
    run_fingerprint: `sha256:fingerprint-${fast}`,
    reproduced_from_run_id: null,
    annotations: { display_name: name, note: '', tags: [] },
  }
}

const runA = runItem(runAId, 10, 'SMA baseline')
const runB = runItem(runBId, 15)
const listResponse: RunListResponse = { items: [runB, runA], total: 2, limit: 100, offset: 0 }

function detailFor(run: RunListItem): RunDetail {
  return {
    manifest: {
      run_version: '1.0', run_id: run.run_id, run_type: 'BACKTEST', run_fingerprint: run.run_fingerprint,
      status: run.status, created_at: run.created_at, completed_at: run.completed_at,
      strategy: { strategy_id: run.strategy_id, name: run.strategy_name, version: '1.0', class_name: 'PairsTradingStrategy', source_fingerprint: run.strategy_fingerprint, original_source_path: '/strategies/pairs.py' },
      dataset: { dataset_id: run.dataset_id, name: run.dataset_name, content_fingerprint: run.dataset_fingerprint, source_timezone: 'UTC' },
      period: run.period, parameters: run.parameters,
      execution_model: { execution_model_id: 'next-close', version: '1.0', description: 'signal at close(t); execute at close(t+1)' },
      engine: { python_version: '3.12.9', platform: 'Linux', vqd_version: '0.1.0' },
      trace_version: '1.0', trace_id: run.trace_id, metrics: run.metrics,
      artifacts: { strategy_source_sha256: 'sha256:source', trace_sha256: 'sha256:trace', diagnostics_sha256: null, pnl_autopsy_sha256: null },
      failure: null, reproduced_from_run_id: null,
    },
    annotations: run.annotations,
    artifacts: { strategy_source: true, trace: true, diagnostics: false, pnl_autopsy: false },
    integrity: 'VERIFIED', current_strategy_fingerprint: run.strategy_fingerprint, current_source_matches: true,
  }
}

const comparison: RunComparisonReport = {
  report_version: '1.0', run_ids: [runAId, runBId], comparability: 'STRICTLY_COMPARABLE',
  context_diff: [
    { field: 'strategy_revision', same: true, values: ['sha256:strategy-revision', 'sha256:strategy-revision'] },
    { field: 'dataset_revision', same: true, values: ['sha256:dataset-revision', 'sha256:dataset-revision'] },
    { field: 'evaluation_period', same: true, values: ['same', 'same'] },
    { field: 'execution_model', same: true, values: ['next-close@1.0', 'next-close@1.0'] },
  ],
  parameter_diff: [{ parameter: 'lookback', values: [10, 15], changed: true }],
  metric_diff: [{ metric: 'sharpe', values: [1, 1.5], differences_from_first: [null, 0.5] }],
  equity_comparison: [
    { timestamp: '2025-01-01T16:00:00Z', values: [100_000, 100_000] },
    { timestamp: '2025-01-02T16:00:00Z', values: [100_100, 100_250] },
  ],
  signal_comparison: [{ timestamp: '2025-01-10T16:00:00Z', values: ['LONG', 'HOLD'], event_ids: ['event-a', 'event-b'] }],
  execution_comparison: [{ timestamp: '2025-01-11T16:00:00Z', values: ['BUY 100', '[]'], event_ids: ['event-a2', 'event-b2'] }],
  first_behavioral_divergence: {
    status: 'DIVERGENCE', kind: 'FEATURE', timestamp: '2025-01-10T16:00:00Z',
    event_ids: ['event-a', 'event-b'], summary: 'First feature behavior differs.', run_values: ['1.1', '1.2'],
    associated_parameter_differences: ['lookback'],
  },
}

const validation: RunValidationReport = {
  report_version: '2.0', attribution_rule_version: '1.0', report_id: 'validation-a-paper', backtest_run_id: runAId,
  paper_run_id: paperRunId, reference_run_id: 'run-reference', reference_trace_id: 'trace-reference', paper_trace_id: 'trace-paper',
  historical_comparability: 'DESCRIPTIVE_ONLY', strict_recorded_feed_status: 'FIRST_DIVERGENCE',
  checks: [{ field: 'strategy_revision', same: true, reference_value: 'sha256:same', paper_value: 'sha256:same' }, { field: 'market_path', same: false, reference_value: 'sha256:history', paper_value: 'sha256:recorded' }],
  first_divergence: { status: 'DIVERGENCE', layer: 'EXECUTION', timestamp: '2025-01-11T16:00:00Z', reference_value: '100', paper_value: '101', difference: 'First recorded-feed difference at EXECUTION', reference_event_id: 'event-ref', paper_event_id: 'event-paper' },
  pnl_attribution: {
    total_difference: -120, market_path_difference: -80, decision_difference: 0, execution_price_difference: null, delay_impact: null,
    fees: -10, slippage: -5, residual_unattributed: -25, attributed_total: -95, reconciliation_error: 0, status: 'PARTIALLY_ATTRIBUTED',
    components: [
      { layer: 'MARKET_PATH', amount: -80, status: 'ATTRIBUTED', summary: 'Historical-to-recorded-feed P&L bridge under matched strategy and execution semantics', evidence: ['Backtest dataset: historical'], first_divergence_at: null, reference_event_id: null, paper_event_id: null, sample_count: 0, average_delay_ms: null, max_delay_ms: null },
      { layer: 'DECISION', amount: 0, status: 'MATCH', summary: 'Recorded Feed Reference and Paper made the same decisions on every aligned event', evidence: [], first_divergence_at: null, reference_event_id: null, paper_event_id: null, sample_count: 20, average_delay_ms: null, max_delay_ms: null },
      { layer: 'EXECUTION_PRICE', amount: null, status: 'DETECTED', summary: 'Actual Paper fill price differs from the Recorded Feed Reference fill price', evidence: ['Reference fill: 100', 'Paper fill: 101'], first_divergence_at: '2025-01-11T16:00:00Z', reference_event_id: 'event-ref', paper_event_id: 'event-paper', sample_count: 1, average_delay_ms: null, max_delay_ms: null },
      { layer: 'DELAY', amount: null, status: 'DETECTED', summary: 'Paper execution timing differs from the same-decision Recorded Feed Reference', evidence: [], first_divergence_at: '2025-01-11T16:00:01Z', reference_event_id: 'event-ref', paper_event_id: 'event-paper', sample_count: 1, average_delay_ms: 1000, max_delay_ms: 1000 },
      { layer: 'FEES', amount: -10, status: 'ATTRIBUTED', summary: 'Recorded fees difference from Reference to Paper', evidence: [], first_divergence_at: null, reference_event_id: null, paper_event_id: null, sample_count: 0, average_delay_ms: null, max_delay_ms: null },
      { layer: 'SLIPPAGE', amount: -5, status: 'ATTRIBUTED', summary: 'Recorded slippage difference from Reference to Paper', evidence: [], first_divergence_at: null, reference_event_id: null, paper_event_id: null, sample_count: 0, average_delay_ms: null, max_delay_ms: null },
      { layer: 'RESIDUAL', amount: -25, status: 'ATTRIBUTED', summary: 'Residual is retained because the remaining P&L gap is not deterministically isolated by recorded evidence', evidence: ['Residual is never force-distributed across known attribution layers.'], first_divergence_at: null, reference_event_id: null, paper_event_id: null, sample_count: 0, average_delay_ms: null, max_delay_ms: null },
    ],
  },
  note: 'Historical Backtest vs Paper is descriptive unless period and market path match. Strict status compares the frozen Recorded Feed Reference with Paper.',
}

function services() {
  return {
    getRuns: vi.fn<(filters?: RunFilters) => Promise<RunListResponse>>(async () => listResponse),
    getRun: vi.fn<(runId: string) => Promise<RunDetail>>(async (runId) => detailFor(runId === runAId ? runA : runB)),
    saveAnnotations: vi.fn<(runId: string, annotations: RunAnnotations) => Promise<RunAnnotations>>(async (_runId, annotations) => annotations),
    deleteRun: vi.fn<(runId: string) => Promise<void>>(async () => undefined),
    rerun: vi.fn<(runId: string) => Promise<BacktestCreated>>(async () => ({ run_id: 'run-00000000000000000000000c', run_fingerprint: 'sha256:rerun', trace_id: 'trace-c', trace_version: '1.0', status: 'COMPLETED', summary: { total_return: 0.1, net_pnl: 100, max_drawdown: -0.1, timeline_events: 20, signals: 2 } })),
    compare: vi.fn<(runIds: string[]) => Promise<RunComparisonReport>>(async () => comparison),
    validate: vi.fn<(backtestRunId: string, paperRunId: string) => Promise<RunValidationReport>>(async () => validation),
    getSource: vi.fn<(runId: string) => Promise<StrategySourceArtifact>>(async (runId) => ({ run_id: runId, filename: 'strategy.py', sha256: 'sha256:source', source: 'class SavedStrategy:\n    pass\n' })),
  }
}

beforeEach(() => vi.restoreAllMocks())

test('lists, filters, selects, annotates, opens historical artifacts, reruns, and deletes', async () => {
  const api = services()
  const onReplay = vi.fn()
  const onDiagnose = vi.fn()
  const onAutopsy = vi.fn()
  const onLoadConfiguration = vi.fn()
  const onRunDataAudit = vi.fn()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<RunsPage strategies={[strategyDefinition]} datasets={[dataset]} onOpenReplay={onReplay} onOpenDiagnose={onDiagnose} onOpenAutopsy={onAutopsy} onLoadConfiguration={onLoadConfiguration} onRunDataAudit={onRunDataAudit} services={api} />)

  expect(await screen.findByText('2 research records · newest first')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'SMA baseline' })).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Strategy filter'), { target: { value: 'pairs-trading' } })
  fireEvent.change(screen.getByLabelText('Dataset filter'), { target: { value: 'pairs-sample-v1' } })
  fireEvent.change(screen.getByLabelText('Status filter'), { target: { value: 'COMPLETED' } })
  await waitFor(() => expect(api.getRuns).toHaveBeenLastCalledWith(expect.objectContaining({ strategy_id: 'pairs-trading', dataset_id: 'pairs-sample-v1', status: 'COMPLETED' })))

  const runARow = screen.getByRole('button', { name: 'SMA baseline' }).closest('tr')
  expect(runARow).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'SMA baseline' }))
  expect(await screen.findByText('RUN INSPECTOR')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Run Data Audit' }))
  expect(onRunDataAudit).toHaveBeenCalledWith(runAId)
  fireEvent.click(within(runARow!).getByRole('button', { name: 'Replay' }))
  fireEvent.click(within(runARow!).getByRole('button', { name: 'Diagnose' }))
  fireEvent.click(within(runARow!).getByRole('button', { name: 'P&L Autopsy' }))
  expect(onReplay).toHaveBeenCalledWith(runAId, 'trace-a')
  expect(onDiagnose).toHaveBeenCalledWith(runAId, 'trace-a')
  expect(onAutopsy).toHaveBeenCalledWith(runAId, 'trace-a')

  fireEvent.change(screen.getByLabelText('Run note'), { target: { value: 'Cost stress follow-up.' } })
  fireEvent.change(screen.getByLabelText('Run tags'), { target: { value: 'baseline, cost-test' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save annotations' }))
  await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalledWith(runAId, expect.objectContaining({ note: 'Cost stress follow-up.', tags: ['baseline', 'cost-test'] })))
  fireEvent.click(screen.getByRole('button', { name: 'Load config into Strategy' }))
  expect(onLoadConfiguration).toHaveBeenCalledWith(expect.objectContaining({ strategy_id: 'pairs-trading', dataset_id: 'pairs-sample-v1' }))
  fireEvent.click(screen.getByRole('button', { name: 'View strategy snapshot' }))
  expect(await screen.findByText(/SavedStrategy/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Re-run exact revision' }))
  await waitFor(() => expect(api.rerun).toHaveBeenCalledWith(runAId))
  fireEvent.click(await screen.findByRole('button', { name: 'Delete Run' }))
  await waitFor(() => expect(api.deleteRun).toHaveBeenCalled())
})

test('compares two runs with context, parameter, metric, signal, execution, and replay divergence links', async () => {
  const api = services()
  const onReplay = vi.fn()
  render(<RunsPage strategies={[strategyDefinition]} datasets={[dataset]} onOpenReplay={onReplay} onOpenDiagnose={() => undefined} onOpenAutopsy={() => undefined} onLoadConfiguration={() => undefined} services={api} />)
  await screen.findByText('2 research records · newest first')
  fireEvent.click(screen.getByLabelText(`Select ${runAId} for comparison`))
  fireEvent.click(screen.getByLabelText(`Select ${runBId} for comparison`))
  fireEvent.click(screen.getByRole('button', { name: 'Compare' }))

  expect(await screen.findByRole('heading', { name: 'Compare Runs' })).toBeInTheDocument()
  expect(screen.getByText('STRICTLY_COMPARABLE')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Parameter Diff' })).toBeInTheDocument()
  expect(screen.getByText('lookback')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Metric Diff' })).toBeInTheDocument()
  expect(screen.getByText('First feature behavior differs.')).toBeInTheDocument()
  expect(screen.getByText('LONG')).toBeInTheDocument()
  expect(screen.getByText('BUY 100')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Open Run A in Replay' }))
  fireEvent.click(screen.getByRole('button', { name: 'Open Run B in Replay' }))
  expect(onReplay).toHaveBeenNthCalledWith(1, runAId, 'trace-a', 'event-a')
  expect(onReplay).toHaveBeenNthCalledWith(2, runBId, 'trace-b', 'event-b')
})

test('validates one Backtest and one Paper Run against the frozen recorded-feed reference', async () => {
  const api = services()
  const paper = { ...runB, run_id: paperRunId, run_type: 'PAPER' as const, trace_id: 'trace-paper' }
  api.getRuns.mockResolvedValue({ items: [paper, runA], total: 2, limit: 100, offset: 0 })
  const onReplay = vi.fn()
  render(<RunsPage strategies={[strategyDefinition]} datasets={[dataset]} onOpenReplay={onReplay} onOpenDiagnose={() => undefined} onOpenAutopsy={() => undefined} onLoadConfiguration={() => undefined} services={api} />)
  await screen.findByText('2 research records · newest first')
  fireEvent.click(screen.getByLabelText(`Select ${runAId} for comparison`))
  fireEvent.click(screen.getByLabelText(`Select ${paperRunId} for comparison`))
  fireEvent.click(screen.getByRole('button', { name: 'Attribute' }))
  expect(await screen.findByRole('heading', { name: 'Backtest vs Paper Attribution' })).toBeInTheDocument()
  expect(screen.getByText('DESCRIPTIVE_ONLY')).toBeInTheDocument()
  expect(screen.getAllByText('MATCH').length).toBeGreaterThan(0)
  expect(screen.getByText('First recorded-feed difference at EXECUTION')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Attribution Waterfall' })).toBeInTheDocument()
  expect(screen.getAllByText('DETECTED').length).toBeGreaterThanOrEqual(2)
  expect(screen.getAllByText('-25').length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('button', { name: 'Open Reference Replay' }))
  fireEvent.click(screen.getByRole('button', { name: 'Open Paper Replay' }))
  expect(onReplay).toHaveBeenNthCalledWith(1, 'run-reference', 'trace-reference', 'event-ref')
  expect(onReplay).toHaveBeenNthCalledWith(2, paperRunId, 'trace-paper', 'event-paper')
})
