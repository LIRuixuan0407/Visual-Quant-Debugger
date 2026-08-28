from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from app.corporate_actions.models import CorporateActionEvent
from app.models import BacktestMetrics
from app.sdk.models import RuntimeRow
from app.trace.models import (
    AssetPosition,
    BacktestTrace,
    CostSnapshot,
    ExecutionEvent,
    FeatureSnapshot,
    MarketSnapshot,
    MarketValue,
    OrderEvent,
    PnLSnapshot,
    PositionSnapshot,
    SignalCondition,
    SignalEvaluation,
    StrategyDescriptor,
    TimelineEvent,
    TraceMetadata,
    TraceScalar,
    TradeTrace,
)
from app.trace.validation import collect_look_ahead_diagnostics


@dataclass(frozen=True, slots=True)
class RuntimeTraceConfiguration:
    dataset_id: str
    dataset_name: str
    strategy_id: str
    strategy_name: str
    parameters: dict[str, TraceScalar]
    initial_cash: float
    execution_model: str = "signal at close(t); execute at close(t+1)"
    corporate_action_events: tuple[CorporateActionEvent, ...] = ()


def calculate_runtime_metrics(rows: tuple[RuntimeRow, ...], initial_cash: float) -> BacktestMetrics:
    if not rows:
        raise ValueError("Runtime metrics require at least one row")
    equity = np.asarray((initial_cash, *(row.portfolio.equity for row in rows)), dtype=np.float64)
    returns = equity[1:] / equity[:-1] - 1.0
    return_std = float(np.std(returns, ddof=1)) if returns.size >= 2 else 0.0
    sharpe = 0.0 if return_std == 0 else float(np.mean(returns) / return_std * math.sqrt(252))
    drawdown = equity / np.maximum.accumulate(equity) - 1.0
    final = rows[-1].portfolio
    net_pnl = final.equity - initial_cash
    traded_notional = sum(execution.traded_notional for row in rows for execution in row.executions)
    average_equity = float(np.mean(equity[1:]))
    return BacktestMetrics(
        total_return=net_pnl / initial_cash,
        sharpe=sharpe,
        max_drawdown=float(np.min(drawdown)),
        turnover=traded_notional / average_equity if average_equity > 0 else 0.0,
        net_pnl=net_pnl,
        gross_pnl=net_pnl + final.cumulative_fees + final.cumulative_slippage,
        total_fees=final.cumulative_fees,
        total_slippage=final.cumulative_slippage,
        number_of_orders=sum(len(row.orders) for row in rows),
    )


def _target_values(row: RuntimeRow) -> dict[str, float]:
    intent = row.decision.intent
    values = intent.target_positions or intent.target_weights
    return dict(values)


def _position_state(row: RuntimeRow) -> str:
    values = [value for value in row.portfolio.positions.values() if abs(value) >= 1e-12]
    if not values:
        return "FLAT"
    if all(value > 0 for value in values):
        return "LONG"
    if all(value < 0 for value in values):
        return "SHORT"
    return "MIXED"


def _generic_trades(events: tuple[TimelineEvent, ...]) -> tuple[TradeTrace, ...]:
    execution_by_signal = {
        order.source_signal_id: (event, event.order_events, event.execution_events)
        for event in events
        for order in event.order_events
    }
    trades: list[TradeTrace] = []
    active_index: int | None = None
    for event in events:
        signal = event.signal_evaluation
        if signal.signal_id is None:
            continue
        linked = execution_by_signal.get(signal.signal_id)
        if linked is None or not linked[2]:
            continue
        execution_event, orders, executions = linked
        target_values = signal.target_positions or {}
        is_flat = not any(abs(value) >= 1e-12 for value in target_values.values())
        if not is_flat:
            trade = TradeTrace(
                trade_id=f"trade-{len(trades) + 1:06d}",
                direction=signal.next_state,
                status="OPEN",
                entry_signal_id=signal.signal_id,
                exit_signal_id=None,
                entry_event_id=execution_event.event_id,
                exit_event_id=None,
                opened_at=executions[0].executed_at,
                closed_at=None,
                order_ids=tuple(order.order_id for order in orders),
                execution_ids=tuple(item.execution_id for item in executions),
            )
            trades.append(trade)
            active_index = len(trades) - 1
        elif active_index is not None:
            active = trades[active_index]
            trades[active_index] = active.model_copy(
                update={
                    "status": "CLOSED",
                    "exit_signal_id": signal.signal_id,
                    "exit_event_id": execution_event.event_id,
                    "closed_at": executions[0].executed_at,
                    "order_ids": active.order_ids + tuple(order.order_id for order in orders),
                    "execution_ids": active.execution_ids
                    + tuple(item.execution_id for item in executions),
                }
            )
            active_index = None
    return tuple(trades)


def build_runtime_trace(
    rows: tuple[RuntimeRow, ...], configuration: RuntimeTraceConfiguration
) -> BacktestTrace:
    if not rows:
        raise ValueError("A trace requires at least one runtime row")
    metrics = calculate_runtime_metrics(rows, configuration.initial_cash)
    previous_equity = configuration.initial_cash
    events: list[TimelineEvent] = []
    for row in rows:
        current_dependencies = {
            (item.symbol, item.field): item
            for item in row.data_dependencies
            if item.used_at == row.timestamp and item.symbol is not None
        }
        market_values = tuple(
            MarketValue(
                symbol=symbol,
                field=field,
                value=value,
                dependency_id=current_dependencies[(symbol, field)].dependency_id,
            )
            for symbol, fields in row.market.items()
            for field, value in fields.items()
        )
        features = tuple(
            FeatureSnapshot(
                feature_id=item.feature_id,
                name=item.name,
                value=item.value,
                formula=item.formula,
                inputs=item.inputs,
                parameters=dict(item.parameters),
                window_start=item.window_start,
                window_end=item.window_end,
                available_at=item.available_at,
                data_dependencies=item.data_dependencies,
            )
            for item in row.features
        )
        intent = row.decision.intent
        target_values = _target_values(row)
        signal = SignalEvaluation(
            evaluation_id=f"signal-evaluation-{row.index + 1:06d}",
            signal_id=row.decision.signal_id,
            signal=intent.signal,
            decision_time=row.decision.decided_at,
            reason=intent.reason,
            conditions=tuple(
                SignalCondition(
                    left_operand=item.left_operand,
                    left_value=item.left_value,
                    operator=item.operator,
                    right_operand=item.right_operand,
                    right_value=item.right_value,
                    result=item.result,
                    description=item.description,
                )
                for item in intent.conditions
            ),
            dependencies=intent.dependencies,
            previous_state=intent.previous_state,
            next_state=intent.next_state,
            target_position=intent.target_state,
            target_positions=target_values,
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
                execution_id=item.execution_id,
                symbol=item.symbol,
                side=item.side,
                quantity=item.quantity,
                reference_price=item.expected_price,
                fill_price=item.fill_price,
                traded_notional=item.traded_notional,
                fee=item.fee,
                slippage=item.slippage,
                executed_at=item.executed_at,
                source_order_id=item.source_order_id,
            )
            for item in row.executions
        )
        fees = sum(item.fee for item in row.executions)
        slippage = sum(item.slippage for item in row.executions)
        period_net_pnl = row.portfolio.equity - previous_equity
        cumulative_net_pnl = row.portfolio.equity - configuration.initial_cash
        event = TimelineEvent(
            event_id=f"timeline-{row.index + 1:06d}",
            timestamp=row.timestamp,
            market_snapshot=MarketSnapshot(values=market_values),
            feature_snapshots=features,
            signal_evaluation=signal,
            position_snapshot=PositionSnapshot(
                position_state=_position_state(row),
                target_position=intent.target_state,
                target_positions=target_values,
                asset_positions=tuple(
                    AssetPosition(
                        symbol=symbol,
                        quantity=quantity,
                        market_value=quantity * row.market[symbol]["close"],
                    )
                    for symbol, quantity in row.portfolio.positions.items()
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
                cumulative_gross_pnl=(
                    cumulative_net_pnl
                    + row.portfolio.cumulative_fees
                    + row.portfolio.cumulative_slippage
                ),
                cumulative_net_pnl=cumulative_net_pnl,
                equity=row.portfolio.equity,
            ),
            data_dependencies=row.data_dependencies,
        )
        events.append(event)
        previous_equity = row.portfolio.equity
    timeline = tuple(events)
    trace = BacktestTrace(
        metadata=TraceMetadata(
            dataset_id=configuration.dataset_id,
            dataset_name=configuration.dataset_name,
            bar_count=len(rows),
            data_start=rows[0].timestamp,
            data_end=rows[-1].timestamp,
            execution_model=configuration.execution_model,
        ),
        strategy=StrategyDescriptor(
            strategy_id=configuration.strategy_id, name=configuration.strategy_name
        ),
        parameters=configuration.parameters,
        timeline=timeline,
        trades=_generic_trades(timeline),
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
        corporate_action_events=configuration.corporate_action_events,
    )
    return trace.model_copy(update={"diagnostics": collect_look_ahead_diagnostics(trace)})


def trace_period(rows: tuple[RuntimeRow, ...]) -> tuple[datetime, datetime]:
    if not rows:
        raise ValueError("No runtime rows")
    return rows[0].timestamp, rows[-1].timestamp
