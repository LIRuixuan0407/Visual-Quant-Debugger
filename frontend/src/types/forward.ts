import type { Diagnostic, TimelineEvent, TraceScalar } from './trace'

export type ForwardSessionStatus = 'CREATED' | 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'STOPPED' | 'ERROR' | 'FAILED'
export type PendingStatus = 'PENDING' | 'FILLED' | 'CANCELLED' | 'EXPIRED_END_OF_DATA'

export interface PendingTransition {
  pending_id: string
  source_signal_id: string
  source_event_id: string
  source_bar_index: number
  target_position: -1 | 0 | 1
  hedge_ratio: number
  status: PendingStatus
  scheduled_bar_index: number
  scheduled_at: string | null
  resolved_at: string | null
  target_positions?: Record<string, number> | null
}

export interface ForwardSessionSummary {
  initial_equity: number
  final_equity: number
  total_return: number
  max_drawdown: number
  fees: number
  slippage: number
  signal_count: number
  execution_count: number
  closed_trade_count: number
  open_trade_count: number
  processed_bars: number
  expired_order_count: number
}

export interface ForwardSessionSnapshot {
  session_id: string
  status: ForwardSessionStatus
  strategy_id: string
  dataset_id: string
  parameters: Record<string, TraceScalar>
  processed_bar_count: number
  total_bar_count: number
  current_timestamp: string | null
  cash: number
  quantity_a: number
  quantity_b: number
  equity: number
  cumulative_pnl: number
  cumulative_fees: number
  cumulative_slippage: number
  current_signal_state: string
  pending_transitions: PendingTransition[]
  latest_event: TimelineEvent | null
  summary: ForwardSessionSummary
  positions?: Record<string, number>
  failure?: { exception_type: string; message: string; timestamp: string } | null
}

export interface ForwardTrace {
  trace_version: '1.0'
  session_id: string
  strategy_id: string
  parameters: Record<string, TraceScalar>
  timeline: TimelineEvent[]
  diagnostics: Diagnostic[]
}

export interface ResearchForwardMetrics {
  period_label: string
  total_return: number
  sharpe: number
  max_drawdown: number
  turnover: number
  trades: number
  fees: number
  slippage: number
  final_equity: number
}

export interface ConsistencyCheck {
  field: string
  batch_value: string | number
  forward_value: string | number
  difference: number | null
  status: 'MATCH' | 'DIVERGENCE'
}

export interface ForwardComparisonReport {
  session_id: string
  different_evaluation_periods: boolean
  research: ResearchForwardMetrics
  forward: ResearchForwardMetrics
  consistency: ConsistencyCheck[]
  consistency_status: 'MATCH' | 'DIVERGENCE'
  first_divergence: null | {
    field: string
    index: number | null
    batch_value: string | number
    forward_value: string | number
  }
}
