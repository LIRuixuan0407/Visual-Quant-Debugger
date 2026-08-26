from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.fundamentals.models import FundamentalFieldSnapshot, FundamentalSnapshot
from app.trace.models import DataDependency

ResearchStage = Literal["RESEARCH", "VALIDATION", "HOLDOUT"]
UniverseMode = Literal["FIXED_UNIVERSE", "STATIC", "POINT_IN_TIME"]
FactorCategory = Literal["PRICE_VOLUME", "VALUE", "QUALITY", "GROWTH", "LEVERAGE", "MIXED"]
FactorDataSource = Literal["MARKET", "FUNDAMENTAL", "MIXED"]
FactorOrigin = Literal["BUILT_IN", "CUSTOM"]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Factor research timestamps must be timezone-aware")
    return value


class FactorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactorParameter(FactorModel):
    key: str
    label: str
    description: str
    default_value: int | float
    minimum: int | float
    maximum: int | float | None = None
    step: int | float = 1
    unit: str = ""


class FactorDefinition(FactorModel):
    factor_id: str
    name: str
    version: str = "1.0.0"
    formula: str
    description: str
    parameters: tuple[FactorParameter, ...]
    required_fields: tuple[str, ...]
    lookback: int
    availability: str = "close(t)"
    direction: Literal["HIGH", "LOW"] = "HIGH"
    category: FactorCategory = "PRICE_VOLUME"
    data_source: FactorDataSource = "MARKET"
    required_fundamental_fields: tuple[str, ...] = ()
    origin: FactorOrigin = "BUILT_IN"
    source_path: str | None = None
    source_fingerprint: str = ""


class FactorComponent(FactorModel):
    factor_id: str
    weight: float = Field(ge=-10, le=10)
    parameters: dict[str, int | float] = Field(default_factory=dict)


class ResearchPeriod(FactorModel):
    start: datetime
    end: datetime

    _aware_times = field_validator("start", "end")(_aware)

    @model_validator(mode="after")
    def valid_order(self) -> ResearchPeriod:
        if self.end < self.start:
            raise ValueError("Period end cannot precede start")
        return self


class ResearchPeriods(FactorModel):
    research: ResearchPeriod
    validation: ResearchPeriod
    holdout: ResearchPeriod

    @model_validator(mode="after")
    def non_overlapping(self) -> ResearchPeriods:
        if not (
            self.research.end < self.validation.start and self.validation.end < self.holdout.start
        ):
            raise ValueError("RESEARCH, VALIDATION, and HOLDOUT periods must be chronological")
        return self


class CreateFactorResearch(FactorModel):
    name: str = Field(min_length=1, max_length=200)
    dataset_id: str
    factor_id: str
    parameters: dict[str, int | float] = Field(default_factory=dict)
    periods: ResearchPeriods
    universe: tuple[str, ...] = ()
    universe_id: str | None = None
    fundamental_dataset_id: str | None = None
    components: tuple[FactorComponent, ...] = ()

    @model_validator(mode="after")
    def mixed_contract(self) -> CreateFactorResearch:
        if self.factor_id == "mixed" and len(self.components) < 2:
            raise ValueError("Mixed factor research requires at least two explicit components")
        if self.factor_id != "mixed" and self.components:
            raise ValueError("Factor components are only accepted for the mixed factor")
        return self


class FactorTimelinePoint(FactorModel):
    timestamp: datetime
    ic: float | None
    rank_ic: float | None
    quantile_returns: tuple[float | None, ...]
    long_short_spread: float | None

    _aware_timestamp = field_validator("timestamp")(_aware)


class HorizonEvaluation(FactorModel):
    horizon: Literal[1, 5, 20]
    observation_count: int
    cross_section_count: int
    ic: float | None
    rank_ic: float | None
    ic_stability: float | None
    rank_ic_stability: float | None
    quantile_returns: tuple[float | None, ...]
    long_short_spread: float | None
    turnover: float | None
    coverage: float
    monotonic: bool
    timeline: tuple[FactorTimelinePoint, ...]


class PeriodEvaluation(FactorModel):
    stage: ResearchStage
    period: ResearchPeriod
    horizons: tuple[HorizonEvaluation, ...]


class FactorObservation(FactorModel):
    symbol: str
    timestamp: datetime
    factor_id: str
    value: float
    window_start: datetime
    window_end: datetime
    available_at: datetime
    future_returns: dict[int, float | None]
    future_return_timestamps: dict[int, datetime | None] = Field(default_factory=dict)
    dependencies: tuple[DataDependency, ...]
    fundamental_inputs: tuple[FundamentalFieldSnapshot, ...] = ()
    future_data_used: Literal[False] = False

    _aware_times = field_validator("timestamp", "window_start", "window_end", "available_at")(
        _aware
    )

    @field_validator("future_return_timestamps")
    @classmethod
    def aware_future_return_timestamps(
        cls, value: dict[int, datetime | None]
    ) -> dict[int, datetime | None]:
        return {
            horizon: None if timestamp is None else _aware(timestamp)
            for horizon, timestamp in value.items()
        }


class FactorInspection(FactorModel):
    research_id: str
    observation: FactorObservation
    formula: str
    parameter_values: dict[str, int | float]
    point_in_time_status: Literal["SAFE"] = "SAFE"
    restatement_status: Literal["SAFE", "NOT_RESTATEMENT_SAFE"] = "SAFE"


class FactorStrategyArtifact(FactorModel):
    strategy_id: str
    research_id: str
    dataset_id: str
    source_fingerprint: str
    created_at: datetime

    _aware_created = field_validator("created_at")(_aware)


class CreateFactorStrategy(FactorModel):
    long_percent: float = Field(default=10.0, gt=0, le=100)
    rebalance_bars: int = Field(default=5, ge=1, le=63)
    gross_notional: float = Field(default=20_000.0, gt=0)
    max_volatility: float | None = Field(default=None, gt=0)


class FactorResearchRecord(FactorModel):
    research_id: str
    name: str
    created_at: datetime
    dataset_id: str
    dataset_name: str
    dataset_revision: str
    factor: FactorDefinition
    parameters: dict[str, int | float]
    components: tuple[FactorComponent, ...] = ()
    universe: tuple[str, ...]
    universe_id: str | None = None
    universe_mode: UniverseMode = "FIXED_UNIVERSE"
    survivorship_bias_free: bool = False
    survivorship_warning: str = (
        "Current constituents are held fixed through history; this is not survivorship-bias free."
    )
    periods: ResearchPeriods
    revealed_stage: ResearchStage = "RESEARCH"
    evaluations: tuple[PeriodEvaluation, ...]
    factor_observation_count: int
    sample_observations: tuple[FactorObservation, ...]
    fundamental_dataset_id: str | None = None
    fundamental_provider: str | None = None
    restatement_safe: bool = True
    restatement_warning: str | None = None
    strategy: FactorStrategyArtifact | None = None

    _aware_created = field_validator("created_at")(_aware)


class FactorResearchSummary(FactorModel):
    research_id: str
    name: str
    created_at: datetime
    dataset_id: str
    factor_id: str
    symbols: int
    revealed_stage: ResearchStage
    research_ic: float | None
    research_rank_ic: float | None
    factor_category: FactorCategory = "PRICE_VOLUME"
    data_source: FactorDataSource = "MARKET"
    factor_origin: FactorOrigin = "BUILT_IN"
    direction: Literal["HIGH", "LOW"] = "HIGH"

    _aware_created = field_validator("created_at")(_aware)


class HistoricalSecurityRow(FactorModel):
    symbol: str
    company: str
    close: float
    return_1d: float | None
    volume: float | None
    volatility_20d: float | None
    high_low_range: float | None
    average_volume_20d: float | None


class HistoricalTrendPoint(FactorModel):
    timestamp: datetime
    close: float
    volume: float | None

    _aware_timestamp = field_validator("timestamp")(_aware)


class HistoricalMarketView(FactorModel):
    dataset_id: str
    dataset_revision: str
    source: str
    requested_as_of: datetime
    as_of: datetime
    universe_id: str | None = None
    universe_source: str | None = None
    universe_mode: UniverseMode = "FIXED_UNIVERSE"
    survivorship_bias_free: bool = False
    universe_disclosure: str | None = None
    cross_section: tuple[HistoricalSecurityRow, ...]
    selected_symbol: str
    trend: tuple[HistoricalTrendPoint, ...]
    fundamentals: FundamentalSnapshot | None = None

    _aware_times = field_validator("requested_as_of", "as_of")(_aware)
