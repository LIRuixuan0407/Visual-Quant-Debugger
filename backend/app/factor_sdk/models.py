from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, overload

from app.fundamentals import FundamentalFieldSnapshot
from app.sdk.models import FeatureRef, MarketSeries, MarketValueRef, ParameterValue
from app.trace.models import DataDependency

FactorCategory = Literal["PRICE_VOLUME", "VALUE", "QUALITY", "GROWTH", "LEVERAGE", "MIXED"]
FactorDataSource = Literal["MARKET", "FUNDAMENTAL", "MIXED"]
FactorToken = FeatureRef | MarketValueRef | MarketSeries


@dataclass(frozen=True, slots=True)
class FactorMetadata:
    factor_id: str
    name: str
    version: str
    description: str
    formula: str
    required_fields: tuple[str, ...] = ()
    required_fundamental_fields: tuple[str, ...] = ()
    lookback: int = 0
    category: FactorCategory = "PRICE_VOLUME"
    data_source: FactorDataSource = "MARKET"
    direction: Literal["HIGH", "LOW"] = "HIGH"
    availability: str = "close(t)"


@dataclass(frozen=True, slots=True)
class FactorPoint:
    value: float
    source_timestamp: datetime
    available_at: datetime
    used_at: datetime
    dependency: DataDependency
    token: FactorToken | None = field(default=None, repr=False)
    fundamental_input: FundamentalFieldSnapshot | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.available_at > self.used_at:
            raise ValueError("Factor input is not available at the requested point in time")

    def __float__(self) -> float:
        return self.value


@dataclass(frozen=True, slots=True)
class FactorSeries(Sequence[float]):
    points: tuple[FactorPoint, ...]

    @overload
    def __getitem__(self, index: int) -> float: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[float, ...]: ...

    def __getitem__(self, index: int | slice) -> float | tuple[float, ...]:
        return tuple(item.value for item in self.points)[index]

    def __len__(self) -> int:
        return len(self.points)

    def __iter__(self) -> Iterator[float]:
        return iter(tuple(item.value for item in self.points))

    @property
    def latest(self) -> FactorPoint | None:
        return self.points[-1] if self.points else None

    @property
    def timestamps(self) -> tuple[datetime, ...]:
        return tuple(item.source_timestamp for item in self.points)


@dataclass(frozen=True, slots=True)
class FactorResult:
    value: float | None
    formula: str
    inputs: tuple[FactorPoint | FactorSeries | FactorResult, ...]
    parameters: Mapping[str, ParameterValue]
    window_start: datetime | None
    window_end: datetime
    available_at: datetime
    token: FactorToken | None = field(default=None, repr=False)

    @property
    def dependencies(self) -> tuple[DataDependency, ...]:
        values: list[DataDependency] = []
        for item in self.inputs:
            if isinstance(item, FactorPoint):
                values.append(item.dependency)
            elif isinstance(item, FactorSeries):
                values.extend(point.dependency for point in item.points)
            else:
                values.extend(item.dependencies)
        return tuple({item.dependency_id: item for item in values}.values())

    @property
    def fundamental_inputs(self) -> tuple[FundamentalFieldSnapshot, ...]:
        values: list[FundamentalFieldSnapshot] = []
        for item in self.inputs:
            if isinstance(item, FactorPoint):
                if item.fundamental_input is not None:
                    values.append(item.fundamental_input)
            elif isinstance(item, FactorSeries):
                values.extend(
                    point.fundamental_input
                    for point in item.points
                    if point.fundamental_input is not None
                )
            else:
                values.extend(item.fundamental_inputs)
        return tuple(values)

    @property
    def tokens(self) -> tuple[FactorToken, ...]:
        if self.token is not None:
            return (self.token,)
        values: list[FactorToken] = []
        for item in self.inputs:
            if isinstance(item, FactorPoint):
                if item.token is not None:
                    values.append(item.token)
            elif isinstance(item, FactorSeries):
                values.extend(point.token for point in item.points if point.token is not None)
            else:
                values.extend(item.tokens)
        return tuple(values)
