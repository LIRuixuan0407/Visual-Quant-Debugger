/* eslint-disable no-loss-of-precision -- Values mirror the serialized golden trace. */
import type {
  BacktestTrace,
  DataDependency,
  FeatureSnapshot,
  TimelineEvent,
} from '../../types/trace'

function dependency(id: string, symbol: string, value: number, timestamp: string): DataDependency {
  return {
    dependency_id: id,
    source: 'market_data',
    field: 'close',
    symbol,
    value,
    source_timestamp: timestamp,
    available_at: timestamp,
    used_at: timestamp,
  }
}

function feature(
  featureId: string,
  name: string,
  value: number | null,
  inputs: string[],
  timestamp: string,
): FeatureSnapshot {
  const formulas: Record<string, string> = {
    hedge_ratio: 'dot(price_B, price_A) / dot(price_B, price_B)',
    spread: 'price_A - hedge_ratio * price_B',
    rolling_mean: 'mean(spread_window)',
    rolling_std: 'population_std(spread_window, ddof=0)',
    zscore: '(spread - rolling_mean) / rolling_std',
  }
  return {
    feature_id: featureId,
    name,
    value,
    formula: formulas[name],
    inputs,
    parameters: name === 'hedge_ratio' ? { lookback: 5 } : {},
    window_start: value === null ? null : '2024-01-11T16:00:00Z',
    window_end: value === null ? null : timestamp,
    available_at: timestamp,
    data_dependencies: [],
  }
}

const warmupTime = '2024-01-02T16:00:00Z'
const signalTime = '2024-01-17T16:00:00Z'
const executionTime = '2024-01-18T16:00:00Z'

const warmupEvent: TimelineEvent = {
  event_id: 'timeline-000001',
  timestamp: warmupTime,
  market_snapshot: {
    values: [
      { symbol: 'ASSET_A', field: 'close', value: 100.2, dependency_id: 'dependency-000001' },
      { symbol: 'ASSET_B', field: 'close', value: 50, dependency_id: 'dependency-000002' },
    ],
  },
  feature_snapshots: [
    feature('feature-000001', 'hedge_ratio', null, [], warmupTime),
    feature('feature-000002', 'spread', null, [], warmupTime),
    feature('feature-000003', 'rolling_mean', null, [], warmupTime),
    feature('feature-000004', 'rolling_std', null, [], warmupTime),
    feature('feature-000005', 'zscore', null, ['feature-000002', 'feature-000003', 'feature-000004'], warmupTime),
  ],
  signal_evaluation: {
    evaluation_id: 'signal-evaluation-000001',
    signal_id: null,
    signal: 'WARMUP',
    decision_time: warmupTime,
    reason: 'Need two complete 5-bar windows',
    conditions: [{ left_operand: 'zscore', left_value: null, operator: 'is_available', right_operand: null, right_value: null, result: false, description: 'The strategy is still in its warm-up period' }],
    dependencies: [],
    previous_state: 'FLAT',
    next_state: 'FLAT',
    target_position: 0,
  },
  position_snapshot: {
    position_state: 'FLAT',
    target_position: 0,
    asset_positions: [
      { symbol: 'ASSET_A', quantity: 0, market_value: 0 },
      { symbol: 'ASSET_B', quantity: 0, market_value: 0 },
    ],
    gross_exposure: 0,
    net_exposure: 0,
  },
  order_events: [],
  execution_events: [],
  cost_snapshot: { fees: 0, slippage: 0, total_cost: 0, cumulative_fees: 0, cumulative_slippage: 0 },
  pnl_snapshot: { period_gross_pnl: 0, period_net_pnl: 0, cumulative_gross_pnl: 0, cumulative_net_pnl: 0, equity: 100000 },
  data_dependencies: [
    dependency('dependency-000001', 'ASSET_A', 100.2, warmupTime),
    dependency('dependency-000002', 'ASSET_B', 50, warmupTime),
  ],
}

const signalEvent: TimelineEvent = {
  event_id: 'timeline-000012',
  timestamp: signalTime,
  market_snapshot: {
    values: [
      { symbol: 'ASSET_A', field: 'close', value: 109.55, dependency_id: 'dependency-000100' },
      { symbol: 'ASSET_B', field: 'close', value: 54.4, dependency_id: 'dependency-000101' },
    ],
  },
  feature_snapshots: [
    feature('feature-000056', 'hedge_ratio', 2.013985410402049, [], signalTime),
    feature('feature-000057', 'spread', -0.01080632587147079, ['feature-000056'], signalTime),
    feature('feature-000058', 'rolling_mean', -0.7714424950651108, [], signalTime),
    feature('feature-000059', 'rolling_std', 0.38150720518091197, [], signalTime),
    feature('feature-000060', 'zscore', 1.9937661959305428, ['feature-000057', 'feature-000058', 'feature-000059'], signalTime),
  ],
  signal_evaluation: {
    evaluation_id: 'signal-evaluation-000012',
    signal_id: 'signal-0001',
    signal: 'SHORT_SPREAD',
    decision_time: signalTime,
    reason: 'z-score 1.9938 > entry threshold 1.0000',
    conditions: [
      { left_operand: 'zscore', left_value: 1.9937661959305428, operator: '>', right_operand: 'entry_z', right_value: 1, result: true, description: 'Enter short spread when z-score exceeds the entry threshold' },
      { left_operand: 'zscore', left_value: 1.9937661959305428, operator: '<', right_operand: '-entry_z', right_value: -1, result: false, description: 'Enter long spread when z-score is below the negative entry threshold' },
    ],
    dependencies: ['feature-000060'],
    previous_state: 'FLAT',
    next_state: 'SHORT_SPREAD',
    target_position: -1,
  },
  position_snapshot: {
    position_state: 'FLAT',
    target_position: -1,
    asset_positions: [
      { symbol: 'ASSET_A', quantity: 0, market_value: 0 },
      { symbol: 'ASSET_B', quantity: 0, market_value: 0 },
    ],
    gross_exposure: 0,
    net_exposure: 0,
  },
  order_events: [],
  execution_events: [],
  cost_snapshot: { fees: 0, slippage: 0, total_cost: 0, cumulative_fees: 0, cumulative_slippage: 0 },
  pnl_snapshot: { period_gross_pnl: 0, period_net_pnl: 0, cumulative_gross_pnl: 0, cumulative_net_pnl: 0, equity: 100000 },
  data_dependencies: [
    dependency('dependency-000100', 'ASSET_A', 109.55, signalTime),
    dependency('dependency-000101', 'ASSET_B', 54.4, signalTime),
  ],
}

const executionEvent: TimelineEvent = {
  event_id: 'timeline-000013',
  timestamp: executionTime,
  market_snapshot: {
    values: [
      { symbol: 'ASSET_A', field: 'close', value: 106.2, dependency_id: 'dependency-000110' },
      { symbol: 'ASSET_B', field: 'close', value: 54.8, dependency_id: 'dependency-000111' },
    ],
  },
  feature_snapshots: [
    feature('feature-000061', 'hedge_ratio', 1.9981044604158669, [], executionTime),
    feature('feature-000062', 'spread', -3.29612443078949, ['feature-000061'], executionTime),
    feature('feature-000063', 'rolling_mean', -1.2400731320217317, [], executionTime),
    feature('feature-000064', 'rolling_std', 1.0927700176805375, [], executionTime),
    feature('feature-000065', 'zscore', -1.8815041275856346, ['feature-000062', 'feature-000063', 'feature-000064'], executionTime),
  ],
  signal_evaluation: {
    evaluation_id: 'signal-evaluation-000013',
    signal_id: null,
    signal: 'HOLD',
    decision_time: executionTime,
    reason: 'No position transition condition was met',
    conditions: [{ left_operand: 'abs(zscore)', left_value: 1.8815041275856346, operator: '<', right_operand: 'exit_z', right_value: 0.8, result: false, description: 'Close the spread when absolute z-score is below the exit threshold' }],
    dependencies: ['feature-000065'],
    previous_state: 'SHORT_SPREAD',
    next_state: 'SHORT_SPREAD',
    target_position: -1,
  },
  position_snapshot: {
    position_state: 'SHORT_SPREAD',
    target_position: -1,
    asset_positions: [
      { symbol: 'ASSET_A', quantity: -92.35042903583062, market_value: -9809.23807818026 },
      { symbol: 'ASSET_B', quantity: 185.99241672253262, market_value: 10192.384436394788 },
    ],
    gross_exposure: 20001.62251457505,
    net_exposure: 383.1463582145277,
  },
  order_events: [
    { order_id: 'signal-0001-order-1', symbol: 'ASSET_A', side: 'SELL', quantity: 92.35042903583062, submitted_at: executionTime, expected_execution_at: executionTime, target_position: -1, source_signal_id: 'signal-0001' },
    { order_id: 'signal-0001-order-2', symbol: 'ASSET_B', side: 'BUY', quantity: 185.99241672253262, submitted_at: executionTime, expected_execution_at: executionTime, target_position: -1, source_signal_id: 'signal-0001' },
  ],
  execution_events: [
    { execution_id: 'signal-0001-order-1-execution', symbol: 'ASSET_A', side: 'SELL', quantity: 92.35042903583062, reference_price: 106.2, fill_price: 106.1469, traded_notional: 9807.615563605211, fee: 4.903807781802606, slippage: 4.903807781802606, executed_at: executionTime, source_order_id: 'signal-0001-order-1' },
    { execution_id: 'signal-0001-order-2-execution', symbol: 'ASSET_B', side: 'BUY', quantity: 185.99241672253262, reference_price: 54.8, fill_price: 54.8274, traded_notional: 10192.384436394788, fee: 5.096192218197394, slippage: 5.096192218197394, executed_at: executionTime, source_order_id: 'signal-0001-order-2' },
  ],
  cost_snapshot: { fees: 10, slippage: 10, total_cost: 20, cumulative_fees: 10, cumulative_slippage: 10 },
  pnl_snapshot: { period_gross_pnl: -1.4551915228366852e-11, period_net_pnl: -20.000000000014552, cumulative_gross_pnl: -1.4551915228366852e-11, cumulative_net_pnl: -20.000000000014552, equity: 99_980 },
  data_dependencies: [
    dependency('dependency-000110', 'ASSET_A', 106.2, executionTime),
    dependency('dependency-000111', 'ASSET_B', 54.8, executionTime),
  ],
}

export const goldenTrace: BacktestTrace = {
  trace_version: '1.0',
  metadata: {
    dataset_id: 'sha256:fb1aa0b3d18fa2f5de3064d5127153b49a72cf889c7dd676db4ffc320fb7c1cf',
    dataset_name: 'in-memory-bars',
    bar_count: 3,
    data_start: warmupTime,
    data_end: executionTime,
    execution_model: 'signal at close(t); execute at close(t+1)',
  },
  strategy: { strategy_id: 'pairs-trading', name: 'Pairs Trading' },
  parameters: { lookback: 5, entry_z: 1, exit_z: 0.8, fee_bps: 5, slippage_bps: 5 },
  timeline: [warmupEvent, signalEvent, executionEvent],
  trades: [],
  metrics: { total_return: -0.0002, net_pnl: -20 },
  diagnostics: [],
}
