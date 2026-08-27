import { readJson } from './client'
import type { LineageDirection, LineageNodeType, ResearchLineageGraph, ResearchLineageSummary } from '../types/researchLineage'

export interface ResearchLineageQuery {
  root_type?: LineageNodeType | null
  root_id?: string | null
  direction?: LineageDirection
  max_depth?: number
  node_types?: LineageNodeType[]
}

export async function getResearchLineage(query: ResearchLineageQuery = {}): Promise<ResearchLineageGraph> {
  const parameters = new URLSearchParams()
  if (query.root_type && query.root_id) {
    parameters.set('root_type', query.root_type)
    parameters.set('root_id', query.root_id)
  }
  if (query.direction) parameters.set('direction', query.direction)
  if (query.max_depth) parameters.set('max_depth', String(query.max_depth))
  for (const nodeType of query.node_types ?? []) parameters.append('node_types', nodeType)
  const suffix = parameters.size > 0 ? `?${parameters.toString()}` : ''
  return readJson(await fetch(`/api/research-lineage${suffix}`), 'GET /api/research-lineage') as Promise<ResearchLineageGraph>
}

export async function getResearchLineageSummary(): Promise<ResearchLineageSummary> {
  return readJson(await fetch('/api/research-lineage/summary'), 'GET /api/research-lineage/summary') as Promise<ResearchLineageSummary>
}
