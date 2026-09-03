import { readJson } from './client'
import type { CombinationMethod, DiscoverySuggestion, ExperimentComparisonReport, FactorRelationshipRecord, HypothesisIntegrityReport, PortfolioResearchRecord, PortfolioResearchSummary, RebalanceRule, ResearchHypothesis, ResearchSnapshot, ResearchSnapshotSummary, ResearchWorkspace, ResearchWorkspaceSummary, WalkForwardResearchRecord, WorkspaceIntegrityReport } from '../types/research'
import type { ResearchStage } from '../types/factor'
import { addCreatedObjectToCurrentWorkspace } from './workspaces'

async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, { method: 'POST', headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  return readJson(response, `POST ${path}`) as Promise<T>
}
export async function getPortfolioResearchList(): Promise<PortfolioResearchSummary[]> { return readJson(await fetch('/api/portfolio-research'), 'GET /api/portfolio-research') as Promise<PortfolioResearchSummary[]> }
export async function getPortfolioResearch(id: string): Promise<PortfolioResearchRecord> { return readJson(await fetch(`/api/portfolio-research/${encodeURIComponent(id)}`), 'GET portfolio research') as Promise<PortfolioResearchRecord> }
export async function createPortfolioResearch(input: { name: string; factors: Array<{ research_id: string; weight: number; direction_override: 'HIGH' | 'LOW' | null }>; combination: CombinationMethod; filters: { minimum_liquidity: number | null; maximum_volatility: number | null; require_factor_availability: boolean; include_symbols: string[]; exclude_symbols: string[] }; construction: { mode: 'LONG_ONLY' | 'LONG_SHORT'; selection: 'TOP_N' | 'TOP_PERCENT'; top_n: number; top_percent: number; weighting: 'EQUAL_WEIGHT' | 'SCORE_WEIGHTED'; max_single_position_weight: number }; rebalance: RebalanceRule; gross_notional: number; initial_cash: number; fee_bps: number; slippage_bps: number; spread_bps: number; market_impact_bps: number }): Promise<PortfolioResearchRecord> { const created = await post<PortfolioResearchRecord>('/api/portfolio-research', input); await addCreatedObjectToCurrentWorkspace('PORTFOLIO_RESEARCH', created.portfolio_research_id); return created }
export function revealPortfolio(id: string, action: 'validate' | 'reveal-holdout'): Promise<PortfolioResearchRecord> { return post(`/api/portfolio-research/${encodeURIComponent(id)}/${action}`) }
export async function createPortfolioStrategy(id: string): Promise<{ strategy_id: string; source_fingerprint: string }> { const created = await post<{ strategy_id: string; source_fingerprint: string }>(`/api/portfolio-research/${encodeURIComponent(id)}/strategy`); await addCreatedObjectToCurrentWorkspace('STRATEGY', created.strategy_id); return created }

export async function getWalkForwardList(): Promise<WalkForwardResearchRecord[]> { return readJson(await fetch('/api/walk-forward'), 'GET /api/walk-forward') as Promise<WalkForwardResearchRecord[]> }
export async function createWalkForward(input: { name: string; factor_research_id: string; strategy_id: string | null; config: { research_months: number; validation_months: number; forward_months: number; step_months: number; start: string | null; end: string | null }; horizon: 1 | 5 | 20; initial_cash: number; fee_bps: number; slippage_bps: number; strategy_parameters: Record<string, number> }): Promise<WalkForwardResearchRecord> { const created = await post<WalkForwardResearchRecord>('/api/walk-forward', input); await addCreatedObjectToCurrentWorkspace('WALK_FORWARD', created.walk_forward_id); if (created.run_id) await addCreatedObjectToCurrentWorkspace('RUN', created.run_id); return created }

export async function getRelationshipList(): Promise<FactorRelationshipRecord[]> { return readJson(await fetch('/api/factor-relationships'), 'GET /api/factor-relationships') as Promise<FactorRelationshipRecord[]> }
export async function createRelationship(input: { name: string; factor_research_ids: string[]; stage: ResearchStage; horizon: 1 | 5 | 20; rolling_window: number; top_percent: number; redundancy_threshold: number; overlap_threshold: number }): Promise<FactorRelationshipRecord> { const created = await post<FactorRelationshipRecord>('/api/factor-relationships', input); await addCreatedObjectToCurrentWorkspace('FACTOR_RELATIONSHIP', created.relationship_id); return created }

export async function getHypotheses(): Promise<ResearchHypothesis[]> { return readJson(await fetch('/api/hypotheses'), 'GET /api/hypotheses') as Promise<ResearchHypothesis[]> }
export async function getDiscoverySuggestions(): Promise<DiscoverySuggestion[]> { return readJson(await fetch('/api/hypotheses/suggestions'), 'GET hypothesis suggestions') as Promise<DiscoverySuggestion[]> }
export async function createHypothesis(input: { title: string; description: string; universe: string[]; factor_research_ids: string[]; expected_relationship: string; holding_horizon: string; rebalance_idea: RebalanceRule; risk_assumptions: string[] }): Promise<ResearchHypothesis> { const created = await post<ResearchHypothesis>('/api/hypotheses', input); await addCreatedObjectToCurrentWorkspace('HYPOTHESIS', created.hypothesis_id); return created }
export async function createHypothesisRevision(id: string, input: { title?: string; description?: string; universe?: string[]; factor_research_ids?: string[]; expected_relationship?: string; holding_horizon?: string; rebalance_idea?: RebalanceRule; risk_assumptions?: string[]; revision_reason: string }): Promise<ResearchHypothesis> { const created = await post<ResearchHypothesis>(`/api/hypotheses/${encodeURIComponent(id)}/revisions`, input); await addCreatedObjectToCurrentWorkspace('HYPOTHESIS', created.hypothesis_id); return created }
export async function hypothesisAction(id: string, action: 'candidate' | 'validate' | 'reveal-holdout' | 'strategy'): Promise<ResearchHypothesis> { const updated = await post<ResearchHypothesis>(`/api/hypotheses/${encodeURIComponent(id)}/${action}`); if (action === 'candidate' && updated.lineage.portfolio_research_id) await addCreatedObjectToCurrentWorkspace('PORTFOLIO_RESEARCH', updated.lineage.portfolio_research_id); if (action === 'strategy' && updated.lineage.strategy_id) await addCreatedObjectToCurrentWorkspace('STRATEGY', updated.lineage.strategy_id); return updated }
export function attachHypothesisRun(id: string, runId: string, traceId: string): Promise<ResearchHypothesis> { return post(`/api/hypotheses/${encodeURIComponent(id)}/runs`, { run_id: runId, trace_id: traceId }) }

export async function getResearchSnapshots(): Promise<ResearchSnapshotSummary[]> { return readJson(await fetch('/api/research-snapshots'), 'GET /api/research-snapshots') as Promise<ResearchSnapshotSummary[]> }
export async function getResearchSnapshot(id: string): Promise<ResearchSnapshot> { return readJson(await fetch(`/api/research-snapshots/${encodeURIComponent(id)}`), 'GET research snapshot') as Promise<ResearchSnapshot> }
export async function createResearchSnapshot(input: { name: string; hypothesis_id: string }): Promise<ResearchSnapshot> { const created = await post<ResearchSnapshot>('/api/research-snapshots', input); await addCreatedObjectToCurrentWorkspace('SNAPSHOT', created.snapshot_id); return created }
export function compareResearchSnapshots(snapshotIds: string[]): Promise<ExperimentComparisonReport> { return post('/api/research-snapshots/compare', { snapshot_ids: snapshotIds }) }

export async function getWorkspaceIntegrity(): Promise<WorkspaceIntegrityReport> { return readJson(await fetch('/api/research-integrity'), 'GET /api/research-integrity') as Promise<WorkspaceIntegrityReport> }
export async function getHypothesisIntegrity(id: string): Promise<HypothesisIntegrityReport> { return readJson(await fetch(`/api/research-integrity/${encodeURIComponent(id)}`), 'GET hypothesis integrity') as Promise<HypothesisIntegrityReport> }

export async function getResearchWorkspaces(workspaceId?: string): Promise<ResearchWorkspaceSummary[]> { const endpoint = workspaceId ? `/api/research-workspaces?workspace_id=${encodeURIComponent(workspaceId)}` : '/api/research-workspaces'; return readJson(await fetch(endpoint), `GET ${endpoint}`) as Promise<ResearchWorkspaceSummary[]> }
export async function getResearchWorkspace(id: string): Promise<ResearchWorkspace> { return readJson(await fetch(`/api/research-workspaces/${encodeURIComponent(id)}`), 'GET research workspace') as Promise<ResearchWorkspace> }

export async function exportResearchBundle(input: { mode: import('../types/research').ResearchBundleMode; root_objects: import('../types/research').ResearchBundleRootObject[] }): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch('/api/research-bundles/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!response.ok) {
    await readJson(response, 'POST /api/research-bundles/export')
    throw new Error('Research Bundle export failed')
  }
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = /filename="([^"]+)"/.exec(disposition)
  return { blob: await response.blob(), filename: match?.[1] ?? 'research-bundle.vqd-bundle.zip' }
}

export async function previewResearchBundle(file: File): Promise<import('../types/research').ResearchBundleImportPreview> {
  const response = await fetch('/api/research-bundles/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/zip' },
    body: file,
  })
  return readJson(response, 'POST /api/research-bundles/preview') as Promise<import('../types/research').ResearchBundleImportPreview>
}

export async function importResearchBundle(previewId: string, targetWorkspaceId: string): Promise<import('../types/research').ResearchBundleImportResult> {
  return readJson(
    await fetch(`/api/research-bundles/import/${encodeURIComponent(previewId)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target_workspace_id: targetWorkspaceId }) }),
    'POST research bundle import',
  ) as Promise<import('../types/research').ResearchBundleImportResult>
}
