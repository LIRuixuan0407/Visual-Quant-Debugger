import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { PaperSessionSnapshot } from '../../types/paper'
import type { StrategyDefinition } from '../../types/strategy'
import ForwardPage from './ForwardPage'
import LivePaperPage from './LivePaperPage'

const definition: StrategyDefinition = {
  strategy_id: 'external.sma-cross', name: 'SMA Cross', description: 'Live test strategy', version: '1.0.0',
  parameters: [{ key: 'fast', label: 'Fast', description: 'Fast window', value_type: 'integer', default_value: 3, minimum: 1, exclusive_minimum: false, maximum: 20, step: 1, unit: 'bars', impact_hint: '' }],
  validation_rules: [], presets: [], pipeline: [], execution_assumptions: [],
  data_requirements: { required_fields: ['close'], symbol_count: 1, symbols: ['AAPL'], minimum_history: 3 },
}

const pairsDefinition: StrategyDefinition = {
  ...definition,
  strategy_id: 'pairs-trading', name: 'Pairs Trading', description: 'Trade the relationship between two stocks.',
  data_requirements: { required_fields: ['close'], symbol_count: 2, symbols: [], minimum_history: 3 },
}

const snapshot: PaperSessionSnapshot = {
  session_id: 'paper-0123456789abcdef01234567', status: 'CREATED', feed_status: 'DISCONNECTED', recovery_status: 'READY',
  execution_mode: 'VQD_SIMULATED', broker_status: 'NOT_USED', broker_account: null, recent_broker_events: [],
  account_id: 'paper-account-0123456789abcdef01234567',
  strategy_id: definition.strategy_id, strategy_name: definition.name, strategy_fingerprint: 'sha256:abc', symbols: ['AAPL'], parameters: { fast: 3 },
  provider: 'alpaca', feed: 'iex', timeframe: '1Min', market_session: 'US_REGULAR', initial_cash: 100000,
  created_at: '2025-01-02T14:30:00Z', started_at: null, stopped_at: null, last_market_event: null, last_received_at: null,
  last_event_sequence: 0, last_processed_market_event_id: null, market_watermark: null, evaluated_bar_count: 0, correction_count: 0, duplicate_count: 0, out_of_order_count: 0, market_clock: null,
  account: { cash: 100000, positions: {}, equity: 100000, net_pnl: 0, cumulative_fees: 0, cumulative_slippage: 0, max_drawdown: 0, pending_orders: [], executions: [] },
  recent_market_events: [], recent_revisions: [], latest_event: null, error_code: null, error_message: null, research_run_id: null, reference_run_id: null, orders: [], fills: [],
}

const account = { account_id: snapshot.account_id, name: 'Primary paper', currency: 'USD', initial_cash: 100000, cash: 100000, positions: {}, equity: 100000, cumulative_fees: 0, cumulative_slippage: 0, active_session_id: null, created_at: snapshot.created_at, updated_at: snapshot.created_at }

class FakeEventSource {
  static latest: FakeEventSource | null = null
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  private snapshotListener: ((event: MessageEvent<string>) => void) | null = null

  constructor(readonly url: string) { FakeEventSource.latest = this }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) { if (type === 'snapshot') this.snapshotListener = listener }
  emit(value: PaperSessionSnapshot) { this.snapshotListener?.(new MessageEvent('snapshot', { data: JSON.stringify(value) })) }
  close() { /* no-op test stream */ }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); FakeEventSource.latest = null; window.localStorage.clear() })

describe('Live Paper Forward workspace', () => {
  it('uses the same numeric alignment hook for the Forward default header and values', () => {
    render(<ForwardPage definition={definition} sessionId={null} onSessionChange={() => undefined} />)
    expect(screen.getByText('Default')).toHaveClass('parameter-default-heading')
    expect(screen.getByText('3').closest('.parameter-default-cell')).toBeInTheDocument()
  })

  it('uses TDX as the default US market-data source while all paper fills remain local', async () => {
    const tdxSnapshot: PaperSessionSnapshot = { ...snapshot, provider: 'tdx', feed: 'tdx', execution_mode: 'VQD_SIMULATED', broker_status: 'NOT_USED' }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const endpoint = String(input)
      if (endpoint === '/api/market-data/providers') return new Response(JSON.stringify([
        { provider: 'tdx', configured: true, feeds: ['tdx'], selected_feed: 'tdx', timeframe: '1Min', market_session: 'US_REGULAR', markets: ['CN', 'HK', 'US'], requires_credentials: false, supports_live: true, supports_historical: true },
        { provider: 'alpaca', configured: true, feeds: ['iex', 'sip'], selected_feed: 'iex', timeframe: '1Min', market_session: 'US_REGULAR', markets: ['US'], requires_credentials: true, supports_live: true, supports_historical: true },
      ]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-accounts') return new Response(JSON.stringify({ items: [account] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-sessions' && !init?.method) return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/market-data/stocks/search?q=AAPL&provider=tdx&market=US') return new Response(JSON.stringify([{ symbol: 'AAPL', name: 'Apple Inc.', exchange: 'US', status: 'active', tradable: true, fractionable: true, market: 'US', currency: 'USD', lot_size: 1 }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-sessions' && init?.method === 'POST') return new Response(JSON.stringify(tdxSnapshot), { status: 201, headers: { 'Content-Type': 'application/json' } })
      throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${endpoint}`)
    })
    render(<LivePaperPage definition={definition} />)
    expect(await screen.findByRole('heading', { name: 'Create a paper portfolio' })).toBeInTheDocument()
    expect(await screen.findByText('TDX · Market data ready')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Market' })).toHaveValue('US')
    expect(screen.getByRole('combobox', { name: 'Market data provider' })).toHaveValue('tdx')
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Paper Account' })).toHaveValue(account.account_id))
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click((await screen.findByText('Apple Inc.')).closest('button')!)
    expect(screen.getAllByText('VQD local paper execution')).toHaveLength(2)
    expect(screen.getByText('Virtual money only')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Create paper portfolio' }))
    await waitFor(() => expect(screen.getAllByText(snapshot.account_id).length).toBeGreaterThan(0))
    expect(screen.getAllByText('TDX · TDX').length).toBeGreaterThan(0)
    expect(screen.getByText('VQD SIMULATED EXECUTION')).toBeInTheDocument()
    expect(screen.getByText('NO BROKER ORDER')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /buy/i })).not.toBeInTheDocument()
    const request = fetchMock.mock.calls.find((call) => String(call[0]) === '/api/paper-sessions' && call[1]?.method === 'POST')
    const body = JSON.parse(String(request?.[1]?.body)) as Record<string, unknown>
    expect(body).toMatchObject({ account_id: account.account_id, provider: 'tdx', feed: 'tdx', market_session: 'US_REGULAR', symbols: ['AAPL'], execution_mode: 'VQD_SIMULATED' })
    expect(body).toMatchObject({ securities: [{ symbol: 'AAPL', name: 'Apple Inc.', exchange: 'US', status: 'active' }] })
    expect(body).not.toHaveProperty('initial_cash')
    expect(JSON.stringify(body).toLowerCase()).not.toContain('secret')
  })

  it('guides a two-stock A-share strategy and never submits an incomplete pair', async () => {
    const posted: Array<Record<string, unknown>> = []
    const accountRequests: Array<Record<string, unknown>> = []
    const cnAccount = { ...account, account_id: 'paper-account-cn-0123456789abcdef01', name: 'A-share paper', currency: 'CNY' as const, initial_cash: 1_000_000, cash: 1_000_000, equity: 1_000_000 }
    const cnSnapshot: PaperSessionSnapshot = {
      ...snapshot,
      account_id: cnAccount.account_id,
      provider: 'tdx', feed: 'tdx', market_session: 'CN_REGULAR', initial_cash: 1_000_000,
      strategy_id: pairsDefinition.strategy_id, strategy_name: pairsDefinition.name, symbols: ['600519.SH', '000858.SZ'],
      account: { ...snapshot.account, cash: 1_000_000, equity: 1_000_000 },
    }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const endpoint = String(input)
      if (endpoint === '/api/market-data/providers') return new Response(JSON.stringify([{ provider: 'tdx', configured: true, feeds: ['tdx'], selected_feed: 'tdx', timeframe: '1Min', market_session: 'US_REGULAR', markets: ['CN', 'HK', 'US'], requires_credentials: false, supports_live: true, supports_historical: true }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-accounts' && !init?.method) return new Response(JSON.stringify({ items: [account] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-accounts' && init?.method === 'POST') {
        accountRequests.push(JSON.parse(String(init.body)) as Record<string, unknown>)
        return new Response(JSON.stringify(cnAccount), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }
      if (endpoint === '/api/paper-sessions' && !init?.method) return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/market-data/stocks/search?q=600519&provider=tdx&market=CN') return new Response(JSON.stringify([{ symbol: '600519.SH', name: '贵州茅台', exchange: 'SH', status: 'active', tradable: true, fractionable: false, market: 'CN', currency: 'CNY', lot_size: 100 }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/market-data/stocks/search?q=000858&provider=tdx&market=CN') return new Response(JSON.stringify([{ symbol: '000858.SZ', name: '五粮液', exchange: 'SZ', status: 'active', tradable: true, fractionable: false, market: 'CN', currency: 'CNY', lot_size: 100 }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-sessions' && init?.method === 'POST') {
        posted.push(JSON.parse(String(init.body)) as Record<string, unknown>)
        return new Response(JSON.stringify(cnSnapshot), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${endpoint}`)
    })
    render(<LivePaperPage definition={pairsDefinition} />)
    const createButton = await screen.findByRole('button', { name: 'Create paper portfolio' })
    expect(screen.getByRole('combobox', { name: 'Market' })).toHaveValue('CN')
    expect(screen.getByRole('combobox', { name: 'Paper Account' })).toHaveValue('__new__')
    expect(createButton).toBeDisabled()
    expect(screen.getByText('0 / 2')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click((await screen.findByText('贵州茅台')).closest('button')!)
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(createButton).toBeDisabled()
    expect(posted).toHaveLength(0)

    fireEvent.change(screen.getByLabelText('Find a stock'), { target: { value: '000858' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click((await screen.findByText('五粮液')).closest('button')!)
    expect(screen.getByText('2 / 2')).toBeInTheDocument()
    expect(createButton).toBeEnabled()
    fireEvent.click(createButton)
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(accountRequests).toEqual([{ name: 'My Paper Account', initial_cash: 1_000_000, currency: 'CNY' }])
    expect(posted[0]).toMatchObject({ strategy_id: 'pairs-trading', symbols: ['600519.SH', '000858.SZ'], provider: 'tdx', feed: 'tdx', market_session: 'CN_REGULAR', execution_mode: 'VQD_SIMULATED' })
    expect(posted[0]).toMatchObject({ account_id: cnAccount.account_id })
  })

  it('blocks historical framework adapters before any forward or provider request', () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    render(<ForwardPage definition={{ ...definition, historical_research_only: true }} sessionId={null} onSessionChange={() => undefined} />)
    expect(screen.getByRole('heading', { name: 'Native runtime required' })).toBeInTheDocument()
    expect(screen.getByText('Framework strategies are historical-research adapters and cannot run in Forward or Live Paper.')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows backend health, operation history, orders, fills, and a safe divergence recovery surface in Chinese', async () => {
    window.localStorage.setItem('vqd-language', 'zh')
    vi.stubGlobal('EventSource', FakeEventSource)
    const failed = { ...snapshot, status: 'ERROR' as const, recovery_status: 'RECOVERY_DIVERGENCE' as const, broker_status: 'ERROR' as const, error_code: 'RECOVERY_DIVERGENCE', error_message: 'Deterministic replay did not match the persisted checkpoint' }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const endpoint = String(input)
      const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/market-data/providers') return json([{ provider: 'alpaca', configured: true, feeds: ['iex'], selected_feed: 'iex', timeframe: '1Min', market_session: 'US_REGULAR' }])
      if (endpoint === '/api/paper-accounts') return json({ items: [account] })
      if (endpoint === '/api/paper-sessions') return json({ items: [failed] })
      if (endpoint === `/api/paper-sessions/${failed.session_id}`) return json(failed)
      if (endpoint === `/api/paper-sessions/${failed.session_id}/trace?limit=200`) return json({ trace_version: '1.0', session_id: failed.session_id, strategy_id: failed.strategy_id, parameters: failed.parameters, timeline: [], diagnostics: [], market_revisions: [], execution_mode: failed.execution_mode, broker_events: [] })
      if (endpoint === `/api/paper/sessions/${failed.session_id}/health`) return json({ session_id: failed.session_id, status: 'ERROR', feed_status: 'DISCONNECTED', broker_status: 'ERROR', recovery_status: 'RECOVERY_DIVERGENCE', last_received_at: null, last_market_event: null, last_latency_ms: null, stale_seconds: 0, reconnect_count: 2, backfill_count: 1, backfilled_bar_count: 3, open_order_count: 0, partially_filled_order_count: 0, broker_account_status: null, broker_cash: null, broker_equity: null, broker_buying_power: null, rejected_order_count: 0, last_broker_event_at: null })
      if (endpoint === `/api/paper/sessions/${failed.session_id}/operations?limit=200`) return json({ items: [{ operation_id: 'operation-1', sequence: 1, session_id: failed.session_id, operation_type: 'RECOVERY_DIVERGENCE', occurred_at: failed.created_at, message: 'Recovered runtime state does not match the persisted checkpoint.', metadata: {} }] })
      if (endpoint === `/api/paper/sessions/${failed.session_id}/recovery`) return json({ session_id: failed.session_id, status: 'RECOVERY_DIVERGENCE', journal_event_count: 3, broker_event_count: 0, recorded_portfolio_hash: 'sha256:recorded', recovered_portfolio_hash: 'sha256:recovered', recorded_trace_hash: 'sha256:recorded-trace', recovered_trace_hash: 'sha256:recovered-trace', broker_reconciled: false, account_reconciled: true, warnings: ['Session was not resumed automatically.'] })
      throw new Error(`Unexpected request GET ${endpoint}`)
    })

    render(<I18nProvider><LivePaperPage definition={definition} /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: new RegExp(failed.account_id) }))
    expect(await screen.findByRole('heading', { name: '健康状态' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '运维记录' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '概览' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '订单' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '成交' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '恢复' })).toBeInTheDocument()
    expect((await screen.findAllByText('会话未自动恢复运行。')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('恢复后的运行状态与持久化检查点不一致。').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '重试恢复' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止会话' })).toBeInTheDocument()
    expect(screen.queryByText(/force continue/i)).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(`/api/paper-sessions/${failed.session_id}/trace?limit=200`, undefined)
    expect(fetchMock).toHaveBeenCalledWith(`/api/paper/sessions/${failed.session_id}/operations?limit=200`, undefined)
    const operationEndpoint = `/api/paper/sessions/${failed.session_id}/operations?limit=200`
    expect(fetchMock.mock.calls.filter(([input]) => String(input) === operationEndpoint)).toHaveLength(1)
    act(() => FakeEventSource.latest?.onerror?.())
    expect(screen.getByText('实时更新通道正在重连；后端会话仍会独立继续运行。')).toBeInTheDocument()
    act(() => FakeEventSource.latest?.onopen?.())
    await waitFor(() => expect(screen.queryByText('实时更新通道正在重连；后端会话仍会独立继续运行。')).not.toBeInTheDocument())
    act(() => FakeEventSource.latest?.emit({ ...failed, feed_status: 'CONNECTED', last_event_sequence: 1 }))
    await waitFor(() => expect(screen.getAllByText('已连接').length).toBeGreaterThan(0))
    expect(fetchMock.mock.calls.filter(([input]) => String(input) === operationEndpoint)).toHaveLength(1)
  })
})
