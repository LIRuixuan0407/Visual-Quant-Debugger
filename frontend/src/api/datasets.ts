import { readJson } from './client'
import type { CompatibilityCheck, DatasetDefinition, DatasetFamily, DatasetPreview, DatasetRevisionDiff } from '../types/dataset'
import type { StrategyParameters } from '../types/strategy'
import { addCreatedObjectToCurrentWorkspace } from './workspaces'

function isDataset(value: unknown): value is DatasetDefinition {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return typeof item.dataset_id === 'string'
    && typeof item.name === 'string'
    && Array.isArray(item.symbols)
    && Array.isArray(item.fields)
    && typeof item.quality === 'object'
}

export async function getDatasets(workspaceId?: string): Promise<DatasetDefinition[]> {
  const endpoint = workspaceId ? `/api/datasets?workspace_id=${encodeURIComponent(workspaceId)}` : '/api/datasets'
  const response = await fetch(endpoint)
  const body = await readJson(response, `GET ${endpoint}`)
  if (!Array.isArray(body) || !body.every(isDataset)) {
    throw new Error('GET /api/datasets returned a malformed Dataset Library.')
  }
  return body
}

export async function getDatasetFamilies(): Promise<DatasetFamily[]> {
  const response = await fetch('/api/dataset-families')
  const body = await readJson(response, 'GET /api/dataset-families')
  if (!Array.isArray(body)) throw new Error('GET /api/dataset-families returned malformed data.')
  return body as DatasetFamily[]
}

export async function getDatasetFamilyRevisions(datasetFamilyId: string): Promise<DatasetDefinition[]> {
  const endpoint = `/api/dataset-families/${encodeURIComponent(datasetFamilyId)}/revisions`
  const body = await readJson(await fetch(endpoint), `GET ${endpoint}`)
  if (!Array.isArray(body) || !body.every(isDataset)) throw new Error(`GET ${endpoint} returned malformed revisions.`)
  return body
}

export async function compareDatasets(left: string, right: string): Promise<DatasetRevisionDiff> {
  const endpoint = `/api/datasets/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`
  return readJson(await fetch(endpoint), `GET ${endpoint}`) as Promise<DatasetRevisionDiff>
}

export async function refreshProviderDataset(datasetId: string, end: string, revisionReason?: string): Promise<DatasetDefinition> {
  const endpoint = `/api/datasets/${encodeURIComponent(datasetId)}/refresh`
  const body = await readJson(await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ end, revision_reason: revisionReason || null }),
  }), `POST ${endpoint}`)
  if (!isDataset(body)) throw new Error(`POST ${endpoint} returned malformed metadata.`)
  await addCreatedObjectToCurrentWorkspace('DATASET', body.dataset_id)
  return body
}

export async function getDatasetRows(datasetId: string): Promise<Array<Record<string, string | number>>> {
  const endpoint = `/api/datasets/${encodeURIComponent(datasetId)}/preview`
  const response = await fetch(endpoint)
  const body = await readJson(response, `GET ${endpoint}`) as { rows?: unknown }
  if (!Array.isArray(body.rows)) throw new Error(`GET ${endpoint} returned a malformed preview.`)
  return body.rows as Array<Record<string, string | number>>
}

export async function previewDataset(file: File): Promise<DatasetPreview> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch('/api/datasets/import/preview', { method: 'POST', body: form })
  return readJson(response, 'POST /api/datasets/import/preview') as Promise<DatasetPreview>
}

export async function importDataset(input: {
  preview_id: string
  name: string
  mapping: Record<string, string>
  timezone: string | null
  dataset_family_id?: string | null
  revision_reason?: string | null
}): Promise<DatasetDefinition> {
  const response = await fetch('/api/datasets/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  const body = await readJson(response, 'POST /api/datasets/import')
  if (!isDataset(body)) throw new Error('POST /api/datasets/import returned malformed metadata.')
  await addCreatedObjectToCurrentWorkspace('DATASET', body.dataset_id)
  return body
}

export async function checkCompatibility(input: {
  strategy_id: string
  dataset_id: string
  parameters: StrategyParameters
}): Promise<CompatibilityCheck> {
  const response = await fetch('/api/compatibility-checks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return readJson(response, 'POST /api/compatibility-checks') as Promise<CompatibilityCheck>
}
