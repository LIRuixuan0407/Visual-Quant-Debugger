import type { Diagnostic, TimelineEvent, TraceScalar } from './trace'

export type PaperSessionStatus = 'CREATED' | 'RUNNING' | 'PAUSED' | 'STOPPED' | 'ERROR'
export type FeedStatus = 'CONNECTED' | 'RECONNECTING' | 'STALE' | 'DISCONNECTED'
export type RecoveryStatus = 'READY' | 'RECOVERING' | 'RECOVERY_DIVERGENCE'
export type PaperExecutionMode = 'VQD_SIMULATED' | 'ALPACA_PAPER'
export type BrokerConnectionStatus = 'NOT_USED' | 'CONNECTED' | 'RECONNECTING' | 'DISCONNECTED' | 'ERROR'
export type BrokerOrderStatus = 'CREATED' | 'SUBMITTED' | 'PARTIALLY_FILLED' | 'PENDING_CANCEL' | 'FILLED' | 'CANCELLED' | 'EXPIRED' | 'REJECTED' | 'REPLACED' | 'DONE_FOR_DAY' | 'HELD' | 'SUSPENDED' | 'UNKNOWN'
export type JournalDisposition = 'BUFFERED' | 'EVALUATED' | 'EVALUATION_SKIPPED_PAUSED' | 'CORRECTION_APPLIED' | 'DUPLICATE_IGNORED' | 'OUT_OF_ORDER_REJECTED'

export interface MarketDataProviderStatus {
  provider: string
  configured: boolean
  feeds: string[]
  selected_feed: string
  timeframe: '1Min'
  market_session: 'US_REGULAR'
}

export interface MarketClockSnapshot {
  timestamp: string
  is_open: boolean
  next_open: string
  next_close: string
  session: 'US_REGULAR'
}

export interface PaperMarketEvent {
  sequence: number
  market_event_id: string
  disposition: JournalDisposition
  symbol: string
  event_time: string
  available_at: string
  received_at: string
  close: number
  revision: number
  is_correction: boolean
  latency_ms: number
}

export interface PaperAccount {
  account_id: string
  name: string
  currency: 'USD'
  initial_cash: number
  cash: number
  positions: Record<string, number>
  equity: number
  cumulative_fees: number
  cumulative_slippage: number
  active_session_id: string | null
  created_at: string
  updated_at: string
}

export interface PaperOrderRecord {
  order_id: string
  account_id: string
  session_id: string
  market_event_id: string
  source_signal_id: string
  source_decision_id: string
  source_intent_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  submitted_at: string
  status: BrokerOrderStatus
  execution_mode: PaperExecutionMode
  provider: string
  provider_order_id: string | null
  client_order_id: string | null
  raw_status: string | null
  filled_quantity: number
  average_fill_price: number | null
  reference_price: number | null
  updated_at: string
  terminal_at: string | null
  rejection_reason: string | null
}

export interface PaperFillRecord {
  fill_id: string
  execution_id: string
  order_id: string
  source_order_id: string
  account_id: string
  session_id: string
  market_event_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  reference_price: number
  fill_price: number
  traded_notional: number
  fee: number
  slippage: number
  executed_at: string
  provider: string
  provider_execution_id: string | null
}

export interface PaperBrokerEvent {
  event_id: string
  sequence: number
  market_sequence: number
  session_id: string
  provider: 'alpaca'
  event_type: string
  order_id: string
  provider_order_id: string | null
  client_order_id: string
  status: BrokerOrderStatus
  raw_status: string
  symbol: string
  side: 'BUY' | 'SELL'
  ordered_quantity: number
  filled_quantity: number
  fill_quantity: number
  fill_price: number | null
  reference_price: number
  execution_id: string | null
  occurred_at: string
  received_at: string
  message: string | null
}

export interface PaperBrokerAccount {
  account_id: string
  status: string
  currency: string
  cash: number
  equity: number
  buying_power: number
  portfolio_value: number
  trading_blocked: boolean
}

export interface MarketRevisionNotice {
  symbol: string
  event_time: string
  used_revision: number
  used_close: number
  later_revision: number
  later_close: number
  revision_available_at: string
}

export interface PaperPendingOrder {
  source_signal_id: string
  due_market_index: number
  target_positions: Record<string, number>
}

export interface PaperExecution {
  execution_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  fill_price: number
  fee: number
  slippage: number
  executed_at: string
}

export interface PaperAccountSnapshot {
  cash: number
  positions: Record<string, number>
  equity: number
  net_pnl: number
  cumulative_fees: number
  cumulative_slippage: number
  max_drawdown: number
  pending_orders: PaperPendingOrder[]
  executions: PaperExecution[]
}

export interface PaperSessionSnapshot {
  session_id: string
  account_id: string
  status: PaperSessionStatus
  feed_status: FeedStatus
  recovery_status: RecoveryStatus
  execution_mode: PaperExecutionMode
  broker_status: BrokerConnectionStatus
  strategy_id: string
  strategy_name: string
  strategy_fingerprint: string
  symbols: string[]
  securities?: Array<{ symbol: string; name: string; exchange: string; status: 'active' | 'inactive' }>
  parameters: Record<string, number>
  provider: string
  feed: string
  timeframe: '1Min'
  market_session: 'US_REGULAR'
  initial_cash: number
  created_at: string
  started_at: string | null
  stopped_at: string | null
  last_market_event: string | null
  last_received_at: string | null
  last_event_sequence: number
  last_processed_market_event_id: string | null
  market_watermark: string | null
  evaluated_bar_count: number
  correction_count: number
  duplicate_count: number
  out_of_order_count: number
  market_clock: MarketClockSnapshot | null
  account: PaperAccountSnapshot
  recent_market_events: PaperMarketEvent[]
  recent_revisions: MarketRevisionNotice[]
  broker_account: PaperBrokerAccount | null
  recent_broker_events: PaperBrokerEvent[]
  latest_event: TimelineEvent | null
  error_code: string | null
  error_message: string | null
  research_run_id: string | null
  reference_run_id: string | null
  orders: PaperOrderRecord[]
  fills: PaperFillRecord[]
}

export interface PaperTrace {
  trace_version: '1.0'
  session_id: string
  strategy_id: string
  parameters: Record<string, TraceScalar>
  timeline: TimelineEvent[]
  diagnostics: Diagnostic[]
  market_revisions: MarketRevisionNotice[]
  execution_mode: PaperExecutionMode
  broker_events: PaperBrokerEvent[]
}

export interface CreatePaperSessionInput {
  account_id: string
  strategy_id: string
  symbols: string[]
  securities?: Array<{ symbol: string; name: string; exchange: string; status: 'active' | 'inactive' }>
  parameters: Record<string, number>
  provider: 'alpaca'
  feed: 'iex' | 'sip'
  timeframe: '1Min'
  market_session: 'US_REGULAR'
  fee_bps: number
  slippage_bps: number
  execution_mode: PaperExecutionMode
}
