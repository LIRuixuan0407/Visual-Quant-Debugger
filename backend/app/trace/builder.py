import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, cast

from app.models import BacktestMetrics, MarketBar, TimelineRow
from app.trace.models import (
    AssetPosition,
    BacktestTrace,
    CostSnapshot,
    DataDependency,
    ExecutionEvent,
    FeatureSnapshot,
    MarketSnapshot,
    MarketValue,
    OrderEvent,
    PnLSnapshot,
    PositionSnapshot,
    PositionState,
    SignalCondition,
    SignalEvaluation,
    StrategyDescriptor,
    TimelineEvent,
    TraceMetadata,
    TraceScalar,
    TradeTrace,
)
from app.trace.validation import collect_look_ahead_diagnostics

FEATURE_NAMES = ("hedge_ratio", "spread", "rolling_mean", "rolling_std", "zscore")


@dataclass(frozen=True, slots=True)
class TraceBuildConfiguration:
    dataset_name: str
    strategy_id: str
    strategy_name: str
    parameters: dict[str, TraceScalar]
    lookback: int
    initial_cash: float
    execution_model: str = "signal at close(t); execute at close(t+1)"


@dataclass(slots=True)
class _DependencyRecorder:
    bars: tuple[MarketBar, ...]
    used_at: datetime
    counter: int
    dependencies: list[DataDependency] = field(default_factory=list)
    ids: dict[tuple[int, str], str] = field(default_factory=dict)

    def market(self, source_index: int, symbol: Literal["ASSET_A", "ASSET_B"]) -> str:
        key = (source_index, symbol)
        if key in self.ids:
            return self.ids[key]
        self.counter += 1
        source_bar = self.bars[source_index]
        value = source_bar.asset_a if symbol == "ASSET_A" else source_bar.asset_b
        dependency = DataDependency(
            dependency_id=f"dependency-{self.counter:06d}",
            source="market_data",
            field="close",
            symbol=symbol,
            value=value,
            source_timestamp=source_bar.timestamp,
            available_at=source_bar.timestamp,
            used_at=self.used_at,
        )
        self.dependencies.append(dependency)
        self.ids[key] = dependency.dependency_id
        return dependency.dependency_id


def _dataset_id(bars: tuple[MarketBar, ...]) -> str:
    semantic_rows = [
        [bar.timestamp.isoformat(), repr(bar.asset_a), repr(bar.asset_b)] for bar in bars
    ]
    payload = json.dumps(semantic_rows, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def _feature_id(bar_index: int, name: str) -> str:
    offset = FEATURE_NAMES.index(name)
    return f"feature-{bar_index * len(FEATURE_NAMES) + offset + 1:06d}"


def _state(target: int) -> PositionState:
    if target > 0:
        return "LONG_SPREAD"
    if target < 0:
        return "SHORT_SPREAD"
    return "FLAT"


def _build_trades(events: tuple[TimelineEvent, ...]) -> tuple[TradeTrace, ...]:
    orders_by_signal = {
        order.source_signal_id: (event, event.order_events, event.execution_events)
        for event in events
        for order in event.order_events
    }
    trades: list[TradeTrace] = []
    active: TradeTrace | None = None
    for event in events:
        signal = event.signal_evaluation
        if signal.signal_id is None:
            continue
        linked = orders_by_signal.get(signal.signal_id)
        if signal.signal in {"LONG_SPREAD", "SHORT_SPREAD"} and linked is not None:
            execution_event, orders, executions = linked
            if not executions:
                continue
            active = TradeTrace(
                trade_id=f"trade-{len(trades) + 1:06d}",
                direction=cast(Literal["LONG_SPREAD", "SHORT_SPREAD"], signal.signal),
                status="OPEN",
                entry_signal_id=signal.signal_id,
                exit_signal_id=None,
                entry_event_id=execution_event.event_id,
                exit_event_id=None,
                opened_at=executions[0].executed_at,
                closed_at=None,
                order_ids=tuple(order.order_id for order in orders),
                execution_ids=tuple(execution.execution_id for execution in executions),
            )
            trades.append(active)
        elif signal.signal == "CLOSE" and active is not None and linked is not None:
            execution_event, orders, executions = linked
            if not executions:
                continue
            closed = active.model_copy(
                update={
                    "status": "CLOSED",
                    "exit_signal_id": signal.signal_id,
                    "exit_event_id": execution_event.event_id,
                    "closed_at": executions[0].executed_at,
                    "order_ids": active.order_ids + tuple(order.order_id for order in orders),
                    "execution_ids": active.execution_ids
                    + tuple(execution.execution_id for execution in executions),
                }
            )
            trades[-1] = closed
            active = None
    return tuple(trades)


def build_trace(
    bars: tuple[MarketBar, ...],
    timeline: tuple[TimelineRow, ...],
    metrics: BacktestMetrics,
    configuration: TraceBuildConfiguration,
) -> BacktestTrace:
    if len(bars) != len(timeline):
        raise ValueError("bars and timeline must have equal length")

    events: list[TimelineEvent] = []
    dependency_counter = 0
    previous_equity = configuration.initial_cash

    for index, (bar, row) in enumerate(zip(bars, timeline, strict=True)):
        dependency_recorder = _DependencyRecorder(bars, bar.timestamp, dependency_counter)
        current_a_dependency = dependency_recorder.market(index, "ASSET_A")
        current_b_dependency = dependency_recorder.market(index, "ASSET_B")
        hedge_dependency_ids: tuple[str, ...] = ()
        hedge_window_start = None
        if row.feature.hedge_ratio is not None:
            source_start = index - configuration.lookback + 1
            symbols: tuple[Literal["ASSET_A", "ASSET_B"], ...] = ("ASSET_A", "ASSET_B")
            hedge_dependency_ids = tuple(
                dependency_recorder.market(source_index, symbol)
                for source_index in range(source_start, index + 1)
                for symbol in symbols
            )
            hedge_window_start = bars[source_start].timestamp

        spread_inputs = (
            (_feature_id(index, "hedge_ratio"),) if row.feature.hedge_ratio is not None else ()
        )
        rolling_inputs = (
            tuple(
                _feature_id(source_index, "spread")
                for source_index in range(index - configuration.lookback + 1, index + 1)
            )
            if row.feature.zscore is not None
            else ()
        )
        features = (
            FeatureSnapshot(
                feature_id=_feature_id(index, "hedge_ratio"),
                name="hedge_ratio",
                value=row.feature.hedge_ratio,
                formula="dot(price_B, price_A) / dot(price_B, price_B)",
                inputs=(),
                parameters={"lookback": configuration.lookback},
                window_start=hedge_window_start,
                window_end=bar.timestamp if row.feature.hedge_ratio is not None else None,
                available_at=row.feature.available_at,
                data_dependencies=hedge_dependency_ids,
            ),
            FeatureSnapshot(
                feature_id=_feature_id(index, "spread"),
                name="spread",
                value=row.feature.spread,
                formula="price_A - hedge_ratio * price_B",
                inputs=spread_inputs,
                parameters={},
                window_start=bar.timestamp if row.feature.spread is not None else None,
                window_end=bar.timestamp if row.feature.spread is not None else None,
                available_at=row.feature.available_at,
                data_dependencies=(current_a_dependency, current_b_dependency)
                if row.feature.spread is not None
                else (),
            ),
            FeatureSnapshot(
                feature_id=_feature_id(index, "rolling_mean"),
                name="rolling_mean",
                value=row.feature.rolling_mean,
                formula="mean(spread_window)",
                inputs=rolling_inputs,
                parameters={"lookback": configuration.lookback},
                window_start=row.feature.window_start,
                window_end=row.feature.window_end,
                available_at=row.feature.available_at,
                data_dependencies=(),
            ),
            FeatureSnapshot(
                feature_id=_feature_id(index, "rolling_std"),
                name="rolling_std",
                value=row.feature.rolling_std,
                formula="population_std(spread_window, ddof=0)",
                inputs=rolling_inputs,
                parameters={"lookback": configuration.lookback, "ddof": 0},
                window_start=row.feature.window_start,
                window_end=row.feature.window_end,
                available_at=row.feature.available_at,
                data_dependencies=(),
            ),
            FeatureSnapshot(
                feature_id=_feature_id(index, "zscore"),
                name="zscore",
                value=row.feature.zscore,
                formula="(spread - rolling_mean) / rolling_std",
                inputs=(
                    _feature_id(index, "spread"),
                    _feature_id(index, "rolling_mean"),
                    _feature_id(index, "rolling_std"),
                ),
                parameters={},
                window_start=row.feature.window_start,
                window_end=row.feature.window_end,
                available_at=row.feature.available_at,
                data_dependencies=(),
            ),
        )

        previous_state = _state(row.decision.previous_target)
        next_state = _state(row.decision.target_position)
        signal = SignalEvaluation(
            evaluation_id=f"signal-evaluation-{index + 1:06d}",
            signal_id=row.decision.signal_id,
            signal=row.decision.action,
            decision_time=row.decision.decided_at,
            reason=row.decision.reason,
            conditions=tuple(
                SignalCondition(
                    left_operand=condition.left_operand,
                    left_value=condition.left_value,
                    operator=condition.operator,
                    right_operand=condition.right_operand,
                    right_value=condition.right_value,
                    result=condition.result,
                    description=condition.description,
                )
                for condition in row.decision.conditions
            ),
            dependencies=(_feature_id(index, "zscore"),) if row.feature.zscore is not None else (),
            previous_state=previous_state,
            next_state=next_state,
            target_position=row.decision.target_position,
        )

        order_events = tuple(
            OrderEvent(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                submitted_at=order.submitted_at,
                expected_execution_at=order.submitted_at,
                target_position=order.target_position,
                source_signal_id=order.source_signal_id,
            )
            for order in row.orders
        )
        execution_events = tuple(
            ExecutionEvent(
                execution_id=execution.execution_id,
                symbol=execution.symbol,
                side=execution.side,
                quantity=execution.quantity,
                reference_price=execution.expected_price,
                fill_price=execution.fill_price,
                traded_notional=execution.traded_notional,
                fee=execution.fee,
                slippage=execution.slippage,
                spread_cost=execution.spread_cost,
                market_impact=execution.market_impact,
                executed_at=execution.executed_at,
                source_order_id=execution.source_order_id,
            )
            for execution in row.executions
        )
        fees = sum(execution.fee for execution in row.executions)
        slippage = sum(execution.slippage for execution in row.executions)
        period_net_pnl = row.portfolio.equity - previous_equity
        cumulative_net_pnl = row.portfolio.equity - configuration.initial_cash
        cumulative_gross_pnl = (
            cumulative_net_pnl + row.portfolio.cumulative_fees + row.portfolio.cumulative_slippage
        )
        event = TimelineEvent(
            event_id=f"timeline-{index + 1:06d}",
            timestamp=bar.timestamp,
            market_snapshot=MarketSnapshot(
                values=(
                    MarketValue(
                        symbol="ASSET_A",
                        field="close",
                        value=bar.asset_a,
                        dependency_id=current_a_dependency,
                    ),
                    MarketValue(
                        symbol="ASSET_B",
                        field="close",
                        value=bar.asset_b,
                        dependency_id=current_b_dependency,
                    ),
                )
            ),
            feature_snapshots=features,
            signal_evaluation=signal,
            position_snapshot=PositionSnapshot(
                position_state=(
                    "LONG_SPREAD"
                    if row.portfolio.quantity_a > 0
                    else "SHORT_SPREAD"
                    if row.portfolio.quantity_a < 0
                    else "FLAT"
                ),
                target_position=row.decision.target_position,
                asset_positions=(
                    AssetPosition(
                        symbol="ASSET_A",
                        quantity=row.portfolio.quantity_a,
                        market_value=row.portfolio.quantity_a * bar.asset_a,
                    ),
                    AssetPosition(
                        symbol="ASSET_B",
                        quantity=row.portfolio.quantity_b,
                        market_value=row.portfolio.quantity_b * bar.asset_b,
                    ),
                ),
                gross_exposure=row.portfolio.gross_exposure,
                net_exposure=row.portfolio.net_exposure,
            ),
            order_events=order_events,
            execution_events=execution_events,
            cost_snapshot=CostSnapshot(
                fees=fees,
                slippage=slippage,
                total_cost=fees + slippage,
                cumulative_fees=row.portfolio.cumulative_fees,
                cumulative_slippage=row.portfolio.cumulative_slippage,
            ),
            pnl_snapshot=PnLSnapshot(
                period_gross_pnl=period_net_pnl + fees + slippage,
                period_net_pnl=period_net_pnl,
                cumulative_gross_pnl=cumulative_gross_pnl,
                cumulative_net_pnl=cumulative_net_pnl,
                equity=row.portfolio.equity,
            ),
            data_dependencies=tuple(dependency_recorder.dependencies),
        )
        events.append(event)
        dependency_counter = dependency_recorder.counter
        previous_equity = row.portfolio.equity

    timeline_events = tuple(events)
    trace = BacktestTrace(
        metadata=TraceMetadata(
            dataset_id=_dataset_id(bars),
            dataset_name=configuration.dataset_name,
            bar_count=len(bars),
            data_start=bars[0].timestamp,
            data_end=bars[-1].timestamp,
            execution_model=configuration.execution_model,
        ),
        strategy=StrategyDescriptor(
            strategy_id=configuration.strategy_id,
            name=configuration.strategy_name,
        ),
        parameters=configuration.parameters,
        timeline=timeline_events,
        trades=_build_trades(timeline_events),
        metrics={
            "total_return": metrics.total_return,
            "sharpe": metrics.sharpe,
            "max_drawdown": metrics.max_drawdown,
            "turnover": metrics.turnover,
            "net_pnl": metrics.net_pnl,
            "gross_pnl": metrics.gross_pnl,
            "total_fees": metrics.total_fees,
            "total_slippage": metrics.total_slippage,
            "number_of_orders": metrics.number_of_orders,
        },
        diagnostics=(),
    )
    return trace.model_copy(update={"diagnostics": collect_look_ahead_diagnostics(trace)})
