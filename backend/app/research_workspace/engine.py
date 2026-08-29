from __future__ import annotations

from datetime import datetime

from app.datasets import DatasetRegistry
from app.discovery.models import ResearchHypothesis
from app.discovery.repository import HypothesisRepository
from app.factor_relationships.repository import FactorRelationshipRepository
from app.factors.repository import FactorResearchRepository
from app.portfolio_lab.models import PortfolioResearchRecord
from app.portfolio_lab.repository import PortfolioResearchRepository
from app.research_integrity import ResearchIntegrityEngine
from app.research_ledger import ResearchLedgerRepository
from app.research_snapshots import ResearchSnapshotRepository
from app.runs import ArtifactIntegrityError, RunNotFoundError, RunRepository
from app.sdk.registry import StrategyRegistry
from app.strategy_drift.repository import StrategyDriftRepository
from app.walk_forward.repository import WalkForwardRepository

from .models import (
    ResearchWorkspace,
    ResearchWorkspaceSummary,
    WorkspaceDriftReport,
    WorkspaceFactor,
    WorkspaceFactorRelationship,
    WorkspaceNextAction,
    WorkspacePortfolio,
    WorkspaceRun,
    WorkspaceStage,
    WorkspaceStageStatus,
    WorkspaceStrategy,
    WorkspaceWalkForward,
)

_STATUS_RANK = {
    "DRAFT": 0,
    "RESEARCHED": 1,
    "VALIDATED": 2,
    "HOLDOUT_REVEALED": 3,
    "STRATEGY_CREATED": 4,
}


class ResearchWorkspaceEngine:
    def __init__(
        self,
        datasets: DatasetRegistry,
        factors: FactorResearchRepository,
        relationships: FactorRelationshipRepository,
        walk_forward: WalkForwardRepository,
        hypotheses: HypothesisRepository,
        portfolios: PortfolioResearchRepository,
        strategies: StrategyRegistry,
        runs: RunRepository,
        snapshots: ResearchSnapshotRepository,
        integrity: ResearchIntegrityEngine,
        ledger: ResearchLedgerRepository,
        drift_reports: StrategyDriftRepository | None = None,
    ) -> None:
        self.datasets = datasets
        self.factors = factors
        self.relationships = relationships
        self.walk_forward = walk_forward
        self.hypotheses = hypotheses
        self.portfolios = portfolios
        self.strategies = strategies
        self.runs = runs
        self.snapshots = snapshots
        self.integrity = integrity
        self.ledger = ledger
        self.drift_reports = drift_reports or StrategyDriftRepository(datasets.workspace_root)

    @staticmethod
    def _next_action(record: ResearchHypothesis) -> WorkspaceNextAction:
        if record.lineage.portfolio_research_id is None:
            return WorkspaceNextAction(action="BUILD_CANDIDATE", label="Create candidate Portfolio")
        if record.status == "RESEARCHED":
            return WorkspaceNextAction(action="RUN_VALIDATION", label="Run Validation")
        if record.status == "VALIDATED":
            return WorkspaceNextAction(
                action="REVEAL_HOLDOUT",
                label="Reveal Holdout",
                requires_explicit_confirmation=True,
            )
        if record.status == "HOLDOUT_REVEALED":
            return WorkspaceNextAction(action="CREATE_STRATEGY", label="Create Native Strategy")
        if record.lineage.strategy_id is not None and not record.lineage.run_ids:
            return WorkspaceNextAction(action="RUN_BACKTEST", label="Run Backtest")
        return WorkspaceNextAction(action="OPEN_RUN", label="Open latest Run")

    @staticmethod
    def _stage_status(complete: bool, current: bool) -> WorkspaceStageStatus:
        return "COMPLETE" if complete else "CURRENT" if current else "BLOCKED"

    def _stages(
        self,
        record: ResearchHypothesis,
        *,
        dataset_exists: bool,
        factors: tuple[WorkspaceFactor, ...],
        portfolio: PortfolioResearchRecord | None,
        strategy: WorkspaceStrategy | None,
        runs: tuple[WorkspaceRun, ...],
    ) -> tuple[WorkspaceStage, ...]:
        all_factors = len(factors) == len(record.factor_research_ids) and bool(factors)
        portfolio_exists = portfolio is not None
        rank = _STATUS_RANK[record.status]
        validation_complete = rank >= _STATUS_RANK["VALIDATED"]
        hypothesis_complete = rank >= _STATUS_RANK["HOLDOUT_REVEALED"]
        strategy_exists = strategy is not None
        return (
            WorkspaceStage(
                key="DATA",
                status=self._stage_status(dataset_exists, not dataset_exists),
                summary=(
                    "Dataset revision is linked and available."
                    if dataset_exists
                    else "The linked Dataset is missing."
                ),
                artifact_ids=(record.dataset_id,),
            ),
            WorkspaceStage(
                key="FACTOR",
                status=self._stage_status(all_factors, dataset_exists and not all_factors),
                summary=(
                    f"{len(factors)} Factor research revisions are linked."
                    if all_factors
                    else "One or more Factor research revisions are missing."
                ),
                artifact_ids=tuple(item.research_id for item in factors),
            ),
            WorkspaceStage(
                key="PORTFOLIO",
                status=self._stage_status(portfolio_exists, all_factors and not portfolio_exists),
                summary=(
                    "Candidate Portfolio is recorded."
                    if portfolio_exists
                    else "Create the deterministic candidate Portfolio from this Idea."
                ),
                artifact_ids=() if portfolio is None else (portfolio.portfolio_research_id,),
            ),
            WorkspaceStage(
                key="VALIDATION",
                status=self._stage_status(
                    validation_complete,
                    portfolio_exists and not validation_complete,
                ),
                summary=(
                    "Validation evidence is recorded."
                    if validation_complete
                    else "Validation remains pending."
                ),
                artifact_ids=() if portfolio is None else (portfolio.portfolio_research_id,),
            ),
            WorkspaceStage(
                key="HYPOTHESIS",
                status=self._stage_status(
                    hypothesis_complete,
                    validation_complete and not hypothesis_complete,
                ),
                summary=(
                    "Hypothesis evidence includes the explicitly revealed Holdout."
                    if hypothesis_complete
                    else (
                        "The Idea is recorded; its evidence state advances only by explicit "
                        "actions."
                    )
                ),
                artifact_ids=(record.hypothesis_id,),
            ),
            WorkspaceStage(
                key="STRATEGY",
                status=self._stage_status(
                    strategy_exists,
                    hypothesis_complete and not strategy_exists,
                ),
                summary=(
                    "Native Strategy revision is linked."
                    if strategy_exists
                    else "Native Strategy creation remains pending."
                ),
                artifact_ids=() if strategy is None else (strategy.strategy_id,),
            ),
            WorkspaceStage(
                key="RUN",
                status=self._stage_status(bool(runs), strategy_exists and not runs),
                summary=(
                    f"{len(runs)} immutable Run / Trace pairs are linked."
                    if runs
                    else "Run the Native Strategy through the existing Execution Engine."
                ),
                artifact_ids=tuple(item.run_id for item in runs),
            ),
        )

    def _updated_at(self, record: ResearchHypothesis) -> datetime:
        timestamps = [
            item.created_at
            for item in self.ledger.list()
            if item.kind == "HYPOTHESIS" and item.artifact_id == record.hypothesis_id
        ]
        return max(timestamps, default=record.created_at)

    def get(
        self,
        hypothesis_id: str,
        *,
        snapshot_ids: tuple[str, ...] | None = None,
    ) -> ResearchWorkspace:
        record = self.hypotheses.get(hypothesis_id)
        if record is None:
            raise KeyError(hypothesis_id)
        dataset = self.datasets.get(record.dataset_id)
        factor_rows = tuple(
            factor
            for research_id in record.factor_research_ids
            if (factor := self.factors.get(research_id)) is not None
        )
        factors = tuple(
            WorkspaceFactor(
                research_id=item.research_id,
                factor_id=item.factor.factor_id,
                name=item.name,
                revealed_stage=item.revealed_stage,
                revision=item.factor.source_fingerprint or item.factor.version,
            )
            for item in factor_rows
        )
        relationships = tuple(
            WorkspaceFactorRelationship(
                relationship_id=relationship_id,
                status="MISSING" if item is None else "AVAILABLE",
                name=None if item is None else item.name,
                stage=None if item is None else item.stage,
                factor_research_ids=() if item is None else item.factor_research_ids,
                redundancy_count=0 if item is None else len(item.redundancy),
                cluster_count=0 if item is None else len(item.clusters),
            )
            for relationship_id in record.lineage.relationship_ids
            for item in (self.relationships.get(relationship_id),)
        )
        walk_forward = tuple(
            WorkspaceWalkForward(
                walk_forward_id=walk_forward_id,
                status="MISSING" if item is None else "AVAILABLE",
                name=None if item is None else item.name,
                factor_research_id=None if item is None else item.factor_research_id,
                factor_id=None if item is None else item.factor_id,
                dataset_id=None if item is None else item.dataset_id,
                window_count=0 if item is None else len(item.windows),
                positive_ic_window_ratio=(
                    None if item is None else item.stability.positive_ic_window_ratio
                ),
            )
            for walk_forward_id in record.lineage.walk_forward_ids
            for item in (self.walk_forward.get(walk_forward_id),)
        )
        portfolio_record = (
            None
            if record.lineage.portfolio_research_id is None
            else self.portfolios.get(record.lineage.portfolio_research_id)
        )
        portfolio = (
            None
            if portfolio_record is None
            else WorkspacePortfolio(
                portfolio_research_id=portfolio_record.portfolio_research_id,
                name=portfolio_record.name,
                revealed_stage=portfolio_record.revealed_stage,
                combination=portfolio_record.combination,
                rebalance=portfolio_record.rebalance,
                net_return=portfolio_record.stages[-1].cost_preview.net_return,
                turnover=portfolio_record.stages[-1].cost_preview.turnover,
            )
        )
        strategy = None
        if record.lineage.strategy_id is not None:
            registration = self.strategies.get_registration(record.lineage.strategy_id)
            if registration is not None:
                strategy = WorkspaceStrategy(
                    strategy_id=registration.strategy_id,
                    source_fingerprint=registration.source_fingerprint,
                )
        run_rows: list[WorkspaceRun] = []
        for run_id in record.lineage.run_ids:
            try:
                manifest = self.runs.get_manifest(run_id)
            except (RunNotFoundError, ArtifactIntegrityError):
                continue
            run_rows.append(
                WorkspaceRun(
                    run_id=manifest.run_id,
                    trace_id=manifest.trace_id,
                    status=manifest.status,
                    created_at=manifest.created_at,
                    run_fingerprint=manifest.run_fingerprint,
                    total_return=(
                        None if manifest.metrics is None else manifest.metrics.total_return
                    ),
                    max_drawdown=(
                        None if manifest.metrics is None else manifest.metrics.max_drawdown
                    ),
                )
            )
        runs = tuple(run_rows)
        if snapshot_ids is None:
            snapshot_ids = tuple(
                item.snapshot_id
                for item in self.snapshots.list()
                if item.hypothesis_id == record.hypothesis_id
            )
        explicit_sources = {*record.lineage.run_ids, *snapshot_ids}
        drift_reports = tuple(
            WorkspaceDriftReport(
                drift_report_id=item.drift_report_id,
                baseline_id=item.baseline_id,
                observed_id=item.observed_id,
                comparability=item.comparability,
                overall_status=item.overall_status,
                first_drift_at=item.first_drift_at,
                first_drift_dimension=item.first_drift_dimension,
                created_at=item.created_at,
            )
            for item in self.drift_reports.list()
            if item.baseline_id in explicit_sources or item.observed_id in explicit_sources
        )
        integrity = self.integrity.audit(record.hypothesis_id)
        stages = self._stages(
            record,
            dataset_exists=dataset is not None,
            factors=factors,
            portfolio=portfolio_record,
            strategy=strategy,
            runs=runs,
        )
        return ResearchWorkspace(
            idea_id=record.hypothesis_id,
            family_id=record.family_id,
            parent_idea_id=record.parent_hypothesis_id,
            title=record.title,
            description=record.description,
            revision=record.revision,
            lifecycle_status=record.status,
            outcome=record.outcome,
            expected_relationship=record.expected_relationship,
            holding_horizon=record.holding_horizon,
            rebalance_idea=record.rebalance_idea,
            risk_assumptions=record.risk_assumptions,
            created_at=record.created_at,
            updated_at=self._updated_at(record),
            dataset_id=record.dataset_id,
            dataset_name=None if dataset is None else dataset.name,
            dataset_revision=record.dataset_fingerprint,
            dataset_period=(dataset.start_time, dataset.end_time) if dataset is not None else None,
            factors=factors,
            relationships=relationships,
            walk_forward=walk_forward,
            portfolio=portfolio,
            strategy=strategy,
            runs=runs,
            snapshot_ids=snapshot_ids,
            drift_reports=drift_reports,
            integrity_status=integrity.overall_status,
            integrity_violations=integrity.violation_count,
            integrity_warnings=integrity.warning_count,
            stages=stages,
            next_action=self._next_action(record),
        )

    def list(self) -> tuple[ResearchWorkspaceSummary, ...]:
        snapshots_by_hypothesis: dict[str, list[str]] = {}
        for snapshot in self.snapshots.list():
            snapshots_by_hypothesis.setdefault(snapshot.hypothesis_id, []).append(
                snapshot.snapshot_id
            )
        workspaces = tuple(
            self.get(
                item.hypothesis_id,
                snapshot_ids=tuple(snapshots_by_hypothesis.get(item.hypothesis_id, ())),
            )
            for item in self.hypotheses.list()
        )
        return tuple(
            ResearchWorkspaceSummary(
                idea_id=item.idea_id,
                family_id=item.family_id,
                title=item.title,
                revision=item.revision,
                lifecycle_status=item.lifecycle_status,
                outcome=item.outcome,
                dataset_id=item.dataset_id,
                factor_count=len(item.factors),
                completed_stage_count=sum(stage.status == "COMPLETE" for stage in item.stages),
                integrity_status=item.integrity_status,
                next_action=item.next_action,
                updated_at=item.updated_at,
            )
            for item in sorted(workspaces, key=lambda value: value.updated_at, reverse=True)
        )
