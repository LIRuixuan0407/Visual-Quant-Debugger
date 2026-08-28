from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CorporateActionType = Literal["SPLIT", "CASH_DIVIDEND", "DELISTING"]
PriceAdjustmentPolicy = Literal["RAW", "SPLIT_ADJUSTED"]
CorporateActionEventStatus = Literal["APPLIED", "REFLECTED_IN_PRICE_VIEW", "UNRESOLVED"]


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("Corporate Action timestamps must be timezone-aware")
    return value


class CorporateActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CorporateAction(CorporateActionModel):
    action_id: str = Field(min_length=1, max_length=200)
    symbol: str = Field(min_length=1, max_length=32)
    action_type: CorporateActionType
    effective_at: datetime
    announced_at: datetime | None = None
    available_at: datetime
    source: str = Field(min_length=1, max_length=200)
    evidence: str = Field(min_length=1, max_length=10_000)
    split_ratio: float | None = Field(default=None, gt=0)
    cash_amount: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=1, max_length=12)
    delisting_reason: str | None = Field(default=None, max_length=2_000)
    settlement_price: float | None = Field(default=None, ge=0)

    _aware_times = field_validator("effective_at", "announced_at", "available_at")(_aware)

    @field_validator("symbol")
    @classmethod
    def normalized_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def type_specific_fields(self) -> CorporateAction:
        if self.announced_at is not None and self.available_at < self.announced_at:
            raise ValueError("Corporate Action available_at cannot precede announced_at")
        if self.action_type == "SPLIT":
            if self.split_ratio is None:
                raise ValueError("SPLIT requires split_ratio")
            if any(
                value is not None
                for value in (
                    self.cash_amount,
                    self.currency,
                    self.delisting_reason,
                    self.settlement_price,
                )
            ):
                raise ValueError("SPLIT only accepts split_ratio")
        elif self.action_type == "CASH_DIVIDEND":
            if self.cash_amount is None or self.currency is None:
                raise ValueError("CASH_DIVIDEND requires cash_amount and currency")
            if any(
                value is not None
                for value in (self.split_ratio, self.delisting_reason, self.settlement_price)
            ):
                raise ValueError("CASH_DIVIDEND only accepts cash_amount and currency")
        elif (
            self.split_ratio is not None
            or self.cash_amount is not None
            or self.currency is not None
        ):
            raise ValueError("DELISTING does not accept Split or Dividend fields")
        return self


class CreateCorporateActionDataset(CorporateActionModel):
    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=200)
    actions: tuple[CorporateAction, ...] = Field(min_length=1)
    disclosure: str = Field(min_length=1, max_length=10_000)


class CorporateActionDataset(CorporateActionModel):
    corporate_action_dataset_id: str
    name: str
    provider: str
    symbols: tuple[str, ...]
    start_time: datetime
    end_time: datetime
    retrieved_at: datetime
    content_fingerprint: str
    actions: tuple[CorporateAction, ...]
    point_in_time_safe: bool
    disclosure: str

    _aware_times = field_validator("start_time", "end_time", "retrieved_at")(_aware)

    @model_validator(mode="after")
    def coherent_dataset(self) -> CorporateActionDataset:
        if self.end_time < self.start_time:
            raise ValueError("Corporate Action dataset end_time cannot precede start_time")
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("Corporate Action dataset symbols must be unique")
        if len(self.actions) != len({item.action_id for item in self.actions}):
            raise ValueError("Corporate Action ids must be unique inside a dataset")
        expected_symbols = tuple(sorted({item.symbol for item in self.actions}))
        if self.symbols != expected_symbols:
            raise ValueError("Corporate Action dataset symbols do not match its actions")
        if self.actions:
            if min(item.effective_at for item in self.actions) != self.start_time:
                raise ValueError("Corporate Action dataset start_time does not match its actions")
            if max(item.effective_at for item in self.actions) != self.end_time:
                raise ValueError("Corporate Action dataset end_time does not match its actions")
        expected_safe = all(item.available_at <= item.effective_at for item in self.actions)
        if self.point_in_time_safe != expected_safe:
            raise ValueError("Corporate Action point_in_time_safe does not match event timestamps")
        return self


class CorporateActionEvent(CorporateActionModel):
    action_id: str
    symbol: str
    action_type: CorporateActionType
    timestamp: datetime
    status: CorporateActionEventStatus
    quantity_before: float
    quantity_after: float
    cash_amount: float
    settlement_price: float | None = None
    evidence: str

    _aware_timestamp = field_validator("timestamp")(_aware)


class CorporateActionApplication(CorporateActionModel):
    corporate_action_dataset_id: str
    price_adjustment_policy: PriceAdjustmentPolicy
    starting_cash: float
    ending_cash: float
    starting_positions: dict[str, float]
    ending_positions: dict[str, float]
    events: tuple[CorporateActionEvent, ...]
    unresolved_action_ids: tuple[str, ...]


class AdjustedMarketView(CorporateActionModel):
    dataset_id: str
    corporate_action_dataset_id: str | None
    price_adjustment_policy: PriceAdjustmentPolicy
    raw_dataset_fingerprint: str
    frame_count: int
    split_count: int
