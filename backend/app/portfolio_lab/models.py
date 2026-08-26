from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.factors.models import (
    FactorCategory,
    FactorDataSource,
    FactorOrigin,
    ResearchPeriod,
    ResearchStage,
)

CombinationMethod = Literal[
    "EQUAL_WEIGHT",
    "USER_DEFINED_WEIGHT",
    "RANK_AVERAGE",
    "Z_SCORE_COMPOSITE",
]
SelectionMethod = Literal["TOP_N", "TOP_PERCENT"]
WeightingMethod = Literal["EQUAL_WEIGHT", "SCORE_WEIGHTED"]
RebalanceRule = Literal["DAILY", "WEEKLY", "MONTHLY"]


class PortfolioModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PortfolioFactorRef(PortfolioModel):
    research_id: str
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    direction_override: Literal["HIGH", "LOW"] | None = None


class PortfolioFilters(PortfolioModel):
    minimum_liquidity: float | None = Field(default=None, ge=0)
    maximum_volatility: float | None = Field(default=None, gt=0)
    require_factor_availability: bool = True
    include_symbols: tuple[str, ...] = ()
    exclude_symbols: tuple[str, ...] = ()

    @field_validator("include_symbols", "exclude_symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple | set):
            raise ValueError("Portfolio symbol filters must be a list of ticker symbols")
        normalized = tuple(str(item).strip().upper() for item in value if str(item).strip())
        if len(normalized) != len(set(normalized)):
            raise ValueError("Portfolio symbol filters cannot contain duplicates")
        return normalized

    @model_validator(mode="after")
    def include_and_exclude_do_not_overlap(self) -> PortfolioFilters:
        overlap = set(self.include_symbols) & set(self.exclude_symbols)
        if overlap:
            raise ValueError(
                "Portfolio include/exclude filters overlap: " + ", ".join(sorted(overlap))
            )
        return self


class PortfolioConstruction(PortfolioModel):
    selection: SelectionMethod = "TOP_N"
    top_n: int = Field(default=5, ge=1)
    top_percent: float = Field(default=20.0, gt=0, le=100)
    weighting: WeightingMethod = "EQUAL_WEIGHT"
    max_single_position_weight: float = Field(default=1.0, gt=0, le=1.0)


class CreatePortfolioResearch(PortfolioModel):
    name: str = Field(min_length=1, max_length=200)
    factors: tuple[PortfolioFactorRef, ...] = Field(min_length=2)
    combination: CombinationMethod
    filters: PortfolioFilters = PortfolioFilters()
    construction: PortfolioConstruction = PortfolioConstruction()
    rebalance: RebalanceRule = "MONTHLY"
    gross_notional: float = Field(default=20_000.0, gt=0)
    initial_cash: float = Field(default=100_000.0, gt=0)
    fee_bps: float = Field(default=5.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)

    @model_validator(mode="after")
    def weights_are_explicit_and_legal(self) -> CreatePortfolioResearch:
        ids = [item.research_id for item in self.factors]
        if len(ids) != len(set(ids)):
            raise ValueError("Portfolio factors must be unique research records")
        if self.combination == "USER_DEFINED_WEIGHT":
            total = sum(item.weight for item in self.factors)
            if abs(total - 1.0) > 1e-6:
                raise ValueError("User-defined factor weights must sum to 1.0")
        return self


class FactorScoreEvidence(PortfolioModel):
    research_id: str
    factor_id: str
    factor_name: str
    direction: Literal["HIGH", "LOW"]
    available: bool
    raw_value: float | None
    rank: int | None
    universe_count: int
    normalized_score: float | None
    contribution: float


class PortfolioFactorCheck(PortfolioModel):
    research_id: str
    factor_id: str
    factor_name: str
    origin: FactorOrigin
    category: FactorCategory
    data_source: FactorDataSource
    direction: Literal["HIGH", "LOW"]
    effective_weight: float
    available_observations: int
    expected_observations: int
    missing_observations: int
    coverage: float = Field(ge=0.0, le=1.0)


class PortfolioPositionLineage(PortfolioModel):
    symbol: str
    selected: bool
    liquidity: float | None
    volatility: float | None
    filter_status: tuple[str, ...]
    factors: tuple[FactorScoreEvidence, ...]
    composite_score: float | None
    portfolio_rank: int | None
    target_weight: float


class PortfolioRebalanceSnapshot(PortfolioModel):
    timestamp: datetime
    stage: ResearchStage
    eligible_count: int
    selected_symbols: tuple[str, ...]
    positions: tuple[PortfolioPositionLineage, ...]

    @field_validator("timestamp")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Portfolio timestamps must be timezone-aware")
        return value


class TransactionCostPreview(PortfolioModel):
    gross_return: float
    fees: float
    slippage: float
    net_return: float
    turnover: float
    max_drawdown: float
    positions: int
    rebalance_count: int


class PortfolioStageResult(PortfolioModel):
    stage: ResearchStage
    period: ResearchPeriod
    factor_checks: tuple[PortfolioFactorCheck, ...]
    snapshots: tuple[PortfolioRebalanceSnapshot, ...]
    cost_preview: TransactionCostPreview


class PortfolioStrategyArtifact(PortfolioModel):
    strategy_id: str
    portfolio_research_id: str
    dataset_id: str
    source_fingerprint: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Strategy timestamp must be timezone-aware")
        return value


class PortfolioResearchRecord(PortfolioModel):
    portfolio_research_id: str
    name: str
    created_at: datetime
    dataset_id: str
    dataset_fingerprint: str
    universe: tuple[str, ...]
    factor_refs: tuple[PortfolioFactorRef, ...]
    factor_ids: tuple[str, ...]
    factor_names: tuple[str, ...]
    combination: CombinationMethod
    filters: PortfolioFilters
    construction: PortfolioConstruction
    rebalance: RebalanceRule
    gross_notional: float
    initial_cash: float
    fee_bps: float
    slippage_bps: float
    revealed_stage: ResearchStage = "RESEARCH"
    stages: tuple[PortfolioStageResult, ...]
    strategy: PortfolioStrategyArtifact | None = None

    @field_validator("created_at")
    @classmethod
    def created_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Portfolio research timestamp must be timezone-aware")
        return value


class PortfolioResearchSummary(PortfolioModel):
    portfolio_research_id: str
    name: str
    created_at: datetime
    dataset_id: str
    factor_count: int
    combination: CombinationMethod
    revealed_stage: ResearchStage
    net_return: float
    turnover: float

    @field_validator("created_at")
    @classmethod
    def aware_summary(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Portfolio summary timestamp must be timezone-aware")
        return value
