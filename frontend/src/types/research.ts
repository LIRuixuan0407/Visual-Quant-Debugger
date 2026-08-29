import type { ResearchStage } from './factor'
import type { RunComparisonReport } from './run'

export type CombinationMethod = 'EQUAL_WEIGHT' | 'USER_DEFINED_WEIGHT' | 'RANK_AVERAGE' | 'Z_SCORE_COMPOSITE'
export type RebalanceRule = 'DAILY' | 'WEEKLY' | 'MONTHLY'

export interface PortfolioFactorRef { research_id: string; weight: number; direction_override: 'HIGH' | 'LOW' | null }
export interface FactorScoreEvidence { research_id: string; factor_id: string; factor_name: string; direction: 'HIGH' | 'LOW'; available: boolean; raw_value: number | null; rank: number | null; universe_count: number; normalized_score: number | null; contribution: number }
export interface PortfolioFactorCheck { research_id: string; factor_id: string; factor_name: string; origin: 'BUILT_IN' | 'CUSTOM'; category: 'PRICE_VOLUME' | 'VALUE' | 'QUALITY' | 'GROWTH' | 'LEVERAGE' | 'MIXED'; data_source: 'MARKET' | 'FUNDAMENTAL' | 'MIXED'; direction: 'HIGH' | 'LOW'; effective_weight: number; available_observations: number; expected_observations: number; missing_observations: number; coverage: number }
export interface PortfolioPositionLineage { symbol: string; selected: boolean; liquidity: number | null; volatility: number | null; filter_status: string[]; factors: FactorScoreEvidence[]; composite_score: number | null; portfolio_rank: number | null; target_weight: number }
export interface CostPreview { gross_return: number; fees: number; slippage: number; net_return: number; turnover: number; max_drawdown: number; positions: number; rebalance_count: number }
export interface PortfolioStageResult { stage: ResearchStage; period: { start: string; end: string }; factor_checks: PortfolioFactorCheck[]; snapshots: Array<{ timestamp: string; stage: ResearchStage; eligible_count: number; selected_symbols: string[]; positions: PortfolioPositionLineage[] }>; cost_preview: CostPreview }
export interface PortfolioResearchRecord {
  portfolio_research_id: string; name: string; created_at: string; dataset_id: string; dataset_fingerprint: string; universe: string[]; factor_refs: PortfolioFactorRef[]; factor_ids: string[]; factor_names: string[]; combination: CombinationMethod; filters: { minimum_liquidity: number | null; maximum_volatility: number | null; require_factor_availability: boolean; include_symbols: string[]; exclude_symbols: string[] }; construction: { selection: 'TOP_N' | 'TOP_PERCENT'; top_n: number; top_percent: number; weighting: 'EQUAL_WEIGHT' | 'SCORE_WEIGHTED'; max_single_position_weight: number }; rebalance: RebalanceRule; gross_notional: number; initial_cash: number; fee_bps: number; slippage_bps: number; revealed_stage: ResearchStage; stages: PortfolioStageResult[]; strategy: { strategy_id: string; source_fingerprint: string } | null
}
export interface PortfolioResearchSummary { portfolio_research_id: string; name: string; created_at: string; dataset_id: string; factor_count: number; combination: CombinationMethod; revealed_stage: ResearchStage; net_return: number; turnover: number }

export interface WalkForwardWindowResult { definition: { index: number; research: { start: string; end: string }; validation: { start: string; end: string }; forward: { start: string; end: string } }; research: FactorWindowMetrics; validation: FactorWindowMetrics; forward: FactorWindowMetrics; forward_strategy: StrategyWindowMetrics | null }
export interface FactorWindowMetrics { observation_count: number; cross_section_count: number; ic: number | null; rank_ic: number | null; quantile_returns: Array<number | null>; spread: number | null; coverage: number; turnover: number | null; monotonic: boolean }
export interface StrategyWindowMetrics { total_return: number; sharpe: number; max_drawdown: number; trades: number; fees: number; slippage: number; net_costs: number }
export interface MetricDistribution { count: number; mean: number | null; std: number | null; minimum: number | null; maximum: number | null }
export interface WalkForwardResearchRecord { walk_forward_id: string; name: string; created_at: string; factor_research_id: string; factor_id: string; factor_revision: string; strategy_id: string | null; strategy_revision: string | null; dataset_id: string; dataset_fingerprint: string; config: { research_months: number; validation_months: number; forward_months: number; step_months: number; start: string | null; end: string | null }; horizon: 1 | 5 | 20; initial_cash: number; fee_bps: number; slippage_bps: number; windows: WalkForwardWindowResult[]; stability: { positive_ic_window_ratio: number; rank_ic_distribution: MetricDistribution; factor_sign_consistency: number; quantile_monotonicity_stability: number; turnover_stability: number; strategy_return_distribution: MetricDistribution | null }; first_degradation: { window_index: number; timestamp: string; reasons: string[]; factor_research_id: string; strategy_id: string | null; run_id: string | null; historical_market_path: string; factor_lab_path: string; replay_path: string | null } | null; run_id: string | null; trace_id: string | null }

export type CorrelationSemantic = 'FACTOR_VALUES' | 'FACTOR_RANKS' | 'FACTOR_RETURNS'
export interface CorrelationCell { left_research_id: string; right_research_id: string; semantic: CorrelationSemantic; pearson: number | null; spearman: number | null; observations: number }
export interface RollingCorrelationPoint { timestamp: string; pearson: number | null; spearman: number | null; observations: number }
export interface RollingCorrelationSeries { left_research_id: string; right_research_id: string; semantic: CorrelationSemantic; window: number; points: RollingCorrelationPoint[] }
export interface ExposureOverlapPoint { timestamp: string; intersection_count: number; union_count: number; overlap_percent: number; jaccard: number }
export interface ExposureOverlap { left_research_id: string; right_research_id: string; top_percent: number; mean_intersection_count: number; mean_union_count: number; mean_overlap: number; mean_jaccard: number; timestamps: number; points: ExposureOverlapPoint[] }
export interface IncrementalInformation { base_research_id: string; added_research_id: string; normalization: 'DIRECTION_ADJUSTED_PERCENTILE_RANK_AVERAGE'; base_rank_ic: number | null; composite_rank_ic: number | null; rank_ic_delta: number | null; base_spread: number | null; composite_spread: number | null; spread_delta: number | null; base_coverage: number; composite_coverage: number; coverage_delta: number; base_turnover: number | null; composite_turnover: number | null; turnover_delta: number | null; base_portfolio_return: number | null; composite_portfolio_return: number | null; portfolio_effect: number | null }
export interface FactorRelationshipRecord {
  relationship_id: string
  name: string
  created_at: string
  stage: ResearchStage
  period: { start: string; end: string }
  horizon: 1 | 5 | 20
  rolling_window: number
  top_percent: number
  redundancy_threshold: number
  overlap_threshold: number
  dataset_id: string
  dataset_fingerprint: string
  universe: string[]
  factor_research_ids: string[]
  factor_ids: string[]
  factor_names: string[]
  factor_revisions: string[]
  value_correlations: CorrelationCell[]
  rank_correlations: CorrelationCell[]
  return_correlations: CorrelationCell[]
  rolling_correlations: RollingCorrelationSeries[]
  redundancy: Array<{ left_research_id: string; right_research_id: string; status: 'HIGH_REDUNDANCY' | 'RELATED' | 'LOW_REDUNDANCY'; rank_correlation: number | null; top_quantile_overlap: number | null; reason: string }>
  exposure_overlap: ExposureOverlap[]
  incremental_information: IncrementalInformation[]
  clusters: Array<{ cluster_id: string; factor_research_ids: string[]; rule: string }>
  correlation_methodology: string
  incremental_disclosure: string
  crowding_disclosure: string
}

export type OutcomeClassification = 'SUPPORTED' | 'MIXED' | 'NOT_SUPPORTED' | 'INSUFFICIENT_EVIDENCE'
export interface HypothesisEvidence { evidence_id: string; source_type: 'FACTOR' | 'RELATIONSHIP' | 'WALK_FORWARD' | 'PORTFOLIO'; source_id: string; stage: ResearchStage | 'WALK_FORWARD'; stance: 'SUPPORTING' | 'CONTRADICTING' | 'NEUTRAL'; label: string; detail: string; metrics: Record<string, number | string | boolean | null> }
export interface CandidateStrategyTemplate { combination: 'RANK_AVERAGE'; selection: 'TOP_PERCENT'; top_percent: number; weighting: 'EQUAL_WEIGHT'; max_single_position_weight: number; rebalance: RebalanceRule; long_only: true; portfolio_research_id: string | null }
export interface ResearchHypothesis { hypothesis_id: string; family_id: string; parent_hypothesis_id: string | null; revision: number; title: string; description: string; dataset_id: string; dataset_fingerprint: string; universe: string[]; factor_research_ids: string[]; expected_relationship: string; holding_horizon: string; rebalance_idea: RebalanceRule; risk_assumptions: string[]; created_at: string; status: 'DRAFT' | 'RESEARCHED' | 'VALIDATED' | 'HOLDOUT_REVEALED' | 'STRATEGY_CREATED'; outcome: OutcomeClassification; created_with_known_stage: ResearchStage; source_revealed_stages: Record<string, ResearchStage>; evidence: HypothesisEvidence[]; candidate: CandidateStrategyTemplate; lineage: { factor_research_ids: string[]; factor_ids: string[]; relationship_ids: string[]; walk_forward_ids: string[]; portfolio_research_id: string | null; strategy_id: string | null; run_ids: string[]; trace_ids: string[] }; revision_reason: string | null; ai_boundary: string }
export interface DiscoverySuggestion { label: 'RESEARCH IDEA'; factor_research_ids: string[]; rationale: string; source_relationship_id: string }

export type SnapshotArtifactKind = 'DATASET' | 'UNIVERSE' | 'CORPORATE_ACTION_DATASET' | 'FACTOR_RESEARCH' | 'FACTOR_RELATIONSHIP' | 'WALK_FORWARD' | 'HYPOTHESIS' | 'PORTFOLIO_RESEARCH' | 'STRATEGY_SOURCE' | 'RUN_MANIFEST' | 'TRACE'
export interface FrozenArtifact { kind: SnapshotArtifactKind; artifact_id: string; source_revision: string; payload_sha256: string; payload_json: string }
export interface SnapshotParameterSet { owner_type: 'HYPOTHESIS' | 'FACTOR' | 'PORTFOLIO' | 'STRATEGY' | 'RUN'; owner_id: string; values: Array<{ key: string; value: string | number | boolean | null }> }
export interface SnapshotPeriod { label: string; source_id: string; start: string | null; end: string | null; cutoff: string | null }
export interface ResearchSnapshotSummary { snapshot_id: string; name: string; created_at: string; content_fingerprint: string; hypothesis_id: string; hypothesis_revision: number; dataset_id: string; dataset_family_id?: string | null; dataset_revision?: number; factor_count: number; strategy_id: string; run_count: number; trace_count: number }
export interface ResearchSnapshot {
  snapshot_version: '1.0'
  snapshot_id: string
  name: string
  created_at: string
  content_fingerprint: string
  lineage: { dataset_id: string; dataset_family_id?: string | null; dataset_revision?: number; universe_ids: string[]; corporate_action_dataset_ids: string[]; factor_research_ids: string[]; factor_ids: string[]; relationship_ids: string[]; walk_forward_ids: string[]; hypothesis_id: string; hypothesis_revision: number; portfolio_research_id: string; strategy_id: string; run_ids: string[]; trace_ids: string[] }
  dataset: FrozenArtifact
  universes: FrozenArtifact[]
  corporate_actions: FrozenArtifact[]
  factors: FrozenArtifact[]
  relationships: FrozenArtifact[]
  walk_forward: FrozenArtifact[]
  hypothesis: FrozenArtifact
  portfolio: FrozenArtifact
  strategy: FrozenArtifact
  runs: FrozenArtifact[]
  traces: FrozenArtifact[]
  parameters: SnapshotParameterSet[]
  time_boundaries: { research: SnapshotPeriod; validation: SnapshotPeriod; holdout: SnapshotPeriod; runs: SnapshotPeriod[] }
  environment: { python_version: string; python_implementation: string; platform: string; machine: string; vqd_version: string; dependencies: Array<{ name: string; version: string }> }
  immutability_disclosure: string
}

export type ExperimentComparability = 'STRICTLY_COMPARABLE' | 'CONTEXTUALLY_COMPARABLE' | 'DESCRIPTIVE_ONLY'
export interface ExperimentComparisonReport {
  comparison_version: '1.0'
  snapshot_ids: string[]
  snapshots: Array<{ snapshot_id: string; name: string; content_fingerprint: string; hypothesis_id: string; hypothesis_revision: number; run_id: string; trace_id: string }>
  comparability: ExperimentComparability
  context_diff: Array<{ field: 'dataset_revision' | 'universe_revisions' | 'corporate_action_revisions' | 'research_periods' | 'run_period' | 'execution_model' | 'runtime' | 'creation_environment'; same: boolean; significance: 'STRICT_CONTROL' | 'CONTEXT' | 'INFORMATIONAL'; values: string[] }>
  artifact_diff: Array<{ kind: SnapshotArtifactKind; semantic_key: string; artifact_ids: Array<string | null>; source_revisions: Array<string | null>; payload_fingerprints: Array<string | null>; same_revision: boolean }>
  parameter_diff: Array<{ owner_type: 'HYPOTHESIS' | 'FACTOR' | 'PORTFOLIO' | 'STRATEGY' | 'RUN'; owner_key: string; parameter: string; values: Array<string | number | boolean | null>; changed: true }>
  metric_diff: Array<{ scope: string; metric: string; values: Array<number | null>; differences_from_first: Array<number | null> }>
  hypothesis_states: Array<{ snapshot_id: string; status: string; outcome: string; supporting_evidence: number; contradicting_evidence: number; neutral_evidence: number }>
  primary_run_comparison: RunComparisonReport
  comparison_disclosure: string
}

export type IntegrityCheckCode = 'POST_HOLDOUT_MODIFICATION' | 'FUTURE_DATA_LEAK' | 'DATASET_SILENT_CHANGE' | 'STRATEGY_SEMANTIC_MISMATCH' | 'MISSING_LINEAGE' | 'MISSING_REVISION'
export type IntegritySeverity = 'PASS' | 'WARNING' | 'VIOLATION'
export interface IntegrityFinding { code: IntegrityCheckCode; severity: IntegritySeverity; subject: string; reason: string; evidence: string[] }
export interface HypothesisIntegrityReport {
  report_version: '1.0'
  hypothesis_id: string
  family_id: string
  title: string
  revision: number
  lifecycle_status: string
  checked_at: string
  findings: IntegrityFinding[]
  violation_count: number
  warning_count: number
  overall_status: IntegritySeverity
  disclosure: string
}
export interface HypothesisIntegritySummary { hypothesis_id: string; family_id: string; title: string; revision: number; lifecycle_status: string; overall_status: IntegritySeverity; violation_count: number; warning_count: number }
export interface WorkspaceIntegrityReport {
  report_version: '1.0'
  generated_at: string
  hypotheses: HypothesisIntegritySummary[]
  overall_status: IntegritySeverity
  total_violations: number
  total_warnings: number
  disclosure: string
}

export type WorkspaceStageKey = 'DATA' | 'FACTOR' | 'PORTFOLIO' | 'VALIDATION' | 'HYPOTHESIS' | 'STRATEGY' | 'RUN'
export type WorkspaceStageStatus = 'COMPLETE' | 'CURRENT' | 'BLOCKED'
export type WorkspaceAction = 'BUILD_CANDIDATE' | 'RUN_VALIDATION' | 'REVEAL_HOLDOUT' | 'CREATE_STRATEGY' | 'RUN_BACKTEST' | 'OPEN_RUN'
export interface WorkspaceNextAction { action: WorkspaceAction; label: string; requires_explicit_confirmation: boolean }
export interface WorkspaceStage { key: WorkspaceStageKey; status: WorkspaceStageStatus; summary: string; artifact_ids: string[] }
export interface WorkspaceFactor { research_id: string; factor_id: string; name: string; revealed_stage: string; revision: string }
export type WorkspaceLineageStatus = 'AVAILABLE' | 'MISSING'
export interface WorkspaceFactorRelationship { relationship_id: string; status: WorkspaceLineageStatus; name: string | null; stage: string | null; factor_research_ids: string[]; redundancy_count: number; cluster_count: number }
export interface WorkspaceWalkForward { walk_forward_id: string; status: WorkspaceLineageStatus; name: string | null; factor_research_id: string | null; factor_id: string | null; dataset_id: string | null; window_count: number; positive_ic_window_ratio: number | null }
export interface WorkspacePortfolio { portfolio_research_id: string; name: string; revealed_stage: string; combination: string; rebalance: string; net_return: number; turnover: number }
export interface WorkspaceStrategy { strategy_id: string; source_fingerprint: string }
export interface WorkspaceDriftReport { drift_report_id: string; baseline_id: string; observed_id: string; comparability: string; overall_status: string; first_drift_at: string | null; first_drift_dimension: string | null; created_at: string }
export interface WorkspaceRun { run_id: string; trace_id: string | null; status: string; created_at: string; run_fingerprint: string; total_return: number | null; max_drawdown: number | null }
export interface ResearchWorkspaceSummary { idea_id: string; family_id: string; title: string; revision: number; lifecycle_status: string; outcome: string; dataset_id: string; factor_count: number; completed_stage_count: number; total_stage_count: 7; integrity_status: IntegritySeverity; next_action: WorkspaceNextAction; updated_at: string }
export interface ResearchWorkspace {
  workspace_version: '1.0'
  idea_id: string
  family_id: string
  parent_idea_id: string | null
  title: string
  description: string
  revision: number
  lifecycle_status: string
  outcome: string
  expected_relationship: string
  holding_horizon: string
  rebalance_idea: string
  risk_assumptions: string[]
  created_at: string
  updated_at: string
  dataset_id: string
  dataset_name: string | null
  dataset_revision: string
  dataset_period: [string, string] | null
  factors: WorkspaceFactor[]
  relationships: WorkspaceFactorRelationship[]
  walk_forward: WorkspaceWalkForward[]
  portfolio: WorkspacePortfolio | null
  strategy: WorkspaceStrategy | null
  runs: WorkspaceRun[]
  snapshot_ids: string[]
  drift_reports?: WorkspaceDriftReport[]
  integrity_status: IntegritySeverity
  integrity_violations: number
  integrity_warnings: number
  stages: WorkspaceStage[]
  next_action: WorkspaceNextAction
  disclosure: string
}
