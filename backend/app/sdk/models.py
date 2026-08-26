from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal, overload

from app.models import DecisionCondition, Execution, Order, PortfolioSnapshot
from app.trace.models import DataDependency, TraceScalar

ParameterValue = int | float
TraceFidelity = Literal["FULL"]


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    value_type: Literal["integer", "number"]
    default: ParameterValue
    minimum: ParameterValue
    maximum: ParameterValue | None
    step: ParameterValue
    description: str
    label: str
    unit: str = ""


@dataclass(frozen=True, slots=True)
class DataRequirements:
    required_fields: tuple[str, ...] = ("close",)
    symbol_count: int | None = None
    symbols: tuple[str, ...] = ()
    minimum_history: int = 1

    def __post_init__(self) -> None:
        if self.symbol_count is not None and self.symbol_count < 1:
            raise ValueError("symbol_count must be positive")
        if self.minimum_history < 1:
            raise ValueError("minimum_history must be positive")


@dataclass(frozen=True, slots=True)
class DiagnosticCapabilities:
    parameter_sensitivity: str | None = None


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    strategy_id: str
    name: str
    version: str
    description: str
    data_requirements: DataRequirements = DataRequirements()
    diagnostic_capabilities: DiagnosticCapabilities = DiagnosticCapabilities()
    trace_fidelity: TraceFidelity = "FULL"


@dataclass(frozen=True, slots=True)
class MarketValueRef:
    symbol: str
    field: str
    value: float
    timestamp: datetime
    dependency_id: str

    def __float__(self) -> float:
        return self.value


@dataclass(frozen=True, slots=True)
class MarketSeries(Sequence[float]):
    points: tuple[MarketValueRef, ...]

    @overload
    def __getitem__(self, index: int) -> float: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[float, ...]: ...

    def __getitem__(self, index: int | slice) -> float | tuple[float, ...]:
        values = tuple(point.value for point in self.points)
        return values[index]

    def __len__(self) -> int:
        return len(self.points)

    def __iter__(self) -> Iterator[float]:
        return iter(tuple(point.value for point in self.points))

    @property
    def dependencies(self) -> tuple[MarketValueRef, ...]:
        return self.points

    @property
    def timestamps(self) -> tuple[datetime, ...]:
        return tuple(point.timestamp for point in self.points)


@dataclass(frozen=True, slots=True)
class FeatureRecord:
    feature_id: str
    name: str
    value: float | None
    formula: str
    inputs: tuple[str, ...]
    parameters: Mapping[str, TraceScalar]
    window_start: datetime | None
    window_end: datetime | None
    available_at: datetime
    data_dependencies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FeatureRef:
    record: FeatureRecord

    @property
    def feature_id(self) -> str:
        return self.record.feature_id

    @property
    def value(self) -> float | None:
        return self.record.value

    def __float__(self) -> float:
        if self.value is None:
            raise TypeError(f"Feature '{self.record.name}' is unavailable")
        return self.value


@dataclass(frozen=True, slots=True)
class TargetPortfolioIntent:
    target_positions: Mapping[str, float]
    target_weights: Mapping[str, float]
    gross_notional: float | None
    reason: str
    signal: str
    conditions: tuple[DecisionCondition, ...]
    dependencies: tuple[str, ...]
    previous_state: str
    next_state: str
    target_state: Literal[-1, 0, 1]
    transition: bool

    def __post_init__(self) -> None:
        if self.target_positions and self.target_weights:
            raise ValueError("Use target positions or target weights, not both")
        object.__setattr__(self, "target_positions", MappingProxyType(dict(self.target_positions)))
        object.__setattr__(self, "target_weights", MappingProxyType(dict(self.target_weights)))


@dataclass(frozen=True, slots=True)
class RuntimeDecision:
    signal_id: str | None
    intent: TargetPortfolioIntent
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeRow:
    index: int
    timestamp: datetime
    market: Mapping[str, Mapping[str, float]]
    features: tuple[FeatureRecord, ...]
    decision: RuntimeDecision
    orders: tuple[Order, ...]
    executions: tuple[Execution, ...]
    portfolio: PortfolioSnapshot
    data_dependencies: tuple[DataDependency, ...]


@dataclass(frozen=True, slots=True)
class RuntimeFailure:
    strategy_id: str
    timestamp: datetime
    event_index: int
    exception_type: str
    message: str
    traceback: str


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    status: Literal["COMPLETED", "FAILED", "PARTIAL"]
    rows: tuple[RuntimeRow, ...]
    failure: RuntimeFailure | None
    unfilled_signal_count: int
