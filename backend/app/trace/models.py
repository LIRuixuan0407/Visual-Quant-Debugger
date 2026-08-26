from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.adapters.models import RuntimeDescriptor, native_runtime

type TraceScalar = str | int | float | bool
type PositionState = str


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Trace timestamps must be timezone-aware")
    return value


class TraceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TraceMetadata(TraceModel):
    dataset_id: str
    dataset_name: str
    bar_count: int
    data_start: datetime
    data_end: datetime
    execution_model: str
    runtime: RuntimeDescriptor = Field(default_factory=native_runtime)
    adapter_warnings: tuple[str, ...] = ()

    _aware_start = field_validator("data_start")(_require_aware)
    _aware_end = field_validator("data_end")(_require_aware)


class StrategyDescriptor(TraceModel):
    strategy_id: str
    name: str


class DataDependency(TraceModel):
    dependency_id: str
    source: str
    field: str
    symbol: str | None = None
    value: float | None = None
    source_timestamp: datetime
    available_at: datetime
    used_at: datetime

    _aware_source = field_validator("source_timestamp")(_require_aware)
    _aware_available = field_validator("available_at")(_require_aware)
    _aware_used = field_validator("used_at")(_require_aware)


class MarketValue(TraceModel):
    symbol: str
    field: str
    value: float
    dependency_id: str


class MarketSnapshot(TraceModel):
    values: tuple[MarketValue, ...]


class FeatureSnapshot(TraceModel):
    feature_id: str
    name: str
    value: float | None
    formula: str
    inputs: tuple[str, ...]
    parameters: dict[str, TraceScalar]
    window_start: datetime | None
    window_end: datetime | None
    available_at: datetime
    data_dependencies: tuple[str, ...]

    @field_validator("window_start", "window_end", "available_at")
    @classmethod
    def require_aware_when_present(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware(value)


class SignalCondition(TraceModel):
    left_operand: str
    left_value: float | None
    operator: str
    right_operand: str | None
    right_value: float | None
    result: bool
    description: str


class SignalEvaluation(TraceModel):
    evaluation_id: str
    signal_id: str | None
    signal: str
    decision_time: datetime
    reason: str
    conditions: tuple[SignalCondition, ...]
    dependencies: tuple[str, ...]
    previous_state: PositionState
    next_state: PositionState
    target_position: Literal[-1, 0, 1]
    target_positions: dict[str, float] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    _aware_decision = field_validator("decision_time")(_require_aware)


class AssetPosition(TraceModel):
    symbol: str
    quantity: float
    market_value: float


class PositionSnapshot(TraceModel):
    position_state: PositionState
    target_position: Literal[-1, 0, 1]
    asset_positions: tuple[AssetPosition, ...]
    gross_exposure: float
    net_exposure: float
    target_positions: dict[str, float] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class OrderEvent(TraceModel):
    order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    submitted_at: datetime
    expected_execution_at: datetime
    target_position: Literal[-1, 0, 1]
    source_signal_id: str

    _aware_submitted = field_validator("submitted_at")(_require_aware)
    _aware_expected = field_validator("expected_execution_at")(_require_aware)


class ExecutionEvent(TraceModel):
    execution_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    reference_price: float
    fill_price: float
    traded_notional: float
    fee: float
    slippage: float
    executed_at: datetime
    source_order_id: str

    _aware_executed = field_validator("executed_at")(_require_aware)


class CostSnapshot(TraceModel):
    fees: float
    slippage: float
    total_cost: float
    cumulative_fees: float
    cumulative_slippage: float


class PnLSnapshot(TraceModel):
    period_gross_pnl: float
    period_net_pnl: float
    cumulative_gross_pnl: float
    cumulative_net_pnl: float
    equity: float


class TimelineEvent(TraceModel):
    event_id: str
    timestamp: datetime
    market_snapshot: MarketSnapshot
    feature_snapshots: tuple[FeatureSnapshot, ...]
    signal_evaluation: SignalEvaluation
    position_snapshot: PositionSnapshot
    order_events: tuple[OrderEvent, ...]
    execution_events: tuple[ExecutionEvent, ...]
    cost_snapshot: CostSnapshot
    pnl_snapshot: PnLSnapshot
    data_dependencies: tuple[DataDependency, ...]

    _aware_timestamp = field_validator("timestamp")(_require_aware)


class TradeTrace(TraceModel):
    trade_id: str
    direction: str
    status: Literal["OPEN", "CLOSED"]
    entry_signal_id: str
    exit_signal_id: str | None
    entry_event_id: str
    exit_event_id: str | None
    opened_at: datetime
    closed_at: datetime | None
    order_ids: tuple[str, ...]
    execution_ids: tuple[str, ...]

    @field_validator("opened_at", "closed_at")
    @classmethod
    def require_aware_when_present(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware(value)


class Diagnostic(TraceModel):
    diagnostic_id: str
    severity: Literal["WARNING", "ERROR"]
    code: str
    message: str
    event_id: str
    dependency_id: str


class BacktestTrace(TraceModel):
    trace_version: Literal["1.0"] = "1.0"
    metadata: TraceMetadata
    strategy: StrategyDescriptor
    parameters: dict[str, TraceScalar]
    timeline: tuple[TimelineEvent, ...]
    trades: tuple[TradeTrace, ...]
    metrics: dict[str, float | int]
    diagnostics: tuple[Diagnostic, ...]
