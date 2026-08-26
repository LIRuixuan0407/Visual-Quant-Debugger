from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.factors.models import ResearchPeriod

Horizon = Literal[1, 5, 20]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Walk-Forward timestamps must be timezone-aware")
    return value


class WalkForwardModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WalkForwardConfig(WalkForwardModel):
    research_months: int = Field(default=12, ge=1, le=120)
    validation_months: int = Field(default=3, ge=1, le=60)
    forward_months: int = Field(default=3, ge=1, le=60)
    step_months: int = Field(default=3, ge=1, le=60)
    start: datetime | None = None
    end: datetime | None = None

    @field_validator("start", "end")
    @classmethod
    def aware_when_present(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)

    @model_validator(mode="after")
    def chronological(self) -> WalkForwardConfig:
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("Walk-Forward end must be after start")
        return self


class CreateWalkForwardResearch(WalkForwardModel):
    name: str = Field(min_length=1, max_length=200)
    factor_research_id: str = Field(min_length=1)
    strategy_id: str | None = None
    config: WalkForwardConfig = WalkForwardConfig()
    horizon: Horizon = 20
    initial_cash: float = Field(default=100_000.0, gt=0)
    fee_bps: float = Field(default=5.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)
    strategy_parameters: dict[str, int | float] = Field(default_factory=dict)


class WalkForwardWindowDefinition(WalkForwardModel):
    index: int = Field(ge=1)
    research: ResearchPeriod
    validation: ResearchPeriod
    forward: ResearchPeriod


class FactorWindowMetrics(WalkForwardModel):
    observation_count: int = Field(ge=0)
    cross_section_count: int = Field(ge=0)
    ic: float | None
    rank_ic: float | None
    quantile_returns: tuple[float | None, ...]
    spread: float | None
    coverage: float = Field(ge=0, le=1)
    turnover: float | None
    monotonic: bool


class StrategyWindowMetrics(WalkForwardModel):
    total_return: float
    sharpe: float
    max_drawdown: float
    trades: int = Field(ge=0)
    fees: float = Field(ge=0)
    slippage: float = Field(ge=0)
    net_costs: float = Field(ge=0)


class WalkForwardWindowResult(WalkForwardModel):
    definition: WalkForwardWindowDefinition
    research: FactorWindowMetrics
    validation: FactorWindowMetrics
    forward: FactorWindowMetrics
    forward_strategy: StrategyWindowMetrics | None = None


class MetricDistribution(WalkForwardModel):
    count: int = Field(ge=0)
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None


class WalkForwardStability(WalkForwardModel):
    positive_ic_window_ratio: float = Field(ge=0, le=1)
    rank_ic_distribution: MetricDistribution
    factor_sign_consistency: float = Field(ge=0, le=1)
    quantile_monotonicity_stability: float = Field(ge=0, le=1)
    turnover_stability: float = Field(ge=0, le=1)
    strategy_return_distribution: MetricDistribution | None = None


class FirstDegradation(WalkForwardModel):
    window_index: int = Field(ge=1)
    timestamp: datetime
    reasons: tuple[str, ...]
    factor_research_id: str
    strategy_id: str | None
    run_id: str | None
    historical_market_path: str
    factor_lab_path: str
    replay_path: str | None

    _aware_timestamp = field_validator("timestamp")(_aware)


class WalkForwardResearchRecord(WalkForwardModel):
    walk_forward_id: str
    name: str
    created_at: datetime
    factor_research_id: str
    factor_id: str
    factor_revision: str
    strategy_id: str | None
    strategy_revision: str | None
    dataset_id: str
    dataset_fingerprint: str
    config: WalkForwardConfig
    horizon: Horizon
    initial_cash: float
    fee_bps: float
    slippage_bps: float
    windows: tuple[WalkForwardWindowResult, ...]
    stability: WalkForwardStability
    first_degradation: FirstDegradation | None
    run_id: str | None = None
    trace_id: str | None = None

    _aware_created = field_validator("created_at")(_aware)
