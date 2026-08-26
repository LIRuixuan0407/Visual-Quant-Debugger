import type { RuntimeDescriptor, TraceScalar } from './trace'

export type RunStatus = 'RUNNING' | 'COMPLETED' | 'FAILED' | 'PARTIAL'
export type Comparability = 'STRICTLY_COMPARABLE' | 'CONTEXTUALLY_COMPARABLE' | 'DESCRIPTIVE_ONLY'

export interface StrategyRevision {
  strategy_id: string
  name: string
  version: string
  class_name: string
  source_fingerprint: string
  original_source_path: string
}

export interface DatasetRevision {
  dataset_id: string
  name: string
  content_fingerprint: string
  source_timezone: string
  symbols?: string[]
}

export interface ResearchPeriod {
  start: string | null
  end: string | null
  cutoff: string | null
}

export interface RunMetrics {
  total_return: number
  sharpe: number
  max_drawdown: number
  turnover: number
  trades: number
  final_equity: number
  fees: number
  slippage: number
  net_pnl: number
}

export interface RunAnnotations {
  display_name: string
  note: string
  tags: string[]
}

export interface RunListItem {
  run_id: string
  run_type: 'BACKTEST' | 'PAPER' | 'REFERENCE'
  trace_id: string | null
  status: RunStatus
  created_at: string
  completed_at: string | null
  strategy_id: string
  strategy_name: string
  strategy_fingerprint: string
  dataset_id: string
  dataset_name: string
  dataset_fingerprint: string
  parameters: Record<string, number>
  period: ResearchPeriod
  metrics: RunMetrics | null
  run_fingerprint: string
  reproduced_from_run_id: string | null
  annotations: RunAnnotations
  runtime?: RuntimeDescriptor
}

export interface RunListResponse {
  items: RunListItem[]
  total: number
  limit: number
  offset: number
}

export interface RunManifest {
  run_version: '1.0' | '1.1'
  run_id: string
  run_type: 'BACKTEST' | 'PAPER' | 'REFERENCE'
  run_fingerprint: string
  status: RunStatus
  created_at: string
  completed_at: string | null
  strategy: StrategyRevision
  dataset: DatasetRevision
  period: ResearchPeriod
  parameters: Record<string, number>
  execution_model: {
    execution_model_id: string
    version: string
    description: string
  }
  runtime?: RuntimeDescriptor
  engine: { python_version: string; platform: string; vqd_version: string }
  trace_version: '1.0'
  trace_id: string | null
  metrics: RunMetrics | null
  artifacts: {
    strategy_source_sha256: string
    trace_sha256: string | null
    diagnostics_sha256: string | null
    pnl_autopsy_sha256: string | null
    adapter_manifest_sha256?: string | null
    recorded_market_events_sha256?: string | null
    runtime_consistency_sha256?: string | null
  }
  failure: null | {
    strategy_id: string
    timestamp: string
    event_index: number
    exception_type: string
    message: string
    traceback: string
  }
  reproduced_from_run_id: string | null
}

export interface RunDetail {
  manifest: RunManifest
  annotations: RunAnnotations
  artifacts: {
    strategy_source: boolean
    trace: boolean
    diagnostics: boolean
    pnl_autopsy: boolean
    adapter_manifest?: boolean
    recorded_market_events?: boolean
    runtime_consistency?: boolean
  }
  integrity: 'VERIFIED'
  current_strategy_fingerprint: string | null
  current_source_matches: boolean | null
}

export interface ContextComparison {
  field: 'strategy_revision' | 'dataset_revision' | 'evaluation_period' | 'execution_model' | 'runtime'
  same: boolean
  values: string[]
}

export interface ParameterComparison {
  parameter: string
  values: Array<TraceScalar | null>
  changed: true
}

export interface MetricComparison {
  metric: string
  values: Array<number | null>
  differences_from_first: Array<number | null>
}

export interface BehaviorDiffRow {
  timestamp: string
  values: string[]
  event_ids: Array<string | null>
}

export interface RunComparisonReport {
  report_version: '1.0'
  run_ids: string[]
  comparability: Comparability
  context_diff: ContextComparison[]
  parameter_diff: ParameterComparison[]
  metric_diff: MetricComparison[]
  equity_comparison: Array<{ timestamp: string; values: number[] }>
  signal_comparison: BehaviorDiffRow[]
  execution_comparison: BehaviorDiffRow[]
  first_behavioral_divergence: null | {
    status: 'DIVERGENCE' | 'NO_BEHAVIORAL_DIVERGENCE'
    kind: 'FEATURE' | 'CONDITION' | 'SIGNAL' | 'POSITION' | 'ORDER' | 'EXECUTION' | null
    timestamp: string | null
    event_ids: Array<string | null>
    summary: string
    run_values: string[]
    associated_parameter_differences: string[]
  }
  first_computational_divergence?: RunComparisonReport['first_behavioral_divergence']
  first_decision_divergence?: RunComparisonReport['first_behavioral_divergence']
  first_trading_divergence?: RunComparisonReport['first_behavioral_divergence']
}

export interface StrategySourceArtifact {
  run_id: string
  filename: 'strategy.py'
  sha256: string
  source: string
}

export interface RunValidationReport {
  report_version: '1.0'
  report_id: string
  backtest_run_id: string
  paper_run_id: string
  reference_run_id: string
  reference_trace_id: string | null
  paper_trace_id: string | null
  historical_comparability: Comparability
  strict_recorded_feed_status: 'MATCH' | 'FIRST_DIVERGENCE' | 'NO_TRACE'
  checks: Array<{
    field: 'strategy_revision' | 'parameters' | 'symbols' | 'market_path' | 'execution_model'
    same: boolean
    reference_value: string
    paper_value: string
  }>
  first_divergence: {
    status: 'MATCH' | 'DIVERGENCE'
    layer: 'DATA' | 'FEATURE' | 'DECISION' | 'ORDER' | 'EXECUTION' | 'PORTFOLIO' | 'P&L' | null
    timestamp: string | null
    reference_value: string
    paper_value: string
    difference: string
    reference_event_id: string | null
    paper_event_id: string | null
  }
  pnl_attribution: {
    total_difference: number
    decision_difference: number | null
    execution_price_difference: number | null
    fees: number
    slippage: number
    residual_unattributed: number
    status: 'RECONCILED' | 'PARTIALLY_ATTRIBUTED' | 'NOT_AVAILABLE'
  }
  note: string
}
