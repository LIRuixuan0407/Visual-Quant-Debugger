import { readJson } from './client'
import type { GlobalSearchResponse, SearchEntityType } from '../types/search'

export async function globalSearch(query: string, types: SearchEntityType[] = [], limit = 20, workspaceId?: string): Promise<GlobalSearchResponse> {
  const parameters = new URLSearchParams({ q: query, limit: String(limit) })
  for (const entityType of types) parameters.append('types', entityType)
  if (workspaceId) parameters.set('workspace_id', workspaceId)
  return readJson(await fetch(`/api/search?${parameters.toString()}`), 'GET /api/search') as Promise<GlobalSearchResponse>
}
