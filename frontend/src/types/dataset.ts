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
  } | null
}

export interface StockSecurity {
  symbol: string
  name: string
  exchange: string
  status: 'active' | 'inactive'
  tradable: boolean
  fractionable: boolean
}

export interface StockSnapshot {
  security: StockSecurity
  provider: 'alpaca'
  feed: 'iex' | 'sip'
  market_timestamp: string
  received_at: string
  latest_trade_price: number | null
  latest_trade_size: number | null
  minute_bar: { close: number; volume: number; event_time: string } | null
  daily_bar: { open: number; high: number; low: number; close: number; volume: number; event_time: string } | null
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
