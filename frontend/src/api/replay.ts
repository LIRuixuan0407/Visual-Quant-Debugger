import type { BacktestCreated, BacktestTrace, RunContext } from '../types/trace'
import type { StrategyParameters } from '../types/strategy'
import { readJson } from './client'

function isBacktestCreated(value: unknown): value is BacktestCreated {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.run_id === 'string' &&
    typeof candidate.run_fingerprint === 'string' &&
    (typeof candidate.trace_id === 'string' || candidate.trace_id === null) &&
    candidate.trace_version === '1.0' &&
    (candidate.summary === null || typeof candidate.summary === 'object')
  )
}

function isBacktestTrace(value: unknown): value is BacktestTrace {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  if (candidate.trace_version !== '1.0' || !Array.isArray(candidate.timeline)) return false
  return candidate.timeline.every((event) => {
    if (typeof event !== 'object' || event === null) return false
    const item = event as Record<string, unknown>
    return (
      typeof item.event_id === 'string' &&
      typeof item.timestamp === 'string' &&
      typeof item.signal_evaluation === 'object' &&
      Array.isArray(item.feature_snapshots) &&
      Array.isArray(item.execution_events)
    )
  })
}

export async function createBacktest(input: StrategyParameters | {
  strategy_id: string
  dataset_id: string
  parameters: StrategyParameters
  research_cutoff?: string | null
}): Promise<BacktestCreated> {
  const payload = 'strategy_id' in input
    ? input
    : { strategy: 'pairs-trading', parameters: input }
  const response = await fetch('/api/backtests', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const body = await readJson(response, 'POST /api/backtests')
  if (!isBacktestCreated(body)) {
    throw new Error('POST /api/backtests returned a malformed response.')
  }
  return body
}

export async function getRunContext(traceId: string): Promise<RunContext> {
  const endpoint = `/api/traces/${encodeURIComponent(traceId)}/context`
  const response = await fetch(endpoint)
  return readJson(response, `GET ${endpoint}`) as Promise<RunContext>
}

export async function getTrace(traceId: string): Promise<BacktestTrace> {
  const endpoint = `/api/traces/${encodeURIComponent(traceId)}`
  const response = await fetch(endpoint)
  const body = await readJson(response, `GET ${endpoint}`)
  if (!isBacktestTrace(body)) {
    throw new Error(`GET ${endpoint} returned a malformed Trace 1.0 payload.`)
  }
  return body
}
