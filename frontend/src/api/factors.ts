import { readJson } from './client'
import type {
  FactorDefinition,
  FactorImportResult,
  FactorComponent,
  FactorObservation,
  FactorResearchRecord,
  FactorResearchSummary,
  FactorStrategyArtifact,
  HistoricalMarketView,
  ResearchPeriods,
} from '../types/factor'
import { addCreatedObjectToCurrentWorkspace } from './workspaces'

interface FactorInspectionResponse { observation: FactorObservation }

export async function getFactors(): Promise<FactorDefinition[]> {
  const response = await fetch('/api/factors')
  return readJson(response, 'GET /api/factors') as Promise<FactorDefinition[]>
}

export async function importFactor(input: { path: string; class_name?: string | null }): Promise<FactorImportResult> {
  const response = await fetch('/api/factors/import', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  })
  return readJson(response, 'POST /api/factors/import') as Promise<FactorImportResult>
}

export async function getFactorResearchList(): Promise<FactorResearchSummary[]> {
  const response = await fetch('/api/factor-research')
  return readJson(response, 'GET /api/factor-research') as Promise<FactorResearchSummary[]>
}

export async function getFactorResearch(researchId: string): Promise<FactorResearchRecord> {
  const response = await fetch(`/api/factor-research/${encodeURIComponent(researchId)}`)
  return readJson(response, 'GET factor research') as Promise<FactorResearchRecord>
}

export async function createFactorResearch(input: {
  name: string
  dataset_id: string
  factor_id: string
  parameters: Record<string, number>
  periods: ResearchPeriods
  universe: string[]
  universe_id?: string | null
  fundamental_dataset_id?: string | null
  corporate_action_dataset_id?: string | null
  price_adjustment_policy?: 'RAW' | 'SPLIT_ADJUSTED'
  components?: FactorComponent[]
}): Promise<FactorResearchRecord> {
  const response = await fetch('/api/factor-research', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  })
  const created = await readJson(response, 'POST /api/factor-research') as FactorResearchRecord
  await addCreatedObjectToCurrentWorkspace('FACTOR_RESEARCH', created.research_id)
  return created
}

async function stageAction(researchId: string, action: 'validate' | 'reveal-holdout'): Promise<FactorResearchRecord> {
  const response = await fetch(`/api/factor-research/${encodeURIComponent(researchId)}/${action}`, { method: 'POST' })
  return readJson(response, `POST factor research ${action}`) as Promise<FactorResearchRecord>
}

export const validateFactorResearch = (researchId: string) => stageAction(researchId, 'validate')
export const revealFactorHoldout = (researchId: string) => stageAction(researchId, 'reveal-holdout')

export async function inspectFactor(researchId: string, symbol: string, timestamp: string): Promise<FactorObservation> {
  const query = new URLSearchParams({ symbol, timestamp })
  const endpoint = `/api/factor-research/${encodeURIComponent(researchId)}/inspect?${query}`
  const response = await fetch(endpoint)
  const body = await readJson(response, 'GET factor inspection') as FactorInspectionResponse
  return body.observation
}

export async function createFactorStrategy(researchId: string, input: {
  long_percent: number
  rebalance_bars: number
  gross_notional: number
  max_volatility: number | null
}): Promise<FactorStrategyArtifact> {
  const response = await fetch(`/api/factor-research/${encodeURIComponent(researchId)}/strategy`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  })
  const created = await readJson(response, 'POST factor strategy') as FactorStrategyArtifact
  await addCreatedObjectToCurrentWorkspace('STRATEGY', created.strategy_id)
  return created
}

export async function getHistoricalMarket(datasetId: string, asOf: string, symbol?: string, fundamentalDatasetId?: string): Promise<HistoricalMarketView> {
  const query = new URLSearchParams({ as_of: asOf })
  if (symbol) query.set('symbol', symbol)
  if (fundamentalDatasetId) query.set('fundamental_dataset_id', fundamentalDatasetId)
  const endpoint = `/api/historical-market/${encodeURIComponent(datasetId)}?${query}`
  const response = await fetch(endpoint)
  return readJson(response, 'GET historical market') as Promise<HistoricalMarketView>
}
