from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.trace.models import BacktestTrace


@dataclass(frozen=True, slots=True)
class FeaturePoint:
    hedge_ratio: float | None
    spread: float | None
    rolling_mean: float | None
    rolling_std: float | None
    zscore: float | None
    window_start: datetime | None
    window_end: datetime | None
    available_at: datetime


@dataclass(frozen=True, slots=True)
class DecisionCondition:
    left_operand: str
    left_value: float | None
    operator: str
    right_operand: str | None
    right_value: float | None
    result: bool
    description: str


@dataclass(frozen=True, slots=True)
class SignalDecision:
    signal_id: str | None
    action: Literal["LONG_SPREAD", "SHORT_SPREAD", "CLOSE", "HOLD", "WARMUP"]
    target_position: Literal[-1, 0, 1]
    reason: str
    decided_at: datetime
    previous_target: Literal[-1, 0, 1]
    conditions: tuple[DecisionCondition, ...]


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    submitted_at: datetime
    target_position: Literal[-1, 0, 1]
    source_signal_id: str


@dataclass(frozen=True, slots=True)
class Execution:
    execution_id: str
    source_order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    expected_price: float
    fill_price: float
    traded_notional: float
    fee: float
    slippage: float
    executed_at: datetime
    spread_cost: float = 0.0
    market_impact: float = 0.0


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    cash: float
    quantity_a: float
    quantity_b: float
    gross_exposure: float
    net_exposure: float
    equity: float
    cumulative_fees: float
    cumulative_slippage: float
    positions: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TimelineRow:
    timestamp: datetime
    asset_a: float
    asset_b: float
    feature: FeaturePoint
    decision: SignalDecision
    orders: tuple[Order, ...]
    executions: tuple[Execution, ...]
    portfolio: PortfolioSnapshot


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    total_return: float
    sharpe: float
    max_drawdown: float
    turnover: float
    net_pnl: float
    gross_pnl: float
    total_fees: float
    total_slippage: float
    number_of_orders: int


@dataclass(frozen=True, slots=True)
class BacktestResult:
    timeline: tuple[TimelineRow, ...]
    metrics: BacktestMetrics
    trace: BacktestTrace
    unfilled_signal_count: int = 0
