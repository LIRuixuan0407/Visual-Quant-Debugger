import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

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

afterEach(() => vi.restoreAllMocks())

describe('Live Paper Forward workspace', () => {
  it('uses the same numeric alignment hook for the Forward default header and values', () => {
    render(<ForwardPage definition={definition} sessionId={null} onSessionChange={() => undefined} />)
    expect(screen.getByText('Default')).toHaveClass('parameter-default-heading')
    expect(screen.getByText('3').closest('.parameter-default-cell')).toBeInTheDocument()
  })

  it('exposes standalone persistent Paper Trading and binds a native strategy to an account', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const endpoint = String(input)
      if (endpoint === '/api/market-data/providers') return new Response(JSON.stringify([{ provider: 'alpaca', configured: true, feeds: ['iex', 'sip'], selected_feed: 'iex', timeframe: '1Min', market_session: 'US_REGULAR' }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-accounts') return new Response(JSON.stringify({ items: [account] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-sessions' && !init?.method) return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/market-data/stocks/search?q=Apple') return new Response(JSON.stringify([{ symbol: 'AAPL', name: 'Apple Inc.', exchange: 'NASDAQ', status: 'active', tradable: true, fractionable: true }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-sessions' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        return new Response(JSON.stringify({ ...snapshot, execution_mode: body.execution_mode, broker_status: body.execution_mode === 'ALPACA_PAPER' ? 'DISCONNECTED' : 'NOT_USED' }), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${endpoint}`)
    })
    render(<LivePaperPage definition={definition} />)
    expect(await screen.findByRole('heading', { name: 'Create a paper portfolio' })).toBeInTheDocument()
    expect(await screen.findByText('Alpaca connected')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /VQD simulated execution/i })).toBeChecked()
    expect(screen.getByText('IEX · single exchange')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Paper Account' })).toHaveValue(account.account_id))
    fireEvent.change(screen.getByLabelText('Find a stock'), { target: { value: 'Apple' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click((await screen.findByText('Apple Inc.')).closest('button')!)
    fireEvent.click(screen.getByRole('radio', { name: /Alpaca Paper broker/i }))
    expect(screen.getByText('Paper orders leave VQD')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Create paper portfolio' }))
    await waitFor(() => expect(screen.getByText(snapshot.account_id)).toBeInTheDocument())
    expect(screen.getAllByText('ALPACA · IEX').length).toBeGreaterThan(0)
    expect(screen.getByText('ALPACA PAPER BROKER')).toBeInTheDocument()
    expect(screen.getByText('NO REAL MONEY')).toBeInTheDocument()
    expect(screen.getByText('Broker · DISCONNECTED')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /buy/i })).not.toBeInTheDocument()
    const request = fetchMock.mock.calls.find((call) => String(call[0]) === '/api/paper-sessions' && call[1]?.method === 'POST')
    const body = JSON.parse(String(request?.[1]?.body)) as Record<string, unknown>
    expect(body).toMatchObject({ account_id: account.account_id, provider: 'alpaca', feed: 'iex', symbols: ['AAPL'], execution_mode: 'ALPACA_PAPER' })
    expect(body).toMatchObject({ securities: [{ symbol: 'AAPL', name: 'Apple Inc.', exchange: 'NASDAQ', status: 'active' }] })
    expect(body).not.toHaveProperty('initial_cash')
    expect(JSON.stringify(body).toLowerCase()).not.toContain('secret')
  })

  it('guides a two-stock strategy and never submits an incomplete pair', async () => {
    const posted: Array<Record<string, unknown>> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const endpoint = String(input)
      if (endpoint === '/api/market-data/providers') return new Response(JSON.stringify([{ provider: 'alpaca', configured: true, feeds: ['iex'], selected_feed: 'iex', timeframe: '1Min', market_session: 'US_REGULAR' }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-accounts') return new Response(JSON.stringify({ items: [account] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-sessions' && !init?.method) return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint.includes('/api/market-data/stocks/search?q=Apple')) return new Response(JSON.stringify([{ symbol: 'AAPL', name: 'Apple Inc.', exchange: 'NASDAQ', status: 'active', tradable: true, fractionable: true }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint.includes('/api/market-data/stocks/search?q=Microsoft')) return new Response(JSON.stringify([{ symbol: 'MSFT', name: 'Microsoft Corporation', exchange: 'NASDAQ', status: 'active', tradable: true, fractionable: true }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (endpoint === '/api/paper-sessions' && init?.method === 'POST') {
        posted.push(JSON.parse(String(init.body)) as Record<string, unknown>)
        return new Response(JSON.stringify({ ...snapshot, strategy_id: pairsDefinition.strategy_id, strategy_name: pairsDefinition.name, symbols: ['AAPL', 'MSFT'] }), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected request ${init?.method ?? 'GET'} ${endpoint}`)
    })
    render(<LivePaperPage definition={pairsDefinition} />)
    const createButton = await screen.findByRole('button', { name: 'Create paper portfolio' })
    expect(createButton).toBeDisabled()
    expect(screen.getByText('0 / 2')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Find a stock'), { target: { value: 'Apple' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click((await screen.findByText('Apple Inc.')).closest('button')!)
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(createButton).toBeDisabled()
    expect(posted).toHaveLength(0)

    fireEvent.change(screen.getByLabelText('Find a stock'), { target: { value: 'Microsoft' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click((await screen.findByText('Microsoft Corporation')).closest('button')!)
    expect(screen.getByText('2 / 2')).toBeInTheDocument()
    expect(createButton).toBeEnabled()
    fireEvent.click(createButton)
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toMatchObject({ strategy_id: 'pairs-trading', symbols: ['AAPL', 'MSFT'] })
  })

  it('blocks historical framework adapters before any forward or provider request', () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    render(<ForwardPage definition={{ ...definition, historical_research_only: true }} sessionId={null} onSessionChange={() => undefined} />)
    expect(screen.getByRole('heading', { name: 'Native runtime required' })).toBeInTheDocument()
    expect(screen.getByText('Framework strategies are historical-research adapters and cannot run in Forward or Live Paper.')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
