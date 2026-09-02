export interface DataQualityReport {
  status: 'VALID' | 'WARNING'
  rows: number
  symbols: number
  start: string
  end: string
  duplicates: number
  missing_required_values: number
  rows_reordered: number
  alignment_gaps: number
  timezone: string
  issues: string[]
}

export interface DatasetDefinition {
  dataset_id: string
  name: string
  source_type: 'CSV' | 'BUILT_IN' | 'PROVIDER'
  timezone: string
  frequency: string
  symbols: string[]
  fields: string[]
  row_count: number
  synchronized_bar_count: number
  start_time: string
  end_time: string
  created_at: string
  content_fingerprint: string
  dataset_family_id?: string | null
  revision?: number
  parent_dataset_id?: string | null
  revision_reason?: string | null
  source_timezone: string
  column_mapping: Record<string, string>
  quality: DataQualityReport
  security_names?: Record<string, string>
  provenance?: {
    provider: string
    feed: string
    requested_symbols: string[]
    requested_start: string
    requested_end: string
    retrieved_at: string
    market_timestamp_start: string
    market_timestamp_end: string
    market?: 'CN' | 'HK' | 'US' | null
    adjustment?: 'NONE' | 'QFQ' | 'HFQ' | null
  } | null
}

export interface StockSecurity {
  symbol: string
  name: string
  exchange: string
  status: 'active' | 'inactive'
  tradable: boolean
  fractionable: boolean
  market?: 'CN' | 'HK' | 'US'
  currency?: 'CNY' | 'HKD' | 'USD'
  lot_size?: number
}

export interface StockSnapshot {
  security: StockSecurity
  provider: 'tdx' | 'alpaca'
  feed: 'tdx' | 'iex' | 'sip'
  market_timestamp: string
  received_at: string
  latest_trade_price: number | null
  latest_trade_size: number | null
  minute_bar: { close: number; volume: number; event_time: string } | null
  daily_bar: { open: number; high: number; low: number; close: number; volume: number; event_time: string } | null
  market?: 'CN' | 'HK' | 'US'
  freshness_status?: 'LIVE' | 'DELAYED' | 'STALE' | 'CLOSED' | 'UNVERIFIED'
  freshness_seconds?: number | null
}

export interface DatasetPreview {
  preview_id: string
  filename: string
  columns: string[]
  rows: Array<Record<string, string>>
  detected_types: Record<string, string>
  detected_timezone: string | null
  candidate_mapping: Record<string, string>
}

export interface CompatibilityCheck {
  strategy_id: string
  dataset_id: string
  compatible: boolean
  required_fields: string[]
  provided_fields: string[]
  required_symbol_count: number | null
  provided_symbol_count: number
  required_symbols: string[]
  missing_symbols: string[]
  minimum_history: number
  synchronized_bar_count: number
  reasons: string[]
}


export interface DatasetFamily {
  dataset_family_id: string
  name: string
  created_at: string
  latest_dataset_id: string
  revision_count: number
}

export interface DatasetRevisionDiff {
  left_dataset_id: string
  right_dataset_id: string
  same_family: boolean
  fingerprint_changed: boolean
  symbols_added: string[]
  symbols_removed: string[]
  fields_added: string[]
  fields_removed: string[]
  start_changed: boolean
  end_changed: boolean
  rows_delta: number
  synchronized_bars_delta: number
  quality_changes: string[]
  provenance_changes: string[]
  data_view_changes: string[]
}
