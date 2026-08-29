export type SearchEntityType = 'DATASET' | 'UNIVERSE' | 'CORPORATE_ACTION_DATASET' | 'FACTOR' | 'FACTOR_RESEARCH' | 'FACTOR_RELATIONSHIP' | 'WALK_FORWARD' | 'PORTFOLIO_RESEARCH' | 'HYPOTHESIS' | 'STRATEGY' | 'RUN' | 'TRACE' | 'SNAPSHOT' | 'DRIFT_REPORT'
export type SearchScalar = string | number | boolean | null

export const SEARCH_ENTITY_TYPES: SearchEntityType[] = ['DATASET', 'UNIVERSE', 'CORPORATE_ACTION_DATASET', 'FACTOR', 'FACTOR_RESEARCH', 'FACTOR_RELATIONSHIP', 'WALK_FORWARD', 'PORTFOLIO_RESEARCH', 'HYPOTHESIS', 'STRATEGY', 'RUN', 'TRACE', 'SNAPSHOT', 'DRIFT_REPORT']

export interface SearchResult {
  entity_type: SearchEntityType
  entity_id: string
  title: string
  subtitle: string
  score: number
  route: string
  highlights: string[]
  metadata: Record<string, SearchScalar>
}

export interface GlobalSearchResponse {
  query: string
  normalized_query: string
  results: SearchResult[]
}

export interface RecentSearchItem {
  entity_type: SearchEntityType
  entity_id: string
  title: string
  route: string
  last_opened_at: string
}

export interface SearchOpenTarget {
  entity_type: SearchEntityType
  entity_id: string
  title: string
  route: string
  metadata?: Record<string, SearchScalar>
}
