import { readJson } from './client'
import type { CreatePaperSessionInput, MarketDataProviderStatus, PaperAccount, PaperOperationalHealth, PaperOperationEvent, PaperRecoveryReport, PaperSessionSnapshot, PaperTrace } from '../types/paper'
import { addCreatedObjectToCurrentWorkspace } from './workspaces'

async function requestJson(endpoint: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(endpoint, init)
  return readJson(response, `${init?.method ?? 'GET'} ${endpoint}`)
}

export function isPaperSession(value: unknown): value is PaperSessionSnapshot {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return typeof item.session_id === 'string' && typeof item.status === 'string' && typeof item.feed_status === 'string' && typeof item.account === 'object' && item.account !== null && Array.isArray(item.recent_market_events)
}

function requireSession(value: unknown, endpoint: string): PaperSessionSnapshot {
  if (!isPaperSession(value)) throw new Error(`${endpoint} returned a malformed paper session.`)
  return value
}

export async function getMarketDataProviders(): Promise<MarketDataProviderStatus[]> {
  const endpoint = '/api/market-data/providers'
  const value = await requestJson(endpoint)
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'object' || item === null || typeof (item as Record<string, unknown>).provider !== 'string')) throw new Error(`${endpoint} returned malformed provider status.`)
  return value as MarketDataProviderStatus[]
}

export async function listPaperSessions(): Promise<PaperSessionSnapshot[]> {
  const endpoint = '/api/paper-sessions'
  const value = await requestJson(endpoint)
  if (typeof value !== 'object' || value === null || !Array.isArray((value as Record<string, unknown>).items) || !(value as { items: unknown[] }).items.every(isPaperSession)) throw new Error(`${endpoint} returned malformed session history.`)
  return (value as { items: PaperSessionSnapshot[] }).items
}

export async function listPaperAccounts(): Promise<PaperAccount[]> {
  const endpoint = '/api/paper-accounts'
  const value = await requestJson(endpoint)
  if (typeof value !== 'object' || value === null || !Array.isArray((value as { items?: unknown }).items)) throw new Error(`${endpoint} returned malformed accounts.`)
  return (value as { items: PaperAccount[] }).items
}

export async function createPaperAccount(name: string, initialCash: number): Promise<PaperAccount> {
  const endpoint = '/api/paper-accounts'
  const value = await requestJson(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, initial_cash: initialCash, currency: 'USD' }) })
  if (typeof value !== 'object' || value === null || typeof (value as { account_id?: unknown }).account_id !== 'string') throw new Error(`${endpoint} returned a malformed account.`)
  return value as PaperAccount
}

export async function createPaperSession(input: CreatePaperSessionInput): Promise<PaperSessionSnapshot> {
  const endpoint = '/api/paper-sessions'
  const created = requireSession(await requestJson(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) }), endpoint)
  await addCreatedObjectToCurrentWorkspace('PAPER_SESSION', created.session_id)
  return created
}

export async function getPaperSession(sessionId: string): Promise<PaperSessionSnapshot> {
  const endpoint = `/api/paper-sessions/${encodeURIComponent(sessionId)}`
  return requireSession(await requestJson(endpoint), endpoint)
}

export async function transitionPaperSession(sessionId: string, action: 'start' | 'pause' | 'resume' | 'stop'): Promise<PaperSessionSnapshot> {
  const endpoint = `/api/paper-sessions/${encodeURIComponent(sessionId)}/${action}`
  const updated = requireSession(await requestJson(endpoint, { method: 'POST' }), endpoint)
  if (updated.research_run_id) await addCreatedObjectToCurrentWorkspace('RUN', updated.research_run_id)
  return updated
}

export async function cancelPaperOrder(sessionId: string, orderId: string): Promise<PaperSessionSnapshot> {
  const endpoint = `/api/paper-sessions/${encodeURIComponent(sessionId)}/orders/${encodeURIComponent(orderId)}/cancel`
  return requireSession(await requestJson(endpoint, { method: 'POST' }), endpoint)
}

export async function getPaperTrace(sessionId: string): Promise<PaperTrace> {
  const endpoint = `/api/paper-sessions/${encodeURIComponent(sessionId)}/trace`
  const value = await requestJson(endpoint)
  if (typeof value !== 'object' || value === null || (value as Record<string, unknown>).trace_version !== '1.0' || !Array.isArray((value as Record<string, unknown>).timeline) || !Array.isArray((value as Record<string, unknown>).market_revisions)) throw new Error(`${endpoint} returned a malformed live Trace.`)
  return value as PaperTrace
}

export async function getPaperHealth(sessionId: string): Promise<PaperOperationalHealth> {
  const endpoint = `/api/paper/sessions/${encodeURIComponent(sessionId)}/health`
  const value = await requestJson(endpoint)
  if (typeof value !== 'object' || value === null || (value as Record<string, unknown>).session_id !== sessionId || typeof (value as Record<string, unknown>).stale_seconds !== 'number') throw new Error(`${endpoint} returned malformed health data.`)
  return value as PaperOperationalHealth
}

export async function getPaperOperations(sessionId: string): Promise<PaperOperationEvent[]> {
  const endpoint = `/api/paper/sessions/${encodeURIComponent(sessionId)}/operations`
  const value = await requestJson(endpoint)
  if (typeof value !== 'object' || value === null || !Array.isArray((value as { items?: unknown }).items)) throw new Error(`${endpoint} returned a malformed operation log.`)
  return (value as { items: PaperOperationEvent[] }).items
}

export async function getPaperRecovery(sessionId: string): Promise<PaperRecoveryReport> {
  const endpoint = `/api/paper/sessions/${encodeURIComponent(sessionId)}/recovery`
  const value = await requestJson(endpoint)
  if (typeof value !== 'object' || value === null || (value as Record<string, unknown>).session_id !== sessionId || !Array.isArray((value as Record<string, unknown>).warnings)) throw new Error(`${endpoint} returned a malformed recovery report.`)
  return value as PaperRecoveryReport
}

export async function recoverPaperSession(sessionId: string): Promise<PaperRecoveryReport> {
  const endpoint = `/api/paper/sessions/${encodeURIComponent(sessionId)}/recover`
  const value = await requestJson(endpoint, { method: 'POST' })
  if (typeof value !== 'object' || value === null || (value as Record<string, unknown>).session_id !== sessionId || !Array.isArray((value as Record<string, unknown>).warnings)) throw new Error(`${endpoint} returned a malformed recovery report.`)
  return value as PaperRecoveryReport
}
