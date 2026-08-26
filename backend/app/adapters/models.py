from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CapabilityStatus = Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"]
TraceFidelity = Literal["FULL", "STANDARD", "BASIC"]
DeterminismStatus = Literal["DETERMINISTIC", "SEEDED", "UNVERIFIED"]
FrameworkValue = str | int | float | bool | None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Adapter timestamps must be timezone-aware")
    return value


class AdapterModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TraceCapabilitySet(AdapterModel):
    market_timeline: CapabilityStatus = "UNAVAILABLE"
    feature_values: CapabilityStatus = "UNAVAILABLE"
    feature_lineage: CapabilityStatus = "UNAVAILABLE"
    decision_events: CapabilityStatus = "UNAVAILABLE"
    decision_conditions: CapabilityStatus = "UNAVAILABLE"
    data_dependencies: CapabilityStatus = "UNAVAILABLE"
    orders: CapabilityStatus = "UNAVAILABLE"
    executions: CapabilityStatus = "UNAVAILABLE"
    positions: CapabilityStatus = "UNAVAILABLE"
    trades: CapabilityStatus = "UNAVAILABLE"
    equity: CapabilityStatus = "UNAVAILABLE"
    pnl: CapabilityStatus = "UNAVAILABLE"
    point_in_time_proven: CapabilityStatus = "UNAVAILABLE"
    gross_pnl: CapabilityStatus = "UNAVAILABLE"
    fees: CapabilityStatus = "UNAVAILABLE"
    slippage: CapabilityStatus = "UNAVAILABLE"
    trade_attribution: CapabilityStatus = "UNAVAILABLE"
    drawdowns: CapabilityStatus = "UNAVAILABLE"


def derive_trace_fidelity(capabilities: TraceCapabilitySet) -> TraceFidelity:
    full_fields = (
        capabilities.market_timeline,
        capabilities.feature_values,
        capabilities.feature_lineage,
        capabilities.decision_events,
        capabilities.decision_conditions,
        capabilities.data_dependencies,
        capabilities.orders,
        capabilities.executions,
        capabilities.positions,
        capabilities.trades,
        capabilities.equity,
        capabilities.pnl,
        capabilities.point_in_time_proven,
    )
    if all(status == "AVAILABLE" for status in full_fields):
        return "FULL"
    evidence_fields = (
        capabilities.feature_values,
        capabilities.decision_events,
    )
    if (
        capabilities.market_timeline == "AVAILABLE"
        and capabilities.equity == "AVAILABLE"
        and capabilities.pnl == "AVAILABLE"
        and any(status in {"AVAILABLE", "PARTIAL"} for status in evidence_fields)
    ):
        return "STANDARD"
    return "BASIC"


def full_trace_capabilities() -> TraceCapabilitySet:
    return TraceCapabilitySet.model_validate(
        {name: "AVAILABLE" for name in TraceCapabilitySet.model_fields}
    )


class RuntimeDescriptor(AdapterModel):
    kind: Literal["native", "framework"] = "native"
    adapter_id: str | None = None
    adapter_version: str | None = None
    framework_name: str | None = None
    framework_version: str | None = None
    execution_owner: str = "VQD"
    trace_fidelity: TraceFidelity = "FULL"
    trace_capabilities: TraceCapabilitySet = Field(default_factory=full_trace_capabilities)
    determinism: DeterminismStatus = "DETERMINISTIC"
    random_seed: int | None = None
    python_executable: str | None = None
    historical_research_only: bool = False


def native_runtime() -> RuntimeDescriptor:
    return RuntimeDescriptor()


class AdapterParameterSpec(AdapterModel):
    name: str
    label: str
    description: str = ""
    value_type: Literal["integer", "number"]
    default: int | float
    minimum: int | float
    maximum: int | float | None = None
    step: int | float = 1
    unit: str = ""


class AdapterDataRequirements(AdapterModel):
    required_fields: tuple[str, ...]
    symbol_count: int | None = None
    minimum_history: int = 1


class AdapterDiagnosticCapabilities(AdapterModel):
    train_test: bool = True
    parameter_sensitivity: str | None = None
    cost_stress: bool = False
    execution_delay: bool = False


class AdapterStrategyManifest(AdapterModel):
    strategy_id: str
    name: str
    description: str = ""
    version: str = "1"
    parameters: tuple[AdapterParameterSpec, ...] = ()
    data_requirements: AdapterDataRequirements
    diagnostic_capabilities: AdapterDiagnosticCapabilities = AdapterDiagnosticCapabilities()
    execution_config: dict[str, FrameworkValue] = Field(default_factory=dict)
    random_seed: int | None = None


class AdapterInspection(AdapterModel):
    adapter_id: str
    adapter_version: str
    framework_name: str
    framework_version: str | None
    installed: bool
    available: bool
    unavailable_reason: str | None = None
    manifest: AdapterStrategyManifest | None = None
    entrypoint: str


class AdapterMarketPoint(AdapterModel):
    timestamp: datetime
    values: dict[str, dict[str, float]]

    _aware_timestamp = field_validator("timestamp")(_aware)


class AdapterDataset(AdapterModel):
    dataset_id: str
    name: str
    revision: str
    symbols: tuple[str, ...]
    fields: tuple[str, ...]
    points: tuple[AdapterMarketPoint, ...]


class AdapterRunRequest(AdapterModel):
    adapter_id: str
    source_path: str
    entrypoint: str
    manifest: AdapterStrategyManifest
    dataset: AdapterDataset
    parameters: dict[str, int | float]
    research_cutoff: datetime | None = None

    @field_validator("research_cutoff")
    @classmethod
    def aware_cutoff(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)


class AdapterFeaturePoint(AdapterModel):
    timestamp: datetime
    name: str
    value: float | None
    formula: str = ""
    inputs: tuple[str, ...] = ()

    _aware_timestamp = field_validator("timestamp")(_aware)


class AdapterSignalPoint(AdapterModel):
    timestamp: datetime
    name: str
    active: bool
    symbol: str | None = None

    _aware_timestamp = field_validator("timestamp")(_aware)


class AdapterOrderRecord(AdapterModel):
    order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    submitted_at: datetime
    expected_execution_at: datetime
    price: float | None = None
    status: str = "RECORDED"

    _aware_submitted = field_validator("submitted_at")(_aware)
    _aware_expected = field_validator("expected_execution_at")(_aware)


class AdapterExecutionRecord(AdapterModel):
    execution_id: str
    source_order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    price: float
    executed_at: datetime
    fee: float = 0.0
    slippage: float | None = None
    reference_price: float | None = None
    meaning: str = "framework execution record"

    _aware_executed = field_validator("executed_at")(_aware)


class AdapterPositionPoint(AdapterModel):
    timestamp: datetime
    quantities: dict[str, float]
    market_values: dict[str, float] = Field(default_factory=dict)

    _aware_timestamp = field_validator("timestamp")(_aware)


class AdapterTradeRecord(AdapterModel):
    trade_id: str
    symbol: str
    direction: str
    status: Literal["OPEN", "CLOSED"]
    opened_at: datetime
    closed_at: datetime | None = None
    entry_price: float
    exit_price: float | None = None
    quantity: float
    pnl: float | None = None
    fees: float | None = None

    @field_validator("opened_at", "closed_at")
    @classmethod
    def aware_times(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)


class AdapterEquityPoint(AdapterModel):
    timestamp: datetime
    equity: float

    _aware_timestamp = field_validator("timestamp")(_aware)


class AdapterRunResult(AdapterModel):
    adapter_id: str
    adapter_version: str
    framework_name: str
    framework_version: str
    execution_owner: str
    strategy_id: str
    strategy_name: str
    parameters: dict[str, int | float]
    dataset_revision: str
    execution_semantics: dict[str, FrameworkValue]
    initial_equity: float
    market_timeline: tuple[AdapterMarketPoint, ...]
    features: tuple[AdapterFeaturePoint, ...] = ()
    signals: tuple[AdapterSignalPoint, ...] = ()
    orders: tuple[AdapterOrderRecord, ...] = ()
    executions: tuple[AdapterExecutionRecord, ...] = ()
    positions: tuple[AdapterPositionPoint, ...] = ()
    trades: tuple[AdapterTradeRecord, ...] = ()
    equity: tuple[AdapterEquityPoint, ...]
    framework_metrics: dict[str, FrameworkValue] = Field(default_factory=dict)
    capabilities: TraceCapabilitySet
    fidelity: TraceFidelity
    warnings: tuple[str, ...] = ()
    determinism: DeterminismStatus = "UNVERIFIED"
    random_seed: int | None = None
    adapter_runtime_seconds: float = 0.0
    normalization_seconds: float = 0.0

    @model_validator(mode="after")
    def fidelity_is_derived(self) -> Self:
        expected = derive_trace_fidelity(self.capabilities)
        if self.fidelity != expected:
            raise ValueError(
                f"Trace fidelity must be derived from captured capabilities: expected {expected}"
            )
        if not self.market_timeline or len(self.market_timeline) != len(self.equity):
            raise ValueError("Adapter market and equity timelines must be non-empty and aligned")
        market_times = tuple(point.timestamp for point in self.market_timeline)
        if tuple(point.timestamp for point in self.equity) != market_times:
            raise ValueError("Adapter equity timestamps must exactly match the market timeline")
        return self
