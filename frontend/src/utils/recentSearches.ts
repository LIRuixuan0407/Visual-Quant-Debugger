import { SEARCH_ENTITY_TYPES, type RecentSearchItem, type SearchEntityType, type SearchOpenTarget } from '../types/search'

const RECENT_KEY = 'vqd-global-search-recent-v1'
const RECENT_LIMIT = 8

function isEntityType(value: unknown): value is SearchEntityType {
  return typeof value === 'string' && SEARCH_ENTITY_TYPES.includes(value as SearchEntityType)
}

function isRecentItem(value: unknown): value is RecentSearchItem {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return isEntityType(item.entity_type)
    && typeof item.entity_id === 'string'
    && typeof item.title === 'string'
    && typeof item.route === 'string'
    && typeof item.last_opened_at === 'string'
}

export function readRecentSearches(): RecentSearchItem[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(RECENT_KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter(isRecentItem).slice(0, RECENT_LIMIT) : []
  } catch {
    return []
  }
}

export function recordRecentSearch(item: SearchOpenTarget): RecentSearchItem[] {
  const next: RecentSearchItem = {
    entity_type: item.entity_type,
    entity_id: item.entity_id,
    title: item.title,
    route: item.route,
    last_opened_at: new Date().toISOString(),
  }
  const recent = [next, ...readRecentSearches().filter((current) => current.entity_type !== next.entity_type || current.entity_id !== next.entity_id)].slice(0, RECENT_LIMIT)
  try { window.localStorage.setItem(RECENT_KEY, JSON.stringify(recent)) } catch { /* Recent search is optional. */ }
  return recent
}
