import type { BacktestCreated } from '../types/trace'
import type {
  RunAnnotations,
  RunComparisonReport,
  RunDetail,
  RunListResponse,
  RunValidationReport,
  RunStatus,
  StrategySourceArtifact,
} from '../types/run'
import { readJson } from './client'

export interface RunFilters {
  strategy_id?: string
  dataset_id?: string
  status?: RunStatus | ''
  search?: string
  limit?: number
  offset?: number
}

export async function getRuns(filters: RunFilters = {}): Promise<RunListResponse> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  const endpoint = `/api/runs${query.size ? `?${query.toString()}` : ''}`
  const response = await fetch(endpoint)
  return readJson(response, `GET ${endpoint}`) as Promise<RunListResponse>
}

export async function getRun(runId: string): Promise<RunDetail> {
  const endpoint = `/api/runs/${encodeURIComponent(runId)}`
  return readJson(await fetch(endpoint), `GET ${endpoint}`) as Promise<RunDetail>
}

export async function getRunStrategySource(runId: string): Promise<StrategySourceArtifact> {
  const endpoint = `/api/runs/${encodeURIComponent(runId)}/strategy-source`
  return readJson(await fetch(endpoint), `GET ${endpoint}`) as Promise<StrategySourceArtifact>
}

export async function saveRunAnnotations(runId: string, annotations: RunAnnotations): Promise<RunAnnotations> {
  const endpoint = `/api/runs/${encodeURIComponent(runId)}/annotations`
  const response = await fetch(endpoint, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(annotations),
  })
  return readJson(response, `PATCH ${endpoint}`) as Promise<RunAnnotations>
}

export async function deleteRun(runId: string): Promise<void> {
  const endpoint = `/api/runs/${encodeURIComponent(runId)}`
  const response = await fetch(endpoint, { method: 'DELETE' })
  if (!response.ok) await readJson(response, `DELETE ${endpoint}`)
}

export async function rerunExactRevision(runId: string): Promise<BacktestCreated> {
  const endpoint = `/api/runs/${encodeURIComponent(runId)}/rerun`
  return readJson(await fetch(endpoint, { method: 'POST' }), `POST ${endpoint}`) as Promise<BacktestCreated>
}

export async function compareRuns(runIds: string[]): Promise<RunComparisonReport> {
  const endpoint = '/api/run-comparisons'
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds }),
  })
  return readJson(response, `POST ${endpoint}`) as Promise<RunComparisonReport>
}

export async function validateRuns(backtestRunId: string, paperRunId: string): Promise<RunValidationReport> {
  const endpoint = '/api/run-validations'
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ backtest_run_id: backtestRunId, paper_run_id: paperRunId }),
  })
  return readJson(response, `POST ${endpoint}`) as Promise<RunValidationReport>
}
