import type { ProductPage } from '../components/ProductNav'
import type { SearchEntityType, SearchOpenTarget } from '../types/search'

const PAGE_BY_TYPE: Record<SearchEntityType, ProductPage> = {
  DATASET: 'data',
  FACTOR: 'factors',
  FACTOR_RESEARCH: 'factors',
  FACTOR_RELATIONSHIP: 'relationships',
  WALK_FORWARD: 'walk-forward',
  PORTFOLIO_RESEARCH: 'portfolio',
  HYPOTHESIS: 'workspace',
  STRATEGY: 'strategy',
  RUN: 'runs',
  TRACE: 'replay',
  SNAPSHOT: 'snapshots',
}

export function resolveSearchTarget(item: SearchOpenTarget): { page: ProductPage; route: string } {
  return { page: PAGE_BY_TYPE[item.entity_type], route: item.route }
}
