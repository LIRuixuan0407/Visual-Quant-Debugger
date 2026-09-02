import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { PaperAccount } from '../../types/paper'
import type { AlpacaIntegrationStatus } from '../../types/settings'
import ProfilePage from './ProfilePage'

const empty: AlpacaIntegrationStatus = {
  provider: 'alpaca', configured: false, source: 'NONE', masked_api_key: null,
  feed: 'iex', verification_status: 'UNVERIFIED', last_verified_at: null,
  last_error: null, removable: false,
}

const verified: AlpacaIntegrationStatus = {
  provider: 'alpaca', configured: true, source: 'VAULT', masked_api_key: 'PKUS••••7890',
  feed: 'sip', verification_status: 'VERIFIED', last_verified_at: '2026-08-25T05:00:00Z',
  last_error: null, removable: true,
}

function response(value: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  window.localStorage.setItem('vqd-language', 'en')
  vi.restoreAllMocks()
})

test('saves, verifies, masks, and removes the user Alpaca connection', async () => {
  let current = empty
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url === '/api/me/integrations/alpaca' && init?.method === 'PUT') {
      current = { ...verified, verification_status: 'UNVERIFIED', last_verified_at: null }
      return response(current)
    }
    if (url === '/api/me/integrations/alpaca/verify') { current = verified; return response(current) }
    if (url === '/api/me/integrations/alpaca' && init?.method === 'DELETE') { current = empty; return response(null, 204) }
    if (url === '/api/me/integrations/alpaca') return response(current)
    if (url === '/api/paper-accounts') return response({ items: [] })
    throw new Error(`Unexpected fetch ${url}`)
  })

  render(<I18nProvider><ProfilePage /></I18nProvider>)
  expect(await screen.findByRole('heading', { name: 'My' })).toBeInTheDocument()
  expect(screen.getAllByText('NOT CONNECTED').length).toBeGreaterThan(0)

  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'PKUSER1234567890' } })
  fireEvent.change(screen.getByLabelText('Secret Key'), { target: { value: 'super-private-secret-value' } })
  fireEvent.click(screen.getByText('SIP'))
  fireEvent.click(screen.getByRole('button', { name: 'Save and verify' }))

  expect(await screen.findByText('Alpaca connection verified.')).toBeInTheDocument()
  expect(screen.getByText('PKUS••••7890')).toBeInTheDocument()
  const put = fetchMock.mock.calls.find(([url, init]) => url === '/api/me/integrations/alpaca' && init?.method === 'PUT')
  expect(JSON.parse(put?.[1]?.body as string)).toEqual({ api_key: 'PKUSER1234567890', secret_key: 'super-private-secret-value', feed: 'sip' })

  fireEvent.click(screen.getByRole('button', { name: 'Remove connection' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }))
  await waitFor(() => expect(screen.getAllByText('NOT CONNECTED').length).toBeGreaterThan(0))
  expect(fetchMock).toHaveBeenCalledWith('/api/me/integrations/alpaca', { method: 'DELETE' })
})

test('switches between paper accounts without aggregating different currencies', async () => {
  const createdAt = '2026-09-02T17:25:36Z'
  const accounts: PaperAccount[] = [
    { account_id: 'paper-account-cny-00000000000000000001', name: 'A-share paper', currency: 'CNY', initial_cash: 1_000_000, cash: 800_000, positions: { '600519.SH': 100 }, equity: 1_050_000, cumulative_fees: 125, cumulative_slippage: 50, active_session_id: 'paper-cny-00000000000000000001', created_at: createdAt, updated_at: createdAt },
    { account_id: 'paper-account-hkd-00000000000000000001', name: 'Hong Kong paper', currency: 'HKD', initial_cash: 500_000, cash: 500_000, positions: {}, equity: 500_000, cumulative_fees: 0, cumulative_slippage: 0, active_session_id: null, created_at: createdAt, updated_at: createdAt },
    { account_id: 'paper-account-usd-00000000000000000001', name: 'US paper', currency: 'USD', initial_cash: 100_000, cash: 90_000, positions: { AAPL: 50 }, equity: 101_250, cumulative_fees: 25, cumulative_slippage: 10, active_session_id: null, created_at: createdAt, updated_at: createdAt },
  ]
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url === '/api/me/integrations/alpaca') return response(empty)
    if (url === '/api/paper-accounts') return response({ items: accounts })
    throw new Error(`Unexpected fetch ${url}`)
  })

  render(<I18nProvider><ProfilePage /></I18nProvider>)
  const accountSelect = await screen.findByRole('combobox', { name: 'Paper Account' })
  const cnyEquity = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'CNY', maximumFractionDigits: 2 }).format(1_050_000)
  const usdEquity = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(101_250)

  expect(accountSelect).toHaveValue(accounts[0].account_id)
  expect(screen.getByText('3 virtual accounts · Balances stay separate by currency.')).toBeInTheDocument()
  expect(screen.getByText(cnyEquity)).toBeInTheDocument()
  expect(screen.getByText('CNY')).toBeInTheDocument()

  fireEvent.change(accountSelect, { target: { value: accounts[2].account_id } })
  expect(screen.getByText(usdEquity)).toBeInTheDocument()
  expect(screen.getByText('USD')).toBeInTheDocument()
  expect(screen.queryByText(cnyEquity)).not.toBeInTheDocument()
})
