export type TraceScalar = string | number | boolean
export type PositionState = string
export type TradeDirection = string
export type CapabilityStatus = 'AVAILABLE' | 'PARTIAL' | 'UNAVAILABLE'
export type TraceFidelity = 'FULL' | 'STANDARD' | 'BASIC'

export interface TraceCapabilitySet {
  market_timeline: CapabilityStatus
  feature_values: CapabilityStatus
  feature_lineage: CapabilityStatus
  decision_events: CapabilityStatus
  decision_conditions: CapabilityStatus
  data_dependencies: CapabilityStatus
  orders: CapabilityStatus
  executions: CapabilityStatus
  positions: CapabilityStatus
  trades: CapabilityStatus
  equity: CapabilityStatus
  pnl: CapabilityStatus
  point_in_time_proven: CapabilityStatus
  gross_pnl: CapabilityStatus
  fees: CapabilityStatus
  slippage: CapabilityStatus
  trade_attribution: CapabilityStatus
  drawdowns: CapabilityStatus
}

export interface RuntimeDescriptor {
  kind: 'native' | 'framework'
  adapter_id: string | null
  adapter_version: string | null
  framework_name: string | null
  framework_version: string | null
  execution_owner: string
  trace_fidelity: TraceFidelity
  trace_capabilities: TraceCapabilitySet
  determinism: 'DETERMINISTIC' | 'SEEDED' | 'UNVERIFIED'
  random_seed: number | null
  python_executable: string | null
  historical_research_only: boolean
}

export interface TraceMetadata {
  dataset_id: string
  dataset_name: string
  bar_count: number
  data_start: string
  data_end: string
  execution_model: string
  runtime?: RuntimeDescriptor
  adapter_warnings?: string[]
}

export interface StrategyDescriptor {
  strategy_id: string
  name: string
}

export interface DataDependency {
  dependency_id: string
  source: string
  field: string
  symbol: string | null
  value: number | null
  source_timestamp: string
  available_at: string
  used_at: string
}

export interface MarketValue {
  symbol: string
  field: string
  value: number
  dependency_id: string
}

export interface FeatureSnapshot {
  feature_id: string
  name: string
  value: number | null
  formula: string
  inputs: string[]
  parameters: Record<string, TraceScalar>
  window_start: string | null
  window_end: string | null
  available_at: string
  data_dependencies: string[]
}

export interface SignalCondition {
  left_operand: string
  left_value: number | null
  operator: string
  right_operand: string | null
  right_value: number | null
  result: boolean
  description: string
}

export interface SignalEvaluation {
  evaluation_id: string
  signal_id: string | null
  signal: string
  decision_time: string
  reason: string
  conditions: SignalCondition[]
  dependencies: string[]
  previous_state: PositionState
  next_state: PositionState
  target_position: -1 | 0 | 1
  target_positions?: Record<string, number>
}

export interface AssetPosition {
  symbol: string
  quantity: number
  market_value: number
}

export interface PositionSnapshot {
  position_state: PositionState
  target_position: -1 | 0 | 1
  asset_positions: AssetPosition[]
  gross_exposure: number
  net_exposure: number
  target_positions?: Record<string, number>
}

export interface OrderEvent {
  order_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  submitted_at: string
  expected_execution_at: string
  target_position: -1 | 0 | 1
  source_signal_id: string
}

export interface ExecutionEvent {
  execution_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  reference_price: number
  fill_price: number
  traded_notional: number
  fee: number
  slippage: number
  executed_at: string
  source_order_id: string
}

export interface CostSnapshot {
  fees: number
  slippage: number
  total_cost: number
  cumulative_fees: number
  cumulative_slippage: number
}

export interface PnLSnapshot {
  period_gross_pnl: number
  period_net_pnl: number
  cumulative_gross_pnl: number
  cumulative_net_pnl: number
  equity: number
}

export interface TimelineEvent {
  event_id: string
  timestamp: string
  market_snapshot: { values: MarketValue[] }
  feature_snapshots: FeatureSnapshot[]
  signal_evaluation: SignalEvaluation
  position_snapshot: PositionSnapshot
  order_events: OrderEvent[]
  execution_events: ExecutionEvent[]
  cost_snapshot: CostSnapshot
  pnl_snapshot: PnLSnapshot
  data_dependencies: DataDependency[]
}

export interface TradeTrace {
  trade_id: string
  direction: TradeDirection
  status: 'OPEN' | 'CLOSED'
  entry_signal_id: string
  exit_signal_id: string | null
  entry_event_id: string
  exit_event_id: string | null
  opened_at: string
  closed_at: string | null
  order_ids: string[]
  execution_ids: string[]
}

export interface Diagnostic {
  diagnostic_id: string
  severity: 'WARNING' | 'ERROR'
  code: string
  message: string
  event_id: string
  dependency_id: string
}

export interface CorporateActionEvent {
  action_id: string
  symbol: string
  action_type: 'SPLIT' | 'CASH_DIVIDEND' | 'DELISTING'
  timestamp: string
  status: 'APPLIED' | 'REFLECTED_IN_PRICE_VIEW' | 'UNRESOLVED'
  quantity_before: number
  quantity_after: number
  cash_amount: number
  settlement_price: number | null
  evidence: string
}

export interface BacktestTrace {
  trace_version: '1.0'
  metadata: TraceMetadata
  strategy: StrategyDescriptor
  parameters: Record<string, TraceScalar>
  timeline: TimelineEvent[]
  trades: TradeTrace[]
  metrics: Record<string, number>
  diagnostics: Diagnostic[]
  corporate_action_events?: CorporateActionEvent[]
}

export interface BacktestCreated {
  run_id: string
  run_fingerprint: string
  trace_id: string | null
  trace_version: '1.0'
  status?: 'COMPLETED' | 'FAILED' | 'PARTIAL'
  summary: null | {
    total_return: number
    net_pnl: number
    max_drawdown: number
    timeline_events: number
    signals: number
  }
  failure?: {
    strategy_id: string
    timestamp: string
    event_index: number
    exception_type: string
    message: string
    traceback: string
  } | null
}

export interface RunContext {
  run_id: string
  trace_id: string
  strategy_id: string
  strategy_version: string
  strategy_fingerprint: string
  dataset_id: string
  dataset_fingerprint: string
  parameters: Record<string, number>
  execution_model: string
  execution_model_id: string
  execution_model_version: string
  created_at: string
  research_cutoff: string | null
  status: 'COMPLETED' | 'PARTIAL'
  runtime?: RuntimeDescriptor
}
