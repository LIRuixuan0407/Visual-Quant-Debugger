export type DiagnosticMetricStatus = 'OK' | 'INSUFFICIENT_DATA' | 'NO_TRADES' | 'UNDEFINED_SHARPE'

export interface DiagnosticMetrics {
  status: DiagnosticMetricStatus
  return: number
  sharpe: number
  max_drawdown: number
  turnover: number
  trade_count: number
  final_equity: number
  bar_count: number
  note: string | null
}

export interface DiagnosisReport {
  report_version: '1.0'
  source_run: {
    trace_id: string
    strategy_id: string
    dataset_id: string
    dataset_name: string
    dataset_source: string
    bar_count: number
    current_lookback: number
    fee_bps: number
    slippage_bps: number
    sensitivity_parameter?: string | null
  }
  train_test: {
    method: 'chronological-70-30'
    train_start: string
    train_end: string
    test_start: string
    test_end: string
    train_bar_count: number
    test_bar_count: number
    feature_context_policy: string
    pnl_isolation_policy: string
    train: DiagnosticMetrics
    test: DiagnosticMetrics
  }
  lookback_sensitivity: Array<{
    lookback: number
    is_current: boolean
    train: DiagnosticMetrics
    test: DiagnosticMetrics
  }>
  cost_stress: Array<{
    total_friction_bps: number
    fee_bps: number
    slippage_bps: number
    metrics: DiagnosticMetrics
  }>
  execution_delay: Array<{
    additional_delay_bars: 0 | 1 | 2
    execution_offset_bars: 1 | 2 | 3
    unfilled_signal_count: number
    metrics: DiagnosticMetrics
  }>
  observations: Array<{
    observation_id: string
    title: string
    detail: string
    evidence: string
  }>
  sensitivity_available?: boolean
  support?: {
    train_test: 'AVAILABLE' | 'NOT_SUPPORTED'
    parameter_sensitivity: 'AVAILABLE' | 'NOT_SUPPORTED'
    cost_stress: 'AVAILABLE' | 'NOT_SUPPORTED'
    execution_delay: 'AVAILABLE' | 'NOT_SUPPORTED'
  }
}
