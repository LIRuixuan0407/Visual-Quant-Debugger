import { readJson } from './client'
import type { DatasetDefinition, StockSecurity, StockSnapshot } from '../types/dataset'
import { addCreatedObjectToCurrentWorkspace } from './workspaces'

export type MarketRegion = 'CN' | 'HK' | 'US'
export type MarketProvider = 'tdx' | 'alpaca'
export type MarketFeed = 'tdx' | 'iex' | 'sip'

export async function searchStocks(
  query: string,
  options: { provider?: MarketProvider; market?: MarketRegion } = {},
): Promise<StockSecurity[]> {
  const provider = options.provider ?? 'tdx'
  const market = options.market ?? 'CN'
  const endpoint = `/api/market-data/stocks/search?q=${encodeURIComponent(query)}&provider=${provider}&market=${market}`
  const response = await fetch(endpoint)
  const body = await readJson(response, `GET ${endpoint}`)
  if (!Array.isArray(body)) throw new Error('Stock search returned malformed data.')
  return body as StockSecurity[]
}

export async function getStockSnapshot(
  symbol: string,
  options: { provider?: MarketProvider; market?: MarketRegion; feed?: MarketFeed } = {},
): Promise<StockSnapshot> {
  const provider = options.provider ?? 'tdx'
  const market = options.market ?? 'CN'
  const feed = options.feed ?? (provider === 'tdx' ? 'tdx' : 'iex')
  const endpoint = `/api/market-data/stocks/${encodeURIComponent(symbol)}/snapshot?provider=${provider}&market=${market}&feed=${feed}`
  const response = await fetch(endpoint)
  return readJson(response, `GET ${endpoint}`) as Promise<StockSnapshot>
}

export async function saveHistoricalDataset(input: {
  name: string
  symbols: string[]
  start: string
  end: string
  timeframe: '1Min' | '5Min' | '15Min' | '1Hour' | '1Day'
  provider?: MarketProvider
  market?: MarketRegion
  feed?: MarketFeed
  adjustment?: 'NONE' | 'QFQ' | 'HFQ'
}): Promise<DatasetDefinition> {
  const provider = input.provider ?? (input.feed === 'iex' || input.feed === 'sip' ? 'alpaca' : 'tdx')
  const market = input.market ?? (provider === 'alpaca' ? 'US' : 'CN')
  const feed = input.feed ?? (provider === 'alpaca' ? 'iex' : 'tdx')
  const response = await fetch('/api/market-data/historical-datasets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...input,
      provider,
      market,
      feed,
      adjustment: input.adjustment ?? 'NONE',
    }),
  })
  const created = await readJson(response, 'POST /api/market-data/historical-datasets') as DatasetDefinition
  await addCreatedObjectToCurrentWorkspace('DATASET', created.dataset_id)
  return created
}
