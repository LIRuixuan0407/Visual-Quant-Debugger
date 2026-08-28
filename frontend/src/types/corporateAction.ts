export type CorporateActionType = 'SPLIT' | 'CASH_DIVIDEND' | 'DELISTING'
export type PriceAdjustmentPolicy = 'RAW' | 'SPLIT_ADJUSTED'

export interface CorporateAction {
  action_id: string
  symbol: string
  action_type: CorporateActionType
  effective_at: string
  announced_at: string | null
  available_at: string
  source: string
  evidence: string
  split_ratio: number | null
  cash_amount: number | null
  currency: string | null
  delisting_reason: string | null
  settlement_price: number | null
}

export interface CreateCorporateAction {
  action_id: string
  symbol: string
  action_type: CorporateActionType
  effective_at: string
  announced_at?: string | null
  available_at: string
  source: string
  evidence: string
  split_ratio?: number | null
  cash_amount?: number | null
  currency?: string | null
  delisting_reason?: string | null
  settlement_price?: number | null
}

export interface CreateCorporateActionDataset {
  name: string
  provider: string
  actions: CreateCorporateAction[]
  disclosure: string
}

export interface CorporateActionDataset {
  corporate_action_dataset_id: string
  name: string
  provider: string
  symbols: string[]
  start_time: string
  end_time: string
  retrieved_at: string
  content_fingerprint: string
  actions: CorporateAction[]
  point_in_time_safe: boolean
  disclosure: string
}

export interface UniverseMembershipProvenance {
  symbol: string
  source: string
  effective_from: string
  effective_to: string | null
  evidence: string
}

export interface UniverseSnapshot {
  effective_date: string
  symbols: string[]
  membership_provenance: UniverseMembershipProvenance[]
}

export interface CreateHistoricalUniverse {
  name: string
  source: string
  mode: 'STATIC' | 'POINT_IN_TIME'
  dataset_id?: string | null
  snapshots: UniverseSnapshot[]
  disclosure: string
}

export interface HistoricalUniverse {
  universe_id: string
  name: string
  source: string
  mode: 'STATIC' | 'POINT_IN_TIME'
  dataset_id: string | null
  created_at: string
  snapshots: UniverseSnapshot[]
  survivorship_bias_free: boolean
  disclosure: string
}
