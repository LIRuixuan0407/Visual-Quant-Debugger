from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.sdk.models import RuntimeFailure
from app.trace.models import Diagnostic, TimelineEvent, TraceScalar

SessionStatus = Literal["CREATED", "RUNNING", "PAUSED", "COMPLETED", "STOPPED", "ERROR", "FAILED"]
PendingStatus = Literal["PENDING", "FILLED", "CANCELLED", "EXPIRED_END_OF_DATA"]


class ForwardModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PendingTransition(ForwardModel):
    pending_id: str
    source_signal_id: str
    source_event_id: str
    source_bar_index: int
    target_position: Literal[-1, 0, 1]
    hedge_ratio: float
    status: PendingStatus
    scheduled_bar_index: int
    scheduled_at: datetime | None = None
    resolved_at: datetime | None = None
    target_positions: dict[str, float] | None = None


class ForwardTrace(ForwardModel):
    trace_version: Literal["1.0"] = "1.0"
    session_id: str
    strategy_id: str
    parameters: dict[str, TraceScalar]
    timeline: tuple[TimelineEvent, ...]
    diagnostics: tuple[Diagnostic, ...]


class ForwardSessionSummary(ForwardModel):
    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    fees: float
    slippage: float
    signal_count: int
    execution_count: int
    closed_trade_count: int
    open_trade_count: int
    processed_bars: int
    expired_order_count: int


class ForwardSessionSnapshot(ForwardModel):
    session_id: str
    status: SessionStatus
    strategy_id: str
    dataset_id: str
    parameters: dict[str, TraceScalar]
    processed_bar_count: int
    total_bar_count: int
    current_timestamp: datetime | None
    cash: float
    quantity_a: float
    quantity_b: float
    equity: float
    cumulative_pnl: float
    cumulative_fees: float
    cumulative_slippage: float
    current_signal_state: str
    pending_transitions: tuple[PendingTransition, ...]
    latest_event: TimelineEvent | None
    summary: ForwardSessionSummary
    positions: dict[str, float] = {}
    failure: RuntimeFailure | None = None


class ConsistencyCheck(ForwardModel):
    field: str
    batch_value: str | float | int
    forward_value: str | float | int
    difference: float | None
    status: Literal["MATCH", "DIVERGENCE"]


class FirstDivergence(ForwardModel):
    field: str
    index: int | None = None
    batch_value: str | float | int
    forward_value: str | float | int


class ResearchForwardMetrics(ForwardModel):
    period_label: str
    total_return: float
    sharpe: float
    max_drawdown: float
    turnover: float
    trades: int
    fees: float
    slippage: float
    final_equity: float


class ForwardComparisonReport(ForwardModel):
    session_id: str
    different_evaluation_periods: bool = True
    research: ResearchForwardMetrics
    forward: ResearchForwardMetrics
    consistency: tuple[ConsistencyCheck, ...]
    consistency_status: Literal["MATCH", "DIVERGENCE"]
    first_divergence: FirstDivergence | None
