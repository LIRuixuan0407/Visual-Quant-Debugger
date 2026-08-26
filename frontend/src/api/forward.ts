import { readJson } from './client'
import type { ForwardComparisonReport, ForwardSessionSnapshot, ForwardTrace } from '../types/forward'
import type { StrategyParameters } from '../types/strategy'

async function requestJson(endpoint: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(endpoint, init)
  return readJson(response, `${init?.method ?? 'GET'} ${endpoint}`)
}

function isSession(value: unknown): value is ForwardSessionSnapshot {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return typeof item.session_id === 'string' && typeof item.status === 'string' && typeof item.processed_bar_count === 'number' && typeof item.total_bar_count === 'number'
}

function isForwardTrace(value: unknown): value is ForwardTrace {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return item.trace_version === '1.0' && typeof item.session_id === 'string' && Array.isArray(item.timeline) && Array.isArray(item.diagnostics)
}

function isComparison(value: unknown): value is ForwardComparisonReport {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return typeof item.session_id === 'string' && typeof item.research === 'object' && item.research !== null && typeof item.forward === 'object' && item.forward !== null && Array.isArray(item.consistency)
}

async function parseSession(endpoint: string, init?: RequestInit): Promise<ForwardSessionSnapshot> {
  const body = await requestJson(endpoint, init)
  if (!isSession(body)) throw new Error(`${init?.method ?? 'GET'} ${endpoint} returned a malformed Forward session.`)
  return body
}

export function createForwardSession(input: StrategyParameters | {
  strategy_id: string
  dataset_id: string
  parameters: StrategyParameters
  research_cutoff: string | null
}) {
  const payload = 'strategy_id' in input
    ? input
    : { strategy_id: 'pairs-trading', dataset_id: 'forward-demo-v1', parameters: input }
  return parseSession('/api/forward-sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function getForwardSession(sessionId: string) {
  return parseSession(`/api/forward-sessions/${encodeURIComponent(sessionId)}`)
}

function transition(sessionId: string, action: string) {
  const endpoint = `/api/forward-sessions/${encodeURIComponent(sessionId)}/${action}`
  return parseSession(endpoint, { method: 'POST' })
}

export const startForwardSession = (id: string) => transition(id, 'start')
export const pauseForwardSession = (id: string) => transition(id, 'pause')
export const resumeForwardSession = (id: string) => transition(id, 'resume')
export const stopForwardSession = (id: string) => transition(id, 'stop')
export const stepForwardSession = (id: string) => transition(id, 'step')

export async function getForwardTrace(sessionId: string): Promise<ForwardTrace> {
  const endpoint = `/api/forward-sessions/${encodeURIComponent(sessionId)}/trace`
  const body = await requestJson(endpoint)
  if (!isForwardTrace(body)) throw new Error(`GET ${endpoint} returned a malformed Forward Trace.`)
  return body
}

export async function getForwardComparison(sessionId: string): Promise<ForwardComparisonReport> {
  const endpoint = `/api/forward-sessions/${encodeURIComponent(sessionId)}/comparison`
  const body = await requestJson(endpoint)
  if (!isComparison(body)) throw new Error(`GET ${endpoint} returned a malformed Forward comparison.`)
  return body
}
