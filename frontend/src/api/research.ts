import { readJson } from './client'
import type { CombinationMethod, DiscoverySuggestion, ExperimentComparisonReport, FactorRelationshipRecord, HypothesisIntegrityReport, PortfolioResearchRecord, PortfolioResearchSummary, RebalanceRule, ResearchHypothesis, ResearchSnapshot, ResearchSnapshotSummary, WalkForwardResearchRecord, WorkspaceIntegrityReport } from '../types/research'
import type { ResearchStage } from '../types/factor'

async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, { method: 'POST', headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  return readJson(response, `POST ${path}`) as Promise<T>
}
export async function getPortfolioResearchList(): Promise<PortfolioResearchSummary[]> { return readJson(await fetch('/api/portfolio-research'), 'GET /api/portfolio-research') as Promise<PortfolioResearchSummary[]> }
export async function getPortfolioResearch(id: string): Promise<PortfolioResearchRecord> { return readJson(await fetch(`/api/portfolio-research/${encodeURIComponent(id)}`), 'GET portfolio research') as Promise<PortfolioResearchRecord> }
export function createPortfolioResearch(input: { name: string; factors: Array<{ research_id: string; weight: number; direction_override: 'HIGH' | 'LOW' | null }>; combination: CombinationMethod; filters: { minimum_liquidity: number | null; maximum_volatility: number | null; require_factor_availability: boolean; include_symbols: string[]; exclude_symbols: string[] }; construction: { selection: 'TOP_N' | 'TOP_PERCENT'; top_n: number; top_percent: number; weighting: 'EQUAL_WEIGHT' | 'SCORE_WEIGHTED'; max_single_position_weight: number }; rebalance: RebalanceRule; gross_notional: number; initial_cash: number; fee_bps: number; slippage_bps: number }): Promise<PortfolioResearchRecord> { return post('/api/portfolio-research', input) }
export function revealPortfolio(id: string, action: 'validate' | 'reveal-holdout'): Promise<PortfolioResearchRecord> { return post(`/api/portfolio-research/${encodeURIComponent(id)}/${action}`) }
export function createPortfolioStrategy(id: string): Promise<{ strategy_id: string; source_fingerprint: string }> { return post(`/api/portfolio-research/${encodeURIComponent(id)}/strategy`) }

export async function getWalkForwardList(): Promise<WalkForwardResearchRecord[]> { return readJson(await fetch('/api/walk-forward'), 'GET /api/walk-forward') as Promise<WalkForwardResearchRecord[]> }
export function createWalkForward(input: { name: string; factor_research_id: string; strategy_id: string | null; config: { research_months: number; validation_months: number; forward_months: number; step_months: number; start: string | null; end: string | null }; horizon: 1 | 5 | 20; initial_cash: number; fee_bps: number; slippage_bps: number; strategy_parameters: Record<string, number> }): Promise<WalkForwardResearchRecord> { return post('/api/walk-forward', input) }

export async function getRelationshipList(): Promise<FactorRelationshipRecord[]> { return readJson(await fetch('/api/factor-relationships'), 'GET /api/factor-relationships') as Promise<FactorRelationshipRecord[]> }
export function createRelationship(input: { name: string; factor_research_ids: string[]; stage: ResearchStage; horizon: 1 | 5 | 20; rolling_window: number; top_percent: number; redundancy_threshold: number; overlap_threshold: number }): Promise<FactorRelationshipRecord> { return post('/api/factor-relationships', input) }

export async function getHypotheses(): Promise<ResearchHypothesis[]> { return readJson(await fetch('/api/hypotheses'), 'GET /api/hypotheses') as Promise<ResearchHypothesis[]> }
export async function getDiscoverySuggestions(): Promise<DiscoverySuggestion[]> { return readJson(await fetch('/api/hypotheses/suggestions'), 'GET hypothesis suggestions') as Promise<DiscoverySuggestion[]> }
export function createHypothesis(input: { title: string; description: string; universe: string[]; factor_research_ids: string[]; expected_relationship: string; holding_horizon: string; rebalance_idea: RebalanceRule; risk_assumptions: string[] }): Promise<ResearchHypothesis> { return post('/api/hypotheses', input) }
export function createHypothesisRevision(id: string, input: { title?: string; description?: string; universe?: string[]; factor_research_ids?: string[]; expected_relationship?: string; holding_horizon?: string; rebalance_idea?: RebalanceRule; risk_assumptions?: string[]; revision_reason: string }): Promise<ResearchHypothesis> { return post(`/api/hypotheses/${encodeURIComponent(id)}/revisions`, input) }
export function hypothesisAction(id: string, action: 'candidate' | 'validate' | 'reveal-holdout' | 'strategy'): Promise<ResearchHypothesis> { return post(`/api/hypotheses/${encodeURIComponent(id)}/${action}`) }
export function attachHypothesisRun(id: string, runId: string, traceId: string): Promise<ResearchHypothesis> { return post(`/api/hypotheses/${encodeURIComponent(id)}/runs`, { run_id: runId, trace_id: traceId }) }

export async function getResearchSnapshots(): Promise<ResearchSnapshotSummary[]> { return readJson(await fetch('/api/research-snapshots'), 'GET /api/research-snapshots') as Promise<ResearchSnapshotSummary[]> }
export async function getResearchSnapshot(id: string): Promise<ResearchSnapshot> { return readJson(await fetch(`/api/research-snapshots/${encodeURIComponent(id)}`), 'GET research snapshot') as Promise<ResearchSnapshot> }
export function createResearchSnapshot(input: { name: string; hypothesis_id: string }): Promise<ResearchSnapshot> { return post('/api/research-snapshots', input) }
export function compareResearchSnapshots(snapshotIds: string[]): Promise<ExperimentComparisonReport> { return post('/api/research-snapshots/compare', { snapshot_ids: snapshotIds }) }

export async function getWorkspaceIntegrity(): Promise<WorkspaceIntegrityReport> { return readJson(await fetch('/api/research-integrity'), 'GET /api/research-integrity') as Promise<WorkspaceIntegrityReport> }
export async function getHypothesisIntegrity(id: string): Promise<HypothesisIntegrityReport> { return readJson(await fetch(`/api/research-integrity/${encodeURIComponent(id)}`), 'GET hypothesis integrity') as Promise<HypothesisIntegrityReport> }
