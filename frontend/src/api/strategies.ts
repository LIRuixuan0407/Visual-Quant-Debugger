import type { StrategyDefinition } from '../types/strategy'
import { readJson } from './client'
import { addCreatedObjectToCurrentWorkspace } from './workspaces'

function isStrategyDefinition(value: unknown): value is StrategyDefinition {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  if (
    typeof candidate.strategy_id !== 'string'
    || typeof candidate.name !== 'string'
    || !Array.isArray(candidate.parameters)
    || !Array.isArray(candidate.presets)
    || !Array.isArray(candidate.pipeline)
    || !Array.isArray(candidate.validation_rules)
    || !Array.isArray(candidate.execution_assumptions)
  ) return false
  return candidate.parameters.every((parameter) => (
    typeof parameter === 'object'
    && parameter !== null
    && typeof (parameter as Record<string, unknown>).key === 'string'
    && typeof (parameter as Record<string, unknown>).default_value === 'number'
  )) && candidate.presets.every((preset) => (
    typeof preset === 'object'
    && preset !== null
    && typeof (preset as Record<string, unknown>).parameters === 'object'
  ))
}

export async function getStrategyDefinitions(): Promise<StrategyDefinition[]> {
  const response = await fetch('/api/strategies')
  const body = await readJson(response, 'GET /api/strategies')
  if (!Array.isArray(body) || !body.every(isStrategyDefinition)) {
    throw new Error('GET /api/strategies returned a malformed Strategy Library.')
  }
  return body
}

export async function importStrategy(input: { path: string; class_name?: string | null }): Promise<StrategyDefinition> {
  const response = await fetch('/api/strategies/import', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  })
  const body = await readJson(response, 'POST /api/strategies/import')
  if (!isStrategyDefinition(body)) throw new Error('Imported strategy definition is malformed.')
  await addCreatedObjectToCurrentWorkspace('STRATEGY', body.strategy_id)
  return body
}

export async function getStrategyDefinition(strategyId = 'pairs-trading'): Promise<StrategyDefinition> {
  const endpoint = `/api/strategies/${encodeURIComponent(strategyId)}`
  const response = await fetch(endpoint)
  const body = await readJson(response, `GET ${endpoint}`)
  if (!isStrategyDefinition(body)) {
    throw new Error(`GET ${endpoint} returned a malformed Strategy Definition.`)
  }
  return body
}
