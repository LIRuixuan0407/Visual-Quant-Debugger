export type ResearchStage = 'RESEARCH' | 'VALIDATION' | 'HOLDOUT'

import type { FundamentalFieldSnapshot, FundamentalSnapshot } from './fundamental'

export interface FactorParameter {
  key: string
  label: string
  description: string
  default_value: number
  minimum: number
  maximum: number | null
  step: number
  unit: string
}

export interface FactorDefinition {
  factor_id: string
  name: string
  version: string
  formula: string
  description: string
  parameters: FactorParameter[]
  required_fields: string[]
  lookback: number
  availability: string
  direction: 'HIGH' | 'LOW'
  category: 'PRICE_VOLUME' | 'VALUE' | 'QUALITY' | 'GROWTH' | 'LEVERAGE' | 'MIXED'
  data_source: 'MARKET' | 'FUNDAMENTAL' | 'MIXED'
  required_fundamental_fields: string[]
  origin: 'BUILT_IN' | 'CUSTOM'
  source_path: string | null
  source_fingerprint: string
}

export interface FactorImportResult {
  factor: FactorDefinition
  checks: string[]
  security_model: string
}

export interface FactorComponent {
  factor_id: string
  weight: number
  parameters: Record<string, number>
}

export interface ResearchPeriod { start: string; end: string }
export interface ResearchPeriods { research: ResearchPeriod; validation: ResearchPeriod; holdout: ResearchPeriod }

export interface FactorTimelinePoint {
  timestamp: string
  ic: number | null
  rank_ic: number | null
  quantile_returns: Array<number | null>
  long_short_spread: number | null
}

export interface HorizonEvaluation {
  horizon: 1 | 5 | 20
  observation_count: number
  cross_section_count: number
  ic: number | null
  rank_ic: number | null
  ic_stability: number | null
  rank_ic_stability: number | null
  quantile_returns: Array<number | null>
  long_short_spread: number | null
  turnover: number | null
  coverage: number
  monotonic: boolean
  timeline: FactorTimelinePoint[]
}

export interface PeriodEvaluation {
  stage: ResearchStage
  period: ResearchPeriod
  horizons: HorizonEvaluation[]
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

export interface FactorObservation {
  symbol: string
  timestamp: string
  factor_id: string
  value: number
  window_start: string
  window_end: string
  available_at: string
  future_returns: Record<string, number | null>
  future_return_timestamps: Record<string, string | null>
  dependencies: DataDependency[]
  fundamental_inputs: FundamentalFieldSnapshot[]
  future_data_used: false
}

export interface FactorStrategyArtifact {
  strategy_id: string
  research_id: string
  dataset_id: string
  source_fingerprint: string
  created_at: string
}

export interface FactorResearchRecord {
  research_id: string
  name: string
  created_at: string
  dataset_id: string
  dataset_name: string
  dataset_revision: string
  dataset_family_id?: string | null
  dataset_revision_number?: number
  factor: FactorDefinition
  parameters: Record<string, number>
  components: FactorComponent[]
  universe: string[]
  universe_id: string | null
  universe_mode: 'FIXED_UNIVERSE' | 'STATIC' | 'POINT_IN_TIME'
  survivorship_bias_free: boolean
  survivorship_warning: string
  periods: ResearchPeriods
  revealed_stage: ResearchStage
  evaluations: PeriodEvaluation[]
  factor_observation_count: number
  sample_observations: FactorObservation[]
  fundamental_dataset_id: string | null
  corporate_action_dataset_id: string | null
  price_adjustment_policy: 'RAW' | 'SPLIT_ADJUSTED'
  fundamental_provider: string | null
  restatement_safe: boolean
  restatement_warning: string | null
  strategy: FactorStrategyArtifact | null
}

export interface FactorResearchSummary {
  research_id: string
  name: string
  created_at: string
  dataset_id: string
  factor_id: string
  symbols: number
  revealed_stage: ResearchStage
  research_ic: number | null
  research_rank_ic: number | null
  factor_category: FactorDefinition['category']
  data_source: FactorDefinition['data_source']
  factor_origin: FactorDefinition['origin']
  direction: FactorDefinition['direction']
}

export interface HistoricalSecurityRow {
  symbol: string
  company: string
  close: number
  return_1d: number | null
  volume: number | null
  volatility_20d: number | null
  high_low_range: number | null
  average_volume_20d: number | null
}

export interface HistoricalMarketView {
  dataset_id: string
  dataset_revision: string
  source: string
  requested_as_of: string
  as_of: string
  universe_id: string | null
  universe_source: string | null
  universe_mode: 'FIXED_UNIVERSE' | 'STATIC' | 'POINT_IN_TIME'
  survivorship_bias_free: boolean
  universe_disclosure: string | null
  cross_section: HistoricalSecurityRow[]
  selected_symbol: string
  trend: Array<{ timestamp: string; close: number; volume: number | null }>
  fundamentals: FundamentalSnapshot | null
}
