import type { CorporateActionDataset, CreateCorporateActionDataset, CreateHistoricalUniverse, HistoricalUniverse } from '../types/corporateAction'
import { readJson } from './client'

export async function getCorporateActionDatasets(): Promise<CorporateActionDataset[]> {
  return readJson(await fetch('/api/corporate-actions'), 'Corporate Action list') as Promise<CorporateActionDataset[]>
}

export async function getHistoricalUniverses(): Promise<HistoricalUniverse[]> {
  return readJson(await fetch('/api/universes'), 'Historical Universe list') as Promise<HistoricalUniverse[]>
}

export async function createCorporateActionDataset(request: CreateCorporateActionDataset): Promise<CorporateActionDataset> {
  return readJson(await fetch('/api/corporate-actions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }), 'Corporate Action import') as Promise<CorporateActionDataset>
}

export async function createHistoricalUniverse(request: CreateHistoricalUniverse): Promise<HistoricalUniverse> {
  return readJson(await fetch('/api/universes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }), 'Historical Universe import') as Promise<HistoricalUniverse>
}
