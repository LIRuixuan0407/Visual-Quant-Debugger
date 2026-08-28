import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { DatasetDefinition } from '../../types/dataset'
import { I18nProvider } from '../../i18n/I18nProvider'
import DataPage from './DataPage'

function response(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const dataset: DatasetDefinition = {
  dataset_id: 'dataset-existing', name: 'Existing research.csv', source_type: 'CSV',
  timezone: 'UTC', frequency: '1D', symbols: ['AAPL'], fields: ['close'], row_count: 2,
  synchronized_bar_count: 2, start_time: '2025-01-01T00:00:00Z', end_time: '2025-01-02T00:00:00Z',
  created_at: '2025-01-03T00:00:00Z', content_fingerprint: 'sha256:existing',
  source_timezone: 'UTC', column_mapping: { timestamp: 'date', symbol: 'ticker', close: 'price' },
  quality: { status: 'VALID', rows: 2, symbols: 1, start: '2025-01-01T00:00:00Z', end: '2025-01-02T00:00:00Z', duplicates: 0, missing_required_values: 0, rows_reordered: 0, alignment_gaps: 0, timezone: 'UTC', issues: [] },
}

afterEach(() => vi.restoreAllMocks())

test('lists datasets, previews CSV mapping, declares timezone, and imports quality metadata', async () => {
  const imported = { ...dataset, dataset_id: 'dataset-imported', name: 'Mapped prices', source_timezone: 'Asia/Hong_Kong' }
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.endsWith('/preview') && url.includes('dataset-existing')) return response({ rows: [{ timestamp: '2025-01-01T00:00:00Z', symbol: 'AAPL', close: 100 }] })
    if (url === '/api/datasets/import/preview') return response({
      preview_id: 'preview-1', filename: 'mapped.csv', columns: ['date', 'ticker', 'price'],
      rows: [{ date: '2025-01-01', ticker: 'AAPL', price: '100' }],
      detected_types: { date: 'datetime', ticker: 'string', price: 'number' },
      detected_timezone: null, candidate_mapping: { timestamp: 'date', symbol: 'ticker', close: 'price' },
    })
    if (url === '/api/datasets/import') return response(imported, 201)
    throw new Error(`Unexpected ${url}`)
  })
  const onImported = vi.fn()
  render(<DataPage datasets={[dataset]} onImported={onImported} />)
  expect(screen.getByText('Existing research.csv')).toBeInTheDocument()
  expect(await screen.findByText('100')).toBeInTheDocument()
  const timestamp = screen.getByText('Jan 01, 2025 · 00:00 UTC')
  expect(timestamp).toHaveAttribute('datetime', '2025-01-01T00:00:00Z')
  expect(screen.queryByText('2025-01-01T00:00:00Z')).not.toBeInTheDocument()

  const file = new File(['date,ticker,price\n2025-01-01,AAPL,100\n'], 'mapped.csv', { type: 'text/csv' })
  fireEvent.change(screen.getByLabelText('Choose CSV'), { target: { files: [file] } })
  expect(await screen.findByRole('heading', { name: 'Import preview · mapped.csv' })).toBeInTheDocument()
  expect(screen.getByLabelText('Map timestamp')).toHaveValue('date')
  expect(screen.getByLabelText('Map symbol')).toHaveValue('ticker')
  expect(screen.getByLabelText('Map close')).toHaveValue('price')
  fireEvent.change(screen.getByLabelText('Dataset name'), { target: { value: 'Mapped prices' } })
  fireEvent.change(screen.getByLabelText('Source timezone'), { target: { value: 'Asia/Hong_Kong' } })
  fireEvent.click(screen.getByRole('button', { name: 'Validate & Import' }))
  await waitFor(() => expect(onImported).toHaveBeenCalledWith(imported))
  const importCall = fetchMock.mock.calls.find(([url]) => url === '/api/datasets/import')
  expect(JSON.parse((importCall?.[1] as RequestInit).body as string)).toMatchObject({
    name: 'Mapped prices', timezone: 'Asia/Hong_Kong',
    mapping: { timestamp: 'date', symbol: 'ticker', close: 'price' },
  })
})

test('shows precise CSV validation errors', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.includes('dataset-existing')) return response({ rows: [] })
    return response({ detail: 'Duplicate (symbol, timestamp) bars detected: 1; import rejected' }, 422)
  })
  render(<DataPage datasets={[dataset]} onImported={() => undefined} />)
  const file = new File(['bad'], 'bad.csv', { type: 'text/csv' })
  fireEvent.change(screen.getByLabelText('Choose CSV'), { target: { files: [file] } })
  expect(await screen.findByRole('alert')).toHaveTextContent('Duplicate (symbol, timestamp) bars detected')
})

test('searches a real stock, shows provider identity, and saves historical bars as a Dataset', async () => {
  const providerDataset: DatasetDefinition = {
    ...dataset,
    dataset_id: 'dataset-aapl-real',
    name: 'AAPL · 1Day · Alpaca IEX',
    source_type: 'PROVIDER',
    fields: ['open', 'high', 'low', 'close', 'volume'],
    provenance: {
      provider: 'alpaca', feed: 'iex', requested_symbols: ['AAPL'],
      requested_start: '2024-01-01T00:00:00Z', requested_end: '2024-12-31T23:59:59Z',
      retrieved_at: '2025-01-01T00:00:00Z', market_timestamp_start: '2024-01-02T21:00:00Z', market_timestamp_end: '2024-12-31T21:00:00Z',
    },
  }
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.includes('dataset-existing') && url.endsWith('/preview')) return response({ rows: [] })
    if (url === '/api/market-data/stocks/search?q=Apple') return response([{ symbol: 'AAPL', name: 'Apple Inc.', exchange: 'NASDAQ', status: 'active', tradable: true, fractionable: true }])
    if (url === '/api/market-data/stocks/AAPL/snapshot?feed=iex') return response({ security: { symbol: 'AAPL', name: 'Apple Inc.', exchange: 'NASDAQ', status: 'active', tradable: true, fractionable: true }, provider: 'alpaca', feed: 'iex', market_timestamp: '2026-08-24T20:00:00Z', received_at: '2026-08-24T20:00:01Z', latest_trade_price: 227.16, latest_trade_size: 100, minute_bar: null, daily_bar: null })
    if (url === '/api/market-data/historical-datasets' && init?.method === 'POST') return response(providerDataset, 201)
    throw new Error(`Unexpected ${init?.method ?? 'GET'} ${url}`)
  })
  const onImported = vi.fn()
  render(<DataPage datasets={[dataset]} onImported={onImported} />)
  fireEvent.change(screen.getByLabelText('Symbol or company'), { target: { value: 'Apple' } })
  fireEvent.click(screen.getByRole('button', { name: 'Search' }))
  fireEvent.click((await screen.findByText('Apple Inc.')).closest('button')!)
  expect(await screen.findByText('$227.16')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save as Dataset' }))
  await waitFor(() => expect(onImported).toHaveBeenCalledWith(providerDataset))
  const request = fetchMock.mock.calls.find(([url]) => url === '/api/market-data/historical-datasets')
  expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ symbols: ['AAPL'], timeframe: '1Day', feed: 'iex' })
})

test('shows Corporate Action evidence and point-in-time Universe history', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.includes('dataset-existing') && url.endsWith('/preview')) return response({ rows: [] })
    if (url === '/api/corporate-actions') return response([{
      corporate_action_dataset_id: 'corporate-actions-real', name: 'Exchange actions', provider: 'NASDAQ', symbols: ['AAPL', 'OLD'],
      start_time: '2024-06-01T00:00:00Z', end_time: '2025-01-03T00:00:00Z', retrieved_at: '2025-01-05T00:00:00Z', content_fingerprint: 'sha256:actions', point_in_time_safe: false,
      disclosure: 'Official notices only.',
      actions: [
        { action_id: 'split-aapl', symbol: 'AAPL', action_type: 'SPLIT', effective_at: '2024-06-01T00:00:00Z', announced_at: '2024-05-01T00:00:00Z', available_at: '2024-05-01T00:00:00Z', source: 'NASDAQ', evidence: 'Split bulletin', split_ratio: 4, cash_amount: null, currency: null, delisting_reason: null, settlement_price: null },
        { action_id: 'dividend-aapl', symbol: 'AAPL', action_type: 'CASH_DIVIDEND', effective_at: '2024-08-01T00:00:00Z', announced_at: '2024-07-01T00:00:00Z', available_at: '2024-07-01T00:00:00Z', source: 'NASDAQ', evidence: 'Dividend bulletin', split_ratio: null, cash_amount: 0.25, currency: 'USD', delisting_reason: null, settlement_price: null },
        { action_id: 'delisting-old', symbol: 'OLD', action_type: 'DELISTING', effective_at: '2025-01-03T00:00:00Z', announced_at: null, available_at: '2025-01-04T00:00:00Z', source: 'NASDAQ', evidence: 'Delisting bulletin', split_ratio: null, cash_amount: null, currency: null, delisting_reason: 'Bankruptcy', settlement_price: null },
      ],
    }])
    if (url === '/api/universes') return response([{
      universe_id: 'universe-history', name: 'Historical index', source: 'Index archive', mode: 'POINT_IN_TIME', dataset_id: 'dataset-existing', created_at: '2025-01-05T00:00:00Z', survivorship_bias_free: false, disclosure: 'One membership source is missing.',
      snapshots: [
        { effective_date: '2024-01-01T00:00:00Z', symbols: ['OLD'], membership_provenance: [] },
        { effective_date: '2025-01-01T00:00:00Z', symbols: ['AAPL'], membership_provenance: [{ symbol: 'AAPL', source: 'Index archive', effective_from: '2025-01-01T00:00:00Z', effective_to: null, evidence: 'Archived constituent file' }] },
      ],
    }])
    throw new Error(`Unexpected ${url}`)
  })

  render(<DataPage datasets={[dataset]} onImported={() => undefined} />)

  expect((await screen.findAllByText('Exchange actions')).length).toBe(2)
  expect(screen.getByText('Split 1 · Dividend 1 · Delisting 1')).toBeInTheDocument()
  expect(screen.getByText('PIT WARNING')).toBeInTheDocument()
  expect(screen.getByText('No reliable settlement price; the position is not silently removed.')).toBeInTheDocument()
  expect(screen.getByText('SURVIVORSHIP RISK')).toBeInTheDocument()
  expect(screen.getByText('Missing membership evidence: OLD')).toBeInTheDocument()
})

test('imports Corporate Action and point-in-time Universe evidence from JSON', async () => {
  const savedActions = {
    corporate_action_dataset_id: 'corporate-actions-imported', name: 'Imported actions', provider: 'Exchange', symbols: ['AAPL'],
    start_time: '2025-01-02T00:00:00Z', end_time: '2025-01-02T00:00:00Z', retrieved_at: '2025-01-03T00:00:00Z', content_fingerprint: 'sha256:imported-actions', point_in_time_safe: true,
    disclosure: 'Official notice.', actions: [{ action_id: 'split-imported', symbol: 'AAPL', action_type: 'SPLIT', effective_at: '2025-01-02T00:00:00Z', announced_at: '2025-01-01T00:00:00Z', available_at: '2025-01-01T00:00:00Z', source: 'Exchange', evidence: 'Archived notice', split_ratio: 2, cash_amount: null, currency: null, delisting_reason: null, settlement_price: null }],
  }
  const savedUniverse = {
    universe_id: 'universe-imported', name: 'Imported PIT universe', source: 'Index archive', mode: 'POINT_IN_TIME', dataset_id: 'dataset-existing', created_at: '2025-01-03T00:00:00Z', survivorship_bias_free: true, disclosure: 'Source-backed membership.',
    snapshots: [{ effective_date: '2025-01-01T00:00:00Z', symbols: ['AAPL'], membership_provenance: [{ symbol: 'AAPL', source: 'Index archive', effective_from: '2025-01-01T00:00:00Z', effective_to: null, evidence: 'Archived constituent file' }] }],
  }
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.includes('dataset-existing') && url.endsWith('/preview')) return response({ rows: [] })
    if (url === '/api/corporate-actions' && !init?.method) return response([])
    if (url === '/api/universes' && !init?.method) return response([])
    if (url === '/api/corporate-actions' && init?.method === 'POST') return response(savedActions, 201)
    if (url === '/api/universes' && init?.method === 'POST') return response(savedUniverse, 201)
    throw new Error(`Unexpected ${init?.method ?? 'GET'} ${url}`)
  })
  render(<DataPage datasets={[dataset]} onImported={() => undefined} />)

  const actionRequest = {
    name: 'Imported actions', provider: 'Exchange', disclosure: 'Official notice.',
    actions: [{ action_id: 'split-imported', symbol: 'AAPL', action_type: 'SPLIT', effective_at: '2025-01-02T00:00:00Z', announced_at: '2025-01-01T00:00:00Z', available_at: '2025-01-01T00:00:00Z', source: 'Exchange', evidence: 'Archived notice', split_ratio: 2 }],
  }
  fireEvent.change(screen.getByLabelText('Import actions JSON'), {
    target: { files: [new File([JSON.stringify(actionRequest)], 'actions.json', { type: 'application/json' })] },
  })
  expect((await screen.findAllByText('Imported actions')).length).toBe(2)
  const actionCall = fetchMock.mock.calls.find(([url, init]) => url === '/api/corporate-actions' && init?.method === 'POST')
  expect(JSON.parse(String(actionCall?.[1]?.body))).toEqual(actionRequest)

  const universeRequest = {
    name: 'Imported PIT universe', source: 'Index archive', mode: 'POINT_IN_TIME', dataset_id: 'dataset-existing', disclosure: 'Source-backed membership.',
    snapshots: [{ effective_date: '2025-01-01T00:00:00Z', symbols: ['AAPL'], membership_provenance: [{ symbol: 'AAPL', source: 'Index archive', effective_from: '2025-01-01T00:00:00Z', effective_to: null, evidence: 'Archived constituent file' }] }],
  }
  fireEvent.change(screen.getByLabelText('Import universe JSON'), {
    target: { files: [new File([JSON.stringify(universeRequest)], 'universe.json', { type: 'application/json' })] },
  })
  expect((await screen.findAllByText('Imported PIT universe')).length).toBe(2)
  expect(screen.getByText('SURVIVORSHIP SAFE')).toBeInTheDocument()
  const universeCall = fetchMock.mock.calls.find(([url, init]) => url === '/api/universes' && init?.method === 'POST')
  expect(JSON.parse(String(universeCall?.[1]?.body))).toEqual(universeRequest)
})

test('renders the Data workspace in Chinese by default', async () => {
  window.localStorage.removeItem('vqd-language')
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.includes('dataset-existing')) return response({ rows: [{ timestamp: '2025-01-01T00:00:00Z', symbol: 'AAPL', close: 100 }] })
    throw new Error(`Unexpected ${url}`)
  })
  render(<I18nProvider><DataPage datasets={[dataset]} onImported={() => undefined} /></I18nProvider>)
  expect(screen.getByRole('heading', { name: '数据' })).toBeInTheDocument()
  expect(screen.getByLabelText('选择 CSV')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '数据集' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '公司行动' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '历史股票池' })).toBeInTheDocument()
  expect(screen.getByText('名称')).toBeInTheDocument()
  expect(screen.getByText('时间范围')).toBeInTheDocument()
  expect(screen.getAllByText('数据质量').length).toBeGreaterThan(0)
  expect(screen.getByText('当前数据集 · Existing research.csv')).toBeInTheDocument()
  expect((await screen.findAllByText('收盘价')).length).toBeGreaterThan(0)
})
