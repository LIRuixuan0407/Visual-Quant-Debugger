from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Literal

import numpy as np

from app.diagnostics.metrics import daily_returns, max_drawdown, sharpe
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
    SignalEvaluation,
    StrategyDescriptor,
    TimelineEvent,
    TraceMetadata,
    TraceScalar,
    TradeTrace,
)

from .models import (
    AdapterExecutionRecord,
    AdapterFeaturePoint,
    AdapterOrderRecord,
    AdapterRunResult,
    AdapterSignalPoint,
    RuntimeDescriptor,
)


def _state(quantities: dict[str, float]) -> tuple[str, Literal[-1, 0, 1]]:
    nonzero = next((quantity for quantity in quantities.values() if quantity != 0), 0.0)
    if nonzero > 0:
        return "LONG", 1
    if nonzero < 0:
        return "SHORT", -1
    return "FLAT", 0


def _normalized_metrics(result: AdapterRunResult) -> dict[str, float | int]:
    equity = tuple(point.equity for point in result.equity)
    net_pnl = equity[-1] - result.initial_equity
    traded_notional = sum(execution.quantity * execution.price for execution in result.executions)
    average_equity = float(np.mean(np.asarray(equity, dtype=np.float64)))
    total_fees = sum(execution.fee for execution in result.executions)
    total_slippage = sum(execution.slippage or 0.0 for execution in result.executions)
    return {
        "total_return": net_pnl / result.initial_equity,
        "sharpe": sharpe(daily_returns(equity, result.initial_equity)),
        "max_drawdown": max_drawdown(equity, result.initial_equity),
        "turnover": traded_notional / average_equity if average_equity > 0 else 0.0,
        "net_pnl": net_pnl,
        "gross_pnl": net_pnl + total_fees + total_slippage,
        "total_fees": total_fees,
        "total_slippage": total_slippage,
        "number_of_orders": len(result.orders),
        "number_of_trades": len(result.trades),
    }


def build_adapter_trace(result: AdapterRunResult, dataset_name: str) -> BacktestTrace:
    features_by_time: dict[datetime, list[AdapterFeaturePoint]] = defaultdict(list)
    for feature in result.features:
        features_by_time[feature.timestamp].append(feature)
    signals_by_time: dict[datetime, list[AdapterSignalPoint]] = defaultdict(list)
    for signal in result.signals:
        signals_by_time[signal.timestamp].append(signal)
    orders_by_time: dict[datetime, list[AdapterOrderRecord]] = defaultdict(list)
    for order in result.orders:
        orders_by_time[order.submitted_at].append(order)
    executions_by_time: dict[datetime, list[AdapterExecutionRecord]] = defaultdict(list)
    for execution in result.executions:
        executions_by_time[execution.executed_at].append(execution)
    positions_by_time = {point.timestamp: point for point in result.positions}
    equity_by_time = {point.timestamp: point.equity for point in result.equity}

    runtime = RuntimeDescriptor(
        kind="framework",
        adapter_id=result.adapter_id,
        adapter_version=result.adapter_version,
        framework_name=result.framework_name,
        framework_version=result.framework_version,
        execution_owner=result.execution_owner,
        trace_fidelity=result.fidelity,
        trace_capabilities=result.capabilities,
        determinism=result.determinism,
        random_seed=result.random_seed,
        historical_research_only=True,
    )
    events: list[TimelineEvent] = []
    previous_equity = result.initial_equity
    cumulative_fees = 0.0
    cumulative_slippage = 0.0
    previous_state = "FLAT"
    empty_position = {symbol: 0.0 for symbol in result.market_timeline[0].values}

    for bar_index, market in enumerate(result.market_timeline, start=1):
        timestamp = market.timestamp
        adapter_features = features_by_time[timestamp]
        feature_ids = {
            feature.name: f"feature-{bar_index:06d}-{feature_index:03d}"
            for feature_index, feature in enumerate(adapter_features, start=1)
        }
        features = tuple(
            FeatureSnapshot(
                feature_id=feature_ids[feature.name],
                name=feature.name,
                value=feature.value,
                formula=feature.formula,
                inputs=tuple(feature_ids[name] for name in feature.inputs if name in feature_ids),
                parameters={},
                window_start=None,
                window_end=None,
                available_at=timestamp,
                data_dependencies=(),
            )
            for feature in adapter_features
        )
        active_signals = [signal for signal in signals_by_time[timestamp] if signal.active]
        adapter_orders = orders_by_time[timestamp]
        if active_signals:
            signal_name = " + ".join(
                f"{signal.name}[{signal.symbol}]" if signal.symbol else signal.name
                for signal in active_signals
            )
            reason = "Explicit signal array supplied by the framework adapter entrypoint."
            signal_id = f"framework-signal-{bar_index:06d}"
        elif adapter_orders:
            signal_name = "ORDER_SUBMITTED"
            reason = "Framework order activity recorded without a verifiable decision condition."
            signal_id = f"framework-activity-{bar_index:06d}"
        else:
            signal_name = "NOT_RECORDED"
            reason = "No decision event was recorded by this framework adapter at this bar."
            signal_id = None

        position = positions_by_time.get(timestamp)
        quantities = dict(position.quantities) if position is not None else dict(empty_position)
        market_values = (
            dict(position.market_values)
            if position is not None
            else {symbol: 0.0 for symbol in quantities}
        )
        state, target = _state(quantities)
        order_events = tuple(
            OrderEvent(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                submitted_at=order.submitted_at,
                expected_execution_at=order.expected_execution_at,
                target_position=target,
                source_signal_id=signal_id or f"framework-unattributed-{bar_index:06d}",
            )
            for order in adapter_orders
        )
        adapter_executions = executions_by_time[timestamp]
        execution_events = tuple(
            ExecutionEvent(
                execution_id=execution.execution_id,
                symbol=execution.symbol,
                side=execution.side,
                quantity=execution.quantity,
                reference_price=execution.reference_price or execution.price,
                fill_price=execution.price,
                traded_notional=execution.quantity * execution.price,
                fee=execution.fee,
                slippage=execution.slippage or 0.0,
                executed_at=execution.executed_at,
                source_order_id=execution.source_order_id,
            )
            for execution in adapter_executions
        )
        fees = sum(execution.fee for execution in adapter_executions)
        slippage = sum(execution.slippage or 0.0 for execution in adapter_executions)
        cumulative_fees += fees
        cumulative_slippage += slippage
        equity = equity_by_time[timestamp]
        period_net = equity - previous_equity
        cumulative_net = equity - result.initial_equity
        events.append(
            TimelineEvent(
                event_id=f"timeline-{bar_index:06d}",
                timestamp=timestamp,
                market_snapshot=MarketSnapshot(
                    values=tuple(
                        MarketValue(
                            symbol=symbol,
                            field=field,
                            value=value,
                            dependency_id=(f"unproven-market-{bar_index:06d}-{symbol}-{field}"),
                        )
                        for symbol, fields in market.values.items()
                        for field, value in fields.items()
                    )
                ),
                feature_snapshots=features,
                signal_evaluation=SignalEvaluation(
                    evaluation_id=f"framework-evaluation-{bar_index:06d}",
                    signal_id=signal_id,
                    signal=signal_name,
                    decision_time=timestamp,
                    reason=reason,
                    conditions=(),
                    dependencies=tuple(feature.feature_id for feature in features),
                    previous_state=previous_state,
                    next_state=state,
                    target_position=target,
                    target_positions=quantities,
                ),
                position_snapshot=PositionSnapshot(
                    position_state=state,
                    target_position=target,
                    asset_positions=tuple(
                        AssetPosition(
                            symbol=symbol,
                            quantity=quantity,
                            market_value=market_values.get(symbol, 0.0),
                        )
                        for symbol, quantity in quantities.items()
                    ),
                    gross_exposure=sum(abs(value) for value in market_values.values()),
                    net_exposure=sum(market_values.values()),
                    target_positions=quantities,
                ),
                order_events=order_events,
                execution_events=execution_events,
                cost_snapshot=CostSnapshot(
                    fees=fees,
                    slippage=slippage,
                    total_cost=fees + slippage,
                    cumulative_fees=cumulative_fees,
                    cumulative_slippage=cumulative_slippage,
                ),
                pnl_snapshot=PnLSnapshot(
                    period_gross_pnl=period_net + fees + slippage,
                    period_net_pnl=period_net,
                    cumulative_gross_pnl=cumulative_net + cumulative_fees + cumulative_slippage,
                    cumulative_net_pnl=cumulative_net,
                    equity=equity,
                ),
                data_dependencies=(),
            )
        )
        previous_equity = equity
        previous_state = state

    event_id_by_time = {event.timestamp: event.event_id for event in events}
    executions_at = {
        (event.timestamp, execution.symbol): execution.execution_id
        for event in events
        for execution in event.execution_events
    }
    trades = tuple(
        TradeTrace(
            trade_id=trade.trade_id,
            direction=trade.direction,
            status=trade.status,
            entry_signal_id=f"framework-trade-entry-{trade.trade_id}",
            exit_signal_id=(
                None if trade.closed_at is None else f"framework-trade-exit-{trade.trade_id}"
            ),
            entry_event_id=event_id_by_time[trade.opened_at],
            exit_event_id=(None if trade.closed_at is None else event_id_by_time[trade.closed_at]),
            opened_at=trade.opened_at,
            closed_at=trade.closed_at,
            order_ids=(),
            execution_ids=tuple(
                execution_id
                for key in (
                    (trade.opened_at, trade.symbol),
                    *(((trade.closed_at, trade.symbol),) if trade.closed_at is not None else ()),
                )
                if (execution_id := executions_at.get(key)) is not None
            ),
        )
        for trade in result.trades
    )
    metrics = _normalized_metrics(result)
    if not math.isfinite(float(metrics["total_return"])):
        raise ValueError("Normalized adapter metrics contain a non-finite return")
    trace_parameters: dict[str, TraceScalar] = dict(result.parameters)
    return BacktestTrace(
        metadata=TraceMetadata(
            dataset_id=result.dataset_revision,
            dataset_name=dataset_name,
            bar_count=len(events),
            data_start=events[0].timestamp,
            data_end=events[-1].timestamp,
            execution_model=str(result.execution_semantics),
            runtime=runtime,
            adapter_warnings=result.warnings,
        ),
        strategy=StrategyDescriptor(
            strategy_id=result.strategy_id,
            name=result.strategy_name,
        ),
        parameters=trace_parameters,
        timeline=tuple(events),
        trades=trades,
        metrics=metrics,
        diagnostics=(),
    )
