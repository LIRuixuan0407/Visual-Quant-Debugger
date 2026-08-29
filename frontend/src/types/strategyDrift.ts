export type DriftBaselineType = 'RUN' | 'SNAPSHOT'
export type DriftObservedType = 'FORWARD_SESSION' | 'PAPER_SESSION' | 'PAPER_RUN'
export type DriftDimension = 'FACTOR' | 'SIGNAL' | 'TURNOVER' | 'EXPOSURE' | 'PERFORMANCE'
export type DriftMetricStatus = 'STABLE' | 'WATCH' | 'DRIFT' | 'INSUFFICIENT_EVIDENCE'
export type DriftOverallStatus = 'STABLE' | 'WATCH' | 'DRIFT' | 'INCOMPLETE'
export type DriftComparability = 'STRICTLY_COMPARABLE' | 'CONTEXTUALLY_COMPARABLE' | 'DESCRIPTIVE_ONLY' | 'CONFIGURATION_CHANGED'

export interface CreateStrategyDriftReport {
  baseline_type: DriftBaselineType
  baseline_id: string
  observed_type: DriftObservedType
  observed_id: string
  window_bars: number
}

export interface DriftSource {
  source_type: DriftBaselineType | DriftObservedType
  source_id: string
  resolved_run_id: string | null
  trace_id: string | null
  strategy_id: string
  strategy_fingerprint: string | null
  parameters: Record<string, string | number | boolean>
  execution_model: string
  runtime: string
  dataset_id: string
  dataset_revision: string | null
  sample_size: number
  observed_until: string | null
  status: 'COMPLETED' | 'PARTIAL'
}

export interface DriftComparabilityCheck {
  field: 'strategy_id' | 'strategy_fingerprint' | 'parameters' | 'execution_model' | 'runtime'
  baseline_value: string
  observed_value: string
  same: boolean
  blocking: boolean
}

export interface DriftMetric {
  metric: string
  baseline_value: number | null
  observed_value: number | null
  relative_change: number | null
  normalized_distance: number | null
  status: DriftMetricStatus
}

export interface DriftDimensionReport {
  dimension: DriftDimension
  status: DriftMetricStatus
  metrics: DriftMetric[]
  first_drift_at: string | null
  first_drift_event_id: string | null
  evidence: string[]
}

export interface DriftWindowDimension {
  dimension: DriftDimension
  status: DriftMetricStatus
  maximum_normalized_distance: number | null
}

export interface DriftTimelineWindow {
  window_index: number
  start_at: string
  end_at: string
  end_event_id: string
  sample_size: number
  complete: boolean
  dimensions: DriftWindowDimension[]
}

export interface StrategyDriftReport {
  drift_report_id: string
  drift_rule_version: '1.0'
  baseline_type: DriftBaselineType
  baseline_id: string
  observed_type: DriftObservedType
  observed_id: string
  created_at: string
  window_bars: number
  baseline: DriftSource
  observed: DriftSource
  comparability: DriftComparability
  comparability_checks: DriftComparabilityCheck[]
  overall_status: DriftOverallStatus
  dimensions: DriftDimensionReport[]
  timeline: DriftTimelineWindow[]
  first_drift_at: string | null
  first_drift_dimension: DriftDimension | null
  first_drift_event_id: string | null
  disclosure: string
}

export interface StrategyDriftSummary {
  drift_report_id: string
  baseline_type: DriftBaselineType
  baseline_id: string
  observed_type: DriftObservedType
  observed_id: string
  created_at: string
  comparability: DriftComparability
  overall_status: DriftOverallStatus
  first_drift_at: string | null
  first_drift_dimension: DriftDimension | null
  sample_size: number
}
