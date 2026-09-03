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

export interface AutocorrelationPoint {
  lag: number
  status: 'OK' | 'INSUFFICIENT_DATA'
  value: number | null
}

export interface ReturnDiagnostics {
  status: 'OK' | 'INSUFFICIENT_DATA'
  observation_count: number
  return_acf: AutocorrelationPoint[]
  squared_return_acf: AutocorrelationPoint[]
  lag_1_return_autocorrelation: number | null
  lag_1_squared_return_autocorrelation: number | null
  note: string | null
}

export interface PairMeanReversionEvidence {
  status: 'OK' | 'INSUFFICIENT_DATA'
  observation_count: number
  consecutive_pair_count: number
  hedge_ratio_observation_count: number
  phi: number | null
  spread_lag_1_autocorrelation: number | null
  half_life_bars: number | null
  hedge_ratio_mean: number | null
  hedge_ratio_std: number | null
  note: string | null
}

export type VolatilityRegime = 'LOW' | 'NORMAL' | 'HIGH'

export interface VolatilityPoint {
  timestamp: string
  market_return: number | null
  rolling_historical_vol: number | null
  ewma_vol: number | null
  regime: VolatilityRegime | null
}

export interface VolatilityDiagnostics {
  status: 'OK' | 'INSUFFICIENT_DATA' | 'UNSUPPORTED'
  dataset_frequency: string
  rolling_window: number
  ewma_decay: number
  annualization_factor: number | null
  market_return_method: string
  thresholds: { low_upper_bound: number; high_lower_bound: number }
  points: VolatilityPoint[]
  current_regime: VolatilityRegime | null
  current_historical_vol: number | null
  current_ewma_vol: number | null
  drawdown_overlap: Array<{
    episode_id: string
    rank_by_depth: number
    start_time: string
    trough_time: string
    end_time: string
    max_drawdown: number
    start_regime: VolatilityRegime | null
    ewma_rising_at_start: boolean | null
    regime_changed_at_start: boolean | null
  }>
  evaluable_drawdown_count: number
  rising_volatility_start_count: number
  regime_change_start_count: number
  verdict: 'RISING_VOLATILITY_OVERLAP' | 'MIXED_VOLATILITY_OVERLAP' | 'LIMITED_VOLATILITY_OVERLAP' | 'NO_DRAWDOWNS' | 'INSUFFICIENT_DATA' | 'UNSUPPORTED'
  summary: string
  calculation_details: string[]
}

export type TrendRegime = 'UPTREND' | 'DOWNTREND' | 'SIDEWAYS'
export type FailureSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'NOT_AVAILABLE'

export interface RegimePerformance {
  volatility_regime: VolatilityRegime
  trend_regime: TrendRegime
  observation_count: number
  status: 'OK' | 'INSUFFICIENT_DATA'
  total_return: number
  sharpe: number
  max_drawdown: number
  hit_rate: number
  trade_count: number
  turnover: number
}

export interface RegimeDiagnostics {
  status: 'OK' | 'INSUFFICIENT_DATA' | 'UNSUPPORTED'
  trend_window: number
  trend_threshold: number
  performance: RegimePerformance[]
  verdict: 'REGIME_DEPENDENT' | 'MIXED_REGIME_SENSITIVITY' | 'LIMITED_REGIME_SENSITIVITY' | 'LIMITED_EVIDENCE' | 'UNSUPPORTED'
  summary: string
  calculation_details: string[]
}

export interface FailureFingerprintDimension {
  key: 'OOS_DEGRADATION' | 'PARAMETER_INSTABILITY' | 'COST_SENSITIVITY' | 'EXECUTION_DELAY_SENSITIVITY' | 'REGIME_DEPENDENCE' | 'MEAN_REVERSION_EVIDENCE'
  title: string
  severity: FailureSeverity
  evidence: string[]
  calculation_details: string[]
}

export interface FailureFingerprint {
  dimensions: FailureFingerprintDimension[]
  high_severity_count: number
  medium_severity_count: number
  available_dimension_count: number
  summary: string
  calculation_details: string[]
}

export interface WhatIfInputs {
  fee_bps: number
  slippage_bps: number
  spread_bps: number
  market_impact_bps: number
  additional_execution_delay_bars: 0 | 1 | 2
  strategy_parameters: Record<string, number>
}

export interface WhatIfMetrics {
  total_return: number
  sharpe: number
  max_drawdown: number
  turnover: number
  trade_count: number
  net_pnl: number
}

export interface WhatIfMetricDeltas {
  total_return: number
  sharpe: number
  max_drawdown: number
  turnover: number
  trade_count: number
  net_pnl: number
}

export interface WhatIfScenario {
  baseline_inputs: WhatIfInputs
  inputs: WhatIfInputs
  baseline_metrics: WhatIfMetrics
  stressed_metrics: WhatIfMetrics
  deltas: WhatIfMetricDeltas
  unfilled_signal_count: number
  verdict: 'LOWER_NET_PNL' | 'HIGHER_NET_PNL' | 'UNCHANGED_NET_PNL'
  evidence: string[]
  calculation_details: string[]
}

export interface WhatIfSupport {
  status: 'AVAILABLE' | 'NOT_SUPPORTED'
  baseline_inputs: WhatIfInputs | null
  baseline_metrics: WhatIfMetrics | null
  parameter: {
    key: string
    label: string
    value_type: 'integer' | 'number'
    current_value: number
    minimum: number
    maximum: number | null
    step: number
    unit: string
  } | null
  calculation_details: string[]
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
    spread_bps?: number
    market_impact_bps?: number
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
    spread_bps?: number
    market_impact_bps?: number
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
  statistical_diagnostics?: {
    returns: ReturnDiagnostics
    pair_mean_reversion: PairMeanReversionEvidence | null
  } | null
  volatility_diagnostics?: VolatilityDiagnostics | null
  what_if?: WhatIfSupport | null
  regime_diagnostics?: RegimeDiagnostics | null
  failure_fingerprint?: FailureFingerprint | null
}
