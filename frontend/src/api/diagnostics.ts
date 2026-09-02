import { readJson } from './client'
import type { DiagnosisReport, DiagnosticMetrics, WhatIfInputs, WhatIfScenario } from '../types/diagnostics'

function isMetrics(value: unknown): value is DiagnosticMetrics {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return typeof item.return === 'number' && typeof item.sharpe === 'number' && typeof item.status === 'string'
}

function isDiagnosisReport(value: unknown): value is DiagnosisReport {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  const split = item.train_test as Record<string, unknown> | undefined
  return (
    item.report_version === '1.0' &&
    typeof item.source_run === 'object' && item.source_run !== null &&
    typeof split === 'object' && split !== null &&
    isMetrics(split.train) && isMetrics(split.test) &&
    Array.isArray(item.lookback_sensitivity) &&
    Array.isArray(item.cost_stress) &&
    Array.isArray(item.execution_delay) &&
    Array.isArray(item.observations)
  )
}

export async function createDiagnosis(traceId: string): Promise<DiagnosisReport> {
  const response = await fetch('/api/diagnostics', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trace_id: traceId }),
  })
  const body = await readJson(response, 'POST /api/diagnostics')
  if (!isDiagnosisReport(body)) throw new Error('POST /api/diagnostics returned a malformed report.')
  return body
}

function isWhatIfScenario(value: unknown): value is WhatIfScenario {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return (
    typeof item.baseline_inputs === 'object' && item.baseline_inputs !== null &&
    typeof item.inputs === 'object' && item.inputs !== null &&
    typeof item.baseline_metrics === 'object' && item.baseline_metrics !== null &&
    typeof item.stressed_metrics === 'object' && item.stressed_metrics !== null &&
    typeof item.deltas === 'object' && item.deltas !== null &&
    typeof item.verdict === 'string'
  )
}

export async function createWhatIfScenario(traceId: string, inputs: WhatIfInputs): Promise<WhatIfScenario> {
  const response = await fetch('/api/diagnostics/what-if', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trace_id: traceId, inputs }),
  })
  const body = await readJson(response, 'POST /api/diagnostics/what-if')
  if (!isWhatIfScenario(body)) throw new Error('POST /api/diagnostics/what-if returned a malformed scenario.')
  return body
}
