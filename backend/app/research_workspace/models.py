from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.research_integrity.models import IntegrityStatus

WorkspaceStageKey = Literal[
    "DATA",
    "FACTOR",
    "PORTFOLIO",
    "VALIDATION",
    "HYPOTHESIS",
    "STRATEGY",
    "RUN",
]
WorkspaceStageStatus = Literal["COMPLETE", "CURRENT", "BLOCKED"]
WorkspaceLineageStatus = Literal["AVAILABLE", "MISSING"]
WorkspaceAction = Literal[
    "BUILD_CANDIDATE",
    "RUN_VALIDATION",
    "REVEAL_HOLDOUT",
    "CREATE_STRATEGY",
    "RUN_BACKTEST",
    "OPEN_RUN",
]

WORKSPACE_DISCLOSURE = (
    "The unified Research Workspace is a read model over the existing Dataset, Factor, "
    "Factor Relationship, Walk-Forward, Portfolio, Hypothesis, Native Strategy, Run, Trace, "
    "Snapshot, and Integrity records. It does not duplicate quantitative engines, mutate "
    "evidence, reveal Holdout automatically, optimize parameters, or select a winner."
)


class WorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Research Workspace timestamps must be timezone-aware")
    return value


class WorkspaceNextAction(WorkspaceModel):
    action: WorkspaceAction
    label: str
    requires_explicit_confirmation: bool = False


class WorkspaceStage(WorkspaceModel):
    key: WorkspaceStageKey
    status: WorkspaceStageStatus
    summary: str
    artifact_ids: tuple[str, ...] = ()


class WorkspaceFactor(WorkspaceModel):
    research_id: str
    factor_id: str
    name: str
    revealed_stage: str
    revision: str


class WorkspaceFactorRelationship(WorkspaceModel):
    relationship_id: str
    status: WorkspaceLineageStatus
    name: str | None = None
    stage: str | None = None
    factor_research_ids: tuple[str, ...] = ()
    redundancy_count: int = Field(default=0, ge=0)
    cluster_count: int = Field(default=0, ge=0)


class WorkspaceWalkForward(WorkspaceModel):
    walk_forward_id: str
    status: WorkspaceLineageStatus
    name: str | None = None
    factor_research_id: str | None = None
    factor_id: str | None = None
    dataset_id: str | None = None
    window_count: int = Field(default=0, ge=0)
    positive_ic_window_ratio: float | None = None


class WorkspacePortfolio(WorkspaceModel):
    portfolio_research_id: str
    name: str
    revealed_stage: str
    combination: str
    rebalance: str
    net_return: float
    turnover: float


class WorkspaceStrategy(WorkspaceModel):
    strategy_id: str
    source_fingerprint: str


class WorkspaceRun(WorkspaceModel):
    run_id: str
    trace_id: str | None
    status: str
    created_at: datetime
    run_fingerprint: str
    total_return: float | None
    max_drawdown: float | None

    _aware_created = field_validator("created_at")(_aware)


class ResearchWorkspaceSummary(WorkspaceModel):
    idea_id: str
    family_id: str
    title: str
    revision: int = Field(ge=1)
    lifecycle_status: str
    outcome: str
    dataset_id: str
    factor_count: int = Field(ge=0)
    completed_stage_count: int = Field(ge=0, le=7)
    total_stage_count: Literal[7] = 7
    integrity_status: IntegrityStatus
    next_action: WorkspaceNextAction
    updated_at: datetime

    _aware_updated = field_validator("updated_at")(_aware)


class ResearchWorkspace(WorkspaceModel):
    workspace_version: Literal["1.0"] = "1.0"
    idea_id: str
    family_id: str
    parent_idea_id: str | None
    title: str
    description: str
    revision: int = Field(ge=1)
    lifecycle_status: str
    outcome: str
    expected_relationship: str
    holding_horizon: str
    rebalance_idea: str
    risk_assumptions: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    dataset_id: str
    dataset_name: str | None
    dataset_revision: str
    dataset_period: tuple[datetime, datetime] | None
    factors: tuple[WorkspaceFactor, ...]
    relationships: tuple[WorkspaceFactorRelationship, ...]
    walk_forward: tuple[WorkspaceWalkForward, ...]
    portfolio: WorkspacePortfolio | None
    strategy: WorkspaceStrategy | None
    runs: tuple[WorkspaceRun, ...]
    snapshot_ids: tuple[str, ...]
    integrity_status: IntegrityStatus
    integrity_violations: int = Field(ge=0)
    integrity_warnings: int = Field(ge=0)
    stages: tuple[WorkspaceStage, ...]
    next_action: WorkspaceNextAction
    disclosure: str = WORKSPACE_DISCLOSURE

    _aware_times = field_validator("created_at", "updated_at")(_aware)
