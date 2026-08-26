import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { DatasetDefinition } from '../../types/dataset'
import FactorLabPage from './FactorLabPage'
import HistoricalMarketPage from './HistoricalMarketPage'

const dataset: DatasetDefinition = {
  dataset_id: 'dataset-real', name: 'Real five-stock universe', source_type: 'PROVIDER',
  timezone: 'UTC', frequency: '1Day', symbols: ['AAPL', 'AMZN', 'META', 'MSFT', 'NVDA'],
  fields: ['open', 'high', 'low', 'close', 'volume'], row_count: 500, synchronized_bar_count: 100,
  start_time: '2024-01-02T05:00:00Z', end_time: '2024-12-31T05:00:00Z', created_at: '2025-01-01T00:00:00Z',
  content_fingerprint: 'sha256:real', source_timezone: 'UTC', column_mapping: {}, security_names: { AAPL: 'Apple Inc.' },
  quality: { status: 'VALID', rows: 500, symbols: 5, start: '2024-01-02T05:00:00Z', end: '2024-12-31T05:00:00Z', duplicates: 0, missing_required_values: 0, rows_reordered: 0, alignment_gaps: 0, timezone: 'UTC', issues: [] },
  provenance: { provider: 'alpaca', feed: 'iex', requested_symbols: ['AAPL', 'AMZN', 'META', 'MSFT', 'NVDA'], requested_start: '2024-01-01T00:00:00Z', requested_end: '2024-12-31T23:59:59Z', retrieved_at: '2025-01-01T00:00:00Z', market_timestamp_start: '2024-01-02T05:00:00Z', market_timestamp_end: '2024-12-31T05:00:00Z' },
}

afterEach(() => vi.unstubAllGlobals())

describe('Phase 17 Discover workspaces', () => {
  it('renders the real historical cross-section with a clear survivorship warning', async () => {
    const view = {
      dataset_id: dataset.dataset_id, dataset_revision: dataset.content_fingerprint, source: 'alpaca:iex',
      requested_as_of: dataset.end_time, as_of: dataset.end_time, universe_id: 'universe-static', universe_source: 'dataset:dataset-real', universe_mode: 'STATIC', survivorship_bias_free: false,
      universe_disclosure: 'Current constituents are held fixed through history; this is not survivorship-bias free.',
      cross_section: dataset.symbols.map((symbol) => ({ symbol, company: symbol === 'AAPL' ? 'Apple Inc.' : symbol, close: 100, return_1d: .01, volume: 1_000_000, volatility_20d: .02, high_low_range: .01, average_volume_20d: 900_000 })),
      selected_symbol: 'AAPL', trend: [{ timestamp: '2024-12-30T05:00:00Z', close: 99, volume: 900_000 }, { timestamp: dataset.end_time, close: 100, volume: 1_000_000 }],
      fundamentals: null,
    }
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
      const url = String(input)
      const body = url.includes('/api/historical-market') ? view : []
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }))
    render(<I18nProvider><HistoricalMarketPage datasets={[dataset]} onImported={() => undefined} /></I18nProvider>)
    expect(await screen.findByText('Apple Inc.')).toBeInTheDocument()
    expect(screen.getByText('不具备无幸存者偏差保证')).toBeInTheDocument()
    expect(screen.getByText('市场状态 · 2024-12-31')).toBeInTheDocument()
    const chart = screen.getByRole('img', { name: 'AAPL · 价格与成交量历史' })
    fireEvent.keyDown(chart, { key: 'ArrowLeft' })
    expect(screen.getByText('$99.00')).toBeInTheDocument()
  })

  it('starts factor research with backend catalog and three sealed periods', async () => {
    const factor = { factor_id: 'momentum', name: 'Momentum', formula: 'close(t) / close(t-lookback) - 1', description: 'Measures trailing price persistence over a fixed historical window.', parameters: [{ key: 'lookback', label: 'Lookback', description: 'Historical observations used', default_value: 20, minimum: 2, maximum: 252, step: 1, unit: 'bars' }], required_fields: ['close'], required_fundamental_fields: [], lookback: 20, availability: 'close(t)', direction: 'HIGH', category: 'PRICE_VOLUME', data_source: 'MARKET' }
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => Promise.resolve(new Response(JSON.stringify(String(input).includes('/api/factors') ? [factor] : []), { status: 200, headers: { 'Content-Type': 'application/json' } }))))
    render(<I18nProvider><FactorLabPage datasets={[dataset]} onOpenHistorical={() => undefined} onOpenReplay={() => undefined} onRunComplete={() => undefined} /></I18nProvider>)
    expect(await screen.findByText('开始因子研究')).toBeInTheDocument()
    expect(screen.getByText('研究')).toBeInTheDocument()
    expect(screen.getByText('验证集')).toBeInTheDocument()
    expect(screen.getByText('留出集')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '运行研究' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: /导入因子/ }))
    expect(screen.getByText('注册 VQDFactor 源码')).toBeInTheDocument()
    expect(screen.getByText(/VQD 不提供沙箱/)).toBeInTheDocument()
    expect(screen.getByText('源文件')).toBeInTheDocument()
    expect(screen.getByText('因子研究')).toBeInTheDocument()
  })

  it('shows fiscal, filing, availability, and use dates in the fundamental view', async () => {
    const fundamentals = [{
      fundamental_dataset_id: 'fundamental-sec', name: 'SEC filing record', provider: 'sec-companyfacts',
      symbols: dataset.symbols, fields: ['net_income'], start_time: dataset.start_time, end_time: dataset.end_time,
      retrieved_at: '2025-01-01T00:00:00Z', observation_count: 5, point_in_time_safe: true,
      restatement_safe: false, disclosure: 'NOT RESTATEMENT-SAFE',
    }]
    const view = {
      dataset_id: dataset.dataset_id, dataset_revision: dataset.content_fingerprint, source: 'alpaca:iex',
      requested_as_of: dataset.end_time, as_of: dataset.end_time, universe_id: 'universe-static',
      universe_source: 'dataset:dataset-real', universe_mode: 'STATIC', survivorship_bias_free: false,
      universe_disclosure: 'Static universe.', cross_section: dataset.symbols.map((symbol) => ({ symbol, company: symbol, close: 100, return_1d: .01, volume: 1_000_000, volatility_20d: .02, high_low_range: .01, average_volume_20d: 900_000 })),
      selected_symbol: 'AAPL', trend: [{ timestamp: '2024-12-30T05:00:00Z', close: 99, volume: 900_000 }, { timestamp: dataset.end_time, close: 100, volume: 1_000_000 }],
      fundamentals: { fundamental_dataset_id: 'fundamental-sec', provider: 'sec-companyfacts', symbol: 'AAPL', used_at: dataset.end_time, restatement_safe: false, fields: [{ field: 'net_income', status: 'AVAILABLE', value: 1000000, unit: 'USD', fiscal_period: '2023FY', report_date: '2023-12-31T23:59:59Z', filed_at: '2024-02-15T23:59:59Z', available_at: '2024-02-15T23:59:59Z', used_at: dataset.end_time, age_days: 320, form: '10-K', accession: '0001', is_restatement: false }] },
    }
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
      const url = String(input)
      const body = url.endsWith('/api/fundamental-datasets') ? fundamentals : url.includes('/api/fundamental-providers') || url.includes('/api/universes') ? [] : view
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }))
    render(<I18nProvider><HistoricalMarketPage datasets={[dataset]} onImported={() => undefined} /></I18nProvider>)
    await screen.findByRole('img', { name: 'AAPL · 价格与成交量历史' })
    fireEvent.click(screen.getByRole('button', { name: '基本面' }))
    expect(screen.getAllByText('2023FY').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('2024-02-15').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('不具备修订安全保证')).toBeInTheDocument()
  })
})
