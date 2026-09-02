from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.factors.models import ResearchPeriod, ResearchStage

CorrelationSemantic = Literal["FACTOR_VALUES", "FACTOR_RANKS", "FACTOR_RETURNS"]
RedundancyStatus = Literal["HIGH_REDUNDANCY", "RELATED", "LOW_REDUNDANCY"]
Horizon = Literal[1, 5, 20]
PcaStatus = Literal["AVAILABLE", "INSUFFICIENT_DATA"]
PcaComponentName = Literal["PC1", "PC2", "PC3"]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Factor relationship timestamps must be timezone-aware")
    return value


class RelationshipModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreateFactorRelationship(RelationshipModel):
    name: str = Field(min_length=1, max_length=200)
    factor_research_ids: tuple[str, ...] = Field(min_length=2, max_length=12)
    stage: ResearchStage = "RESEARCH"
    horizon: Horizon = 20
    rolling_window: int = Field(default=60, ge=2, le=504)
    top_percent: float = Field(default=20.0, gt=0, le=50)
    redundancy_threshold: float = Field(default=0.75, ge=0, le=1)
    overlap_threshold: float = Field(default=0.60, ge=0, le=1)

    @model_validator(mode="after")
    def unique_factors(self) -> CreateFactorRelationship:
        if len(self.factor_research_ids) != len(set(self.factor_research_ids)):
            raise ValueError("Factor relationship studies require unique Factor research ids")
        return self


class CorrelationCell(RelationshipModel):
    left_research_id: str
    right_research_id: str
    semantic: CorrelationSemantic
    pearson: float | None
    spearman: float | None
    observations: int = Field(ge=0)


class RollingCorrelationPoint(RelationshipModel):
    timestamp: datetime
    pearson: float | None
    spearman: float | None
    observations: int = Field(ge=0)

    _aware_timestamp = field_validator("timestamp")(_aware)


class RollingCorrelationSeries(RelationshipModel):
    left_research_id: str
    right_research_id: str
    semantic: CorrelationSemantic
    window: int
    points: tuple[RollingCorrelationPoint, ...]


class ExposureOverlapPoint(RelationshipModel):
    timestamp: datetime
    intersection_count: int = Field(ge=0)
    union_count: int = Field(ge=0)
    overlap_percent: float = Field(ge=0, le=1)
    jaccard: float = Field(ge=0, le=1)

    _aware_timestamp = field_validator("timestamp")(_aware)


class ExposureOverlap(RelationshipModel):
    left_research_id: str
    right_research_id: str
    top_percent: float
    mean_intersection_count: float = Field(ge=0)
    mean_union_count: float = Field(ge=0)
    mean_overlap: float = Field(ge=0, le=1)
    mean_jaccard: float = Field(ge=0, le=1)
    timestamps: int = Field(ge=0)
    points: tuple[ExposureOverlapPoint, ...]


class RedundancyAssessment(RelationshipModel):
    left_research_id: str
    right_research_id: str
    status: RedundancyStatus
    rank_correlation: float | None
    top_quantile_overlap: float | None
    reason: str


class IncrementalInformation(RelationshipModel):
    base_research_id: str
    added_research_id: str
    normalization: Literal["DIRECTION_ADJUSTED_PERCENTILE_RANK_AVERAGE"] = (
        "DIRECTION_ADJUSTED_PERCENTILE_RANK_AVERAGE"
    )
    base_rank_ic: float | None
    composite_rank_ic: float | None
    rank_ic_delta: float | None
    base_spread: float | None
    composite_spread: float | None
    spread_delta: float | None
    base_coverage: float
    composite_coverage: float
    coverage_delta: float
    base_turnover: float | None
    composite_turnover: float | None
    turnover_delta: float | None
    base_portfolio_return: float | None
    composite_portfolio_return: float | None
    portfolio_effect: float | None


class FactorCluster(RelationshipModel):
    cluster_id: str
    factor_research_ids: tuple[str, ...]
    rule: str


class PcaFactorLoading(RelationshipModel):
    factor_research_id: str
    factor_name: str
    loading: float


class PrincipalComponent(RelationshipModel):
    component: PcaComponentName
    eigenvalue: float = Field(ge=0.0)
    explained_variance: float = Field(ge=0.0, le=1.0)
    cumulative_explained_variance: float = Field(ge=0.0, le=1.0)
    loadings: tuple[PcaFactorLoading, ...]


class LatentFactorEvidence(RelationshipModel):
    component: PcaComponentName
    factor_research_ids: tuple[str, ...]
    minimum_absolute_loading: float = Field(ge=0.0)
    maximum_absolute_pairwise_return_correlation: float = Field(ge=0.0, le=1.0)
    reason: str


class PcaFactorStructure(RelationshipModel):
    status: PcaStatus
    verdict: str
    observations: int = Field(ge=0)
    standardization: str
    components: tuple[PrincipalComponent, ...]
    latent_factor_evidence: tuple[LatentFactorEvidence, ...]
    calculation_details: tuple[str, ...]
    boundary_disclosure: str


class FactorRelationshipRecord(RelationshipModel):
    relationship_id: str
    name: str
    created_at: datetime
    stage: ResearchStage
    period: ResearchPeriod
    horizon: Horizon
    rolling_window: int
    top_percent: float
    redundancy_threshold: float
    overlap_threshold: float
    dataset_id: str
    dataset_fingerprint: str
    universe: tuple[str, ...]
    factor_research_ids: tuple[str, ...]
    factor_ids: tuple[str, ...]
    factor_names: tuple[str, ...]
    factor_revisions: tuple[str, ...]
    value_correlations: tuple[CorrelationCell, ...]
    rank_correlations: tuple[CorrelationCell, ...]
    return_correlations: tuple[CorrelationCell, ...]
    rolling_correlations: tuple[RollingCorrelationSeries, ...]
    redundancy: tuple[RedundancyAssessment, ...]
    exposure_overlap: tuple[ExposureOverlap, ...]
    incremental_information: tuple[IncrementalInformation, ...]
    clusters: tuple[FactorCluster, ...]
    pca: PcaFactorStructure | None = None
    correlation_methodology: str
    incremental_disclosure: str
    crowding_disclosure: str

    _aware_created = field_validator("created_at")(_aware)
