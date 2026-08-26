from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FundamentalPeriodType = Literal["ANNUAL", "QUARTERLY", "INSTANT"]
FundamentalStatus = Literal["AVAILABLE", "MISSING", "NOT_YET_REPORTED", "STALE", "RESTATED"]

STANDARD_FUNDAMENTAL_FIELDS = (
    "revenue",
    "net_income",
    "equity",
    "assets",
    "debt",
    "operating_cash_flow",
    "free_cash_flow",
    "shares_outstanding",
    "operating_income",
)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Fundamental timestamps must be timezone-aware")
    return value


class FundamentalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FundamentalObservation(FundamentalModel):
    observation_id: str
    symbol: str
    field: str
    value: float
    unit: str
    fiscal_period: str
    period_type: FundamentalPeriodType
    period_start: datetime | None = None
    period_end: datetime
    report_date: datetime
    filed_at: datetime
    available_at: datetime
    retrieved_at: datetime
    form: str
    accession: str
    source: str
    source_concepts: tuple[str, ...]
    is_restatement: bool = False

    _aware_times = field_validator(
        "period_start", "period_end", "report_date", "filed_at", "available_at", "retrieved_at"
    )(_aware)

    @model_validator(mode="after")
    def valid_availability(self) -> FundamentalObservation:
        if self.available_at < self.filed_at:
            raise ValueError("available_at cannot precede filed_at")
        return self


class FundamentalDataset(FundamentalModel):
    fundamental_dataset_id: str
    name: str
    provider: str
    symbols: tuple[str, ...]
    fields: tuple[str, ...]
    start_time: datetime
    end_time: datetime
    retrieved_at: datetime
    content_fingerprint: str
    observations: tuple[FundamentalObservation, ...]
    point_in_time_safe: bool
    restatement_safe: bool
    disclosure: str

    _aware_times = field_validator("start_time", "end_time", "retrieved_at")(_aware)


class FundamentalDatasetSummary(FundamentalModel):
    fundamental_dataset_id: str
    name: str
    provider: str
    symbols: tuple[str, ...]
    fields: tuple[str, ...]
    start_time: datetime
    end_time: datetime
    retrieved_at: datetime
    observation_count: int
    point_in_time_safe: bool
    restatement_safe: bool
    disclosure: str

    _aware_times = field_validator("start_time", "end_time", "retrieved_at")(_aware)


class CreateFundamentalDataset(FundamentalModel):
    name: str = Field(min_length=1, max_length=200)
    symbols: tuple[str, ...] = Field(min_length=1, max_length=100)
    start: datetime
    end: datetime

    _aware_times = field_validator("start", "end")(_aware)

    @model_validator(mode="after")
    def valid_period(self) -> CreateFundamentalDataset:
        if self.end <= self.start:
            raise ValueError("Fundamental dataset end must follow start")
        return self


class FundamentalFieldSnapshot(FundamentalModel):
    field: str
    status: FundamentalStatus
    value: float | None
    unit: str | None
    fiscal_period: str | None
    report_date: datetime | None
    filed_at: datetime | None
    available_at: datetime | None
    used_at: datetime
    age_days: int | None
    form: str | None
    accession: str | None
    is_restatement: bool = False

    _aware_times = field_validator("report_date", "filed_at", "available_at", "used_at")(_aware)


class FundamentalSnapshot(FundamentalModel):
    fundamental_dataset_id: str
    provider: str
    symbol: str
    used_at: datetime
    restatement_safe: bool
    fields: tuple[FundamentalFieldSnapshot, ...]

    _aware_used_at = field_validator("used_at")(_aware)


class FundamentalProviderInfo(FundamentalModel):
    provider_id: str
    name: str
    fields: tuple[str, ...]
    requires_credentials: bool
    point_in_time_semantics: str
    restatement_safe: bool
    status: Literal["AVAILABLE", "BLOCKED"]
    detail: str
