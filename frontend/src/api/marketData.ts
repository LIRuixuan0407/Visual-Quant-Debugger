import { readJson } from './client'
import type { DatasetDefinition, StockSecurity, StockSnapshot } from '../types/dataset'
import { addCreatedObjectToCurrentWorkspace } from './workspaces'

export async function searchStocks(query: string): Promise<StockSecurity[]> {
  const endpoint = `/api/market-data/stocks/search?q=${encodeURIComponent(query)}`
  const response = await fetch(endpoint)
  const body = await readJson(response, `GET ${endpoint}`)
  if (!Array.isArray(body)) throw new Error('Stock search returned malformed data.')
  return body as StockSecurity[]
}

export async function getStockSnapshot(symbol: string, feed: 'iex' | 'sip'): Promise<StockSnapshot> {
  const endpoint = `/api/market-data/stocks/${encodeURIComponent(symbol)}/snapshot?feed=${feed}`
  const response = await fetch(endpoint)
  return readJson(response, `GET ${endpoint}`) as Promise<StockSnapshot>
}

export async function saveHistoricalDataset(input: {
  name: string
  symbols: string[]
  start: string
  end: string
  timeframe: '1Min' | '5Min' | '15Min' | '1Hour' | '1Day'
  feed: 'iex' | 'sip'
}): Promise<DatasetDefinition> {
  const response = await fetch('/api/market-data/historical-datasets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  const created = await readJson(response, 'POST /api/market-data/historical-datasets') as DatasetDefinition
  await addCreatedObjectToCurrentWorkspace('DATASET', created.dataset_id)
  return created
}
