export type FundamentalStatus = 'AVAILABLE' | 'MISSING' | 'NOT_YET_REPORTED' | 'STALE' | 'RESTATED'

export interface FundamentalProviderInfo {
  provider_id: string
  name: string
  fields: string[]
  requires_credentials: boolean
  point_in_time_semantics: string
  restatement_safe: boolean
  status: 'AVAILABLE' | 'BLOCKED'
  detail: string
}

export interface FundamentalDatasetSummary {
  fundamental_dataset_id: string
  name: string
  provider: string
  symbols: string[]
  fields: string[]
  start_time: string
  end_time: string
  retrieved_at: string
  observation_count: number
  point_in_time_safe: boolean
  restatement_safe: boolean
  disclosure: string
}

export interface FundamentalFieldSnapshot {
  field: string
  status: FundamentalStatus
  value: number | null
  unit: string | null
  fiscal_period: string | null
  report_date: string | null
  filed_at: string | null
  available_at: string | null
  used_at: string
  age_days: number | null
  form: string | null
  accession: string | null
  is_restatement: boolean
}

export interface FundamentalSnapshot {
  fundamental_dataset_id: string
  provider: string
  symbol: string
  used_at: string
  restatement_safe: boolean
  fields: FundamentalFieldSnapshot[]
}

export interface UniverseMembershipProvenance {
  symbol: string
  source: string
  effective_from: string
  effective_to: string | null
  evidence: string
}

export interface HistoricalUniverse {
  universe_id: string
  name: string
  source: string
  mode: 'STATIC' | 'POINT_IN_TIME'
  dataset_id: string | null
  created_at: string
  snapshots: Array<{
    effective_date: string
    symbols: string[]
    membership_provenance: UniverseMembershipProvenance[]
  }>
  survivorship_bias_free: boolean
  disclosure: string
}
