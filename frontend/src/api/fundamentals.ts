import { readJson } from './client'
import type {
  FundamentalDatasetSummary,
  FundamentalProviderInfo,
  HistoricalUniverse,
} from '../types/fundamental'

export async function getFundamentalProviders(): Promise<FundamentalProviderInfo[]> {
  const response = await fetch('/api/fundamental-providers')
  return readJson(response, 'GET /api/fundamental-providers') as Promise<FundamentalProviderInfo[]>
}

export async function getFundamentalDatasets(): Promise<FundamentalDatasetSummary[]> {
  const response = await fetch('/api/fundamental-datasets')
  return readJson(response, 'GET /api/fundamental-datasets') as Promise<FundamentalDatasetSummary[]>
}

export async function downloadSecFundamentals(input: {
  name: string
  symbols: string[]
  start: string
  end: string
}): Promise<FundamentalDatasetSummary> {
  const response = await fetch('/api/fundamental-datasets/sec-companyfacts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return readJson(response, 'POST SEC fundamentals') as Promise<FundamentalDatasetSummary>
}

export async function getUniverses(): Promise<HistoricalUniverse[]> {
  const response = await fetch('/api/universes')
  return readJson(response, 'GET /api/universes') as Promise<HistoricalUniverse[]>
}
