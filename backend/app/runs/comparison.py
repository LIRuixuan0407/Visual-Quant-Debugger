from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal, cast

from app.trace.models import BacktestTrace, TimelineEvent, TraceScalar

from .models import (
    BehavioralDivergence,
    BehaviorDiffRow,
    Comparability,
    ContextComparison,
    EquityComparisonPoint,
    MetricComparison,
    ParameterComparison,
    RunComparisonReport,
    RunManifest,
)
from .repository import RunRepository


def _render(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _all_same(values: tuple[object, ...]) -> bool:
    return all(value == values[0] for value in values[1:])


def _period(manifest: RunManifest) -> str:
    return _render(
        {
            "start": manifest.period.start,
            "end": manifest.period.end,
            "cutoff": manifest.period.cutoff,
        }
    )


def _context(manifests: tuple[RunManifest, ...]) -> tuple[ContextComparison, ...]:
    fields: tuple[tuple[str, Callable[[RunManifest], str]], ...] = (
        ("strategy_revision", lambda item: item.strategy.source_fingerprint),
        ("dataset_revision", lambda item: item.dataset.content_fingerprint),
        ("evaluation_period", _period),
        (
            "execution_model",
            lambda item: (
                f"{item.execution_model.execution_model_id}@{item.execution_model.version}"
            ),
        ),
        (
            "runtime",
            lambda item: _render(
                {
                    "kind": item.runtime.kind,
                    "adapter_id": item.runtime.adapter_id,
                    "adapter_version": item.runtime.adapter_version,
                    "framework_name": item.runtime.framework_name,
                    "framework_version": item.runtime.framework_version,
                    "execution_owner": item.runtime.execution_owner,
                    "adapter_manifest": item.artifacts.adapter_manifest_sha256,
                }
            ),
        ),
    )
    return tuple(
        ContextComparison(
            field=cast(
                Literal[
                    "strategy_revision",
                    "dataset_revision",
                    "evaluation_period",
                    "execution_model",
                    "runtime",
                ],
                field,
            ),
            same=_all_same(values),
            values=values,
        )
        for field, projection in fields
        for values in [tuple(projection(manifest) for manifest in manifests)]
    )


def _comparability(
    manifests: tuple[RunManifest, ...], context: tuple[ContextComparison, ...]
) -> Comparability:
    if all(item.same for item in context):
        return "STRICTLY_COMPARABLE"
    runtime_kinds = tuple(item.runtime.kind for item in manifests)
    if not _all_same(runtime_kinds):
        return "DESCRIPTIVE_ONLY"
    same_strategy = _all_same(tuple(item.strategy.source_fingerprint for item in manifests))
    same_dataset = _all_same(tuple(item.dataset.content_fingerprint for item in manifests))
    return "CONTEXTUALLY_COMPARABLE" if same_strategy or same_dataset else "DESCRIPTIVE_ONLY"


def _parameter_diff(manifests: tuple[RunManifest, ...]) -> tuple[ParameterComparison, ...]:
    keys = sorted(set().union(*(manifest.parameters for manifest in manifests)))
    differences: list[ParameterComparison] = []
    for key in keys:
        values: tuple[TraceScalar | None, ...] = tuple(
            manifest.parameters.get(key) for manifest in manifests
        )
        changed = not _all_same(values)
        if changed:
            differences.append(ParameterComparison(parameter=key, values=values, changed=True))
    return tuple(differences)


def _metric_diff(manifests: tuple[RunManifest, ...]) -> tuple[MetricComparison, ...]:
    names = (
        "total_return",
        "sharpe",
        "max_drawdown",
        "turnover",
        "trades",
        "final_equity",
        "fees",
        "slippage",
    )
    result: list[MetricComparison] = []
    for name in names:
        values: tuple[float | int | None, ...] = tuple(
            None if manifest.metrics is None else getattr(manifest.metrics, name)
            for manifest in manifests
        )
        baseline = values[0]
        differences = tuple(
            None
            if index == 0 or baseline is None or value is None
            else float(value) - float(baseline)
            for index, value in enumerate(values)
        )
        result.append(
            MetricComparison(metric=name, values=values, differences_from_first=differences)
        )
    return tuple(result)


def _feature_projection(event: TimelineEvent) -> dict[str, object]:
    return {
        feature.name: {
            "value": feature.value,
            "window_start": feature.window_start,
            "window_end": feature.window_end,
        }
        for feature in event.feature_snapshots
    }


def _condition_projection(event: TimelineEvent) -> list[object]:
    return [
        {
            "left": item.left_operand,
            "left_value": item.left_value,
            "operator": item.operator,
            "right": item.right_operand,
            "right_value": item.right_value,
            "result": item.result,
        }
        for item in event.signal_evaluation.conditions
    ]


def _signal_projection(event: TimelineEvent) -> dict[str, object]:
    signal = event.signal_evaluation
    return {
        "transition": signal.signal_id is not None,
        "signal": signal.signal,
        "previous_state": signal.previous_state,
        "next_state": signal.next_state,
        "target_position": signal.target_position,
        "target_positions": signal.target_positions,
    }


def _position_projection(event: TimelineEvent) -> dict[str, object]:
    position = event.position_snapshot
    return {
        "state": position.position_state,
        "target": position.target_position,
        "positions": [
            (item.symbol, item.quantity, item.market_value) for item in position.asset_positions
        ],
        "gross": position.gross_exposure,
        "net": position.net_exposure,
    }


def _order_projection(event: TimelineEvent) -> list[object]:
    return [
        (item.symbol, item.side, item.quantity, item.expected_execution_at)
        for item in event.order_events
    ]


def _execution_projection(event: TimelineEvent) -> list[object]:
    return [
        (
            item.symbol,
            item.side,
            item.quantity,
            item.reference_price,
            item.fill_price,
            item.fee,
            item.slippage,
        )
        for item in event.execution_events
    ]


def _behavior_rows(
    traces: tuple[BacktestTrace, ...], projection: Callable[[TimelineEvent], object]
) -> tuple[BehaviorDiffRow, ...]:
    event_maps = tuple({event.timestamp: event for event in trace.timeline} for trace in traces)
    timestamps = sorted(set.intersection(*(set(items) for items in event_maps)))
    rows: list[BehaviorDiffRow] = []
    for timestamp in timestamps:
        events = tuple(items[timestamp] for items in event_maps)
        values = tuple(_render(projection(event)) for event in events)
        if not _all_same(values):
            rows.append(
                BehaviorDiffRow(
                    timestamp=timestamp,
                    values=values,
                    event_ids=tuple(event.event_id for event in events),
                )
            )
    return tuple(rows)


def _first_divergence(
    traces: tuple[BacktestTrace, BacktestTrace],
    parameter_diff: tuple[ParameterComparison, ...],
    allowed_kinds: tuple[str, ...] | None = None,
) -> BehavioralDivergence:
    first, second = traces
    second_by_time = {event.timestamp: event for event in second.timeline}
    projections: tuple[tuple[str, Callable[[TimelineEvent], object]], ...] = (
        ("FEATURE", _feature_projection),
        ("CONDITION", _condition_projection),
        ("SIGNAL", _signal_projection),
        ("POSITION", _position_projection),
        ("ORDER", _order_projection),
        ("EXECUTION", _execution_projection),
    )
    if allowed_kinds is not None:
        projections = tuple(item for item in projections if item[0] in allowed_kinds)
    for event_a in first.timeline:
        event_b = second_by_time.get(event_a.timestamp)
        if event_b is None:
            continue
        for kind, projection in projections:
            value_a = projection(event_a)
            value_b = projection(event_b)
            if value_a != value_b:
                return BehavioralDivergence(
                    status="DIVERGENCE",
                    kind=cast(
                        Literal[
                            "FEATURE",
                            "CONDITION",
                            "SIGNAL",
                            "POSITION",
                            "ORDER",
                            "EXECUTION",
                        ],
                        kind,
                    ),
                    timestamp=event_a.timestamp,
                    event_ids=(event_a.event_id, event_b.event_id),
                    summary=(
                        f"First {kind.lower()} behavior differs at {event_a.timestamp.isoformat()}"
                    ),
                    run_values=(_render(value_a), _render(value_b)),
                    associated_parameter_differences=tuple(
                        item.parameter for item in parameter_diff
                    ),
                )
    return BehavioralDivergence(
        status="NO_BEHAVIORAL_DIVERGENCE",
        kind=None,
        timestamp=None,
        event_ids=(None, None),
        summary="The two traces have no behavioral divergence.",
        run_values=(),
        associated_parameter_differences=(),
    )


def compare_run_records(
    manifests: tuple[RunManifest, ...],
    traces: tuple[BacktestTrace, ...],
) -> RunComparisonReport:
    run_ids = tuple(item.run_id for item in manifests)
    if not 2 <= len(run_ids) <= 4:
        raise ValueError("Select 2-4 runs for comparison")
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("Select distinct runs for comparison")
    if traces and len(traces) != len(manifests):
        raise ValueError("Run manifests and Traces must have matching lengths")
    context = _context(manifests)
    comparability = _comparability(manifests, context)
    parameter_diff = _parameter_diff(manifests)
    equity: tuple[EquityComparisonPoint, ...] = ()
    if traces and comparability != "DESCRIPTIVE_ONLY":
        timestamps = tuple(tuple(event.timestamp for event in trace.timeline) for trace in traces)
        if _all_same(timestamps):
            equity = tuple(
                EquityComparisonPoint(
                    timestamp=events[0].timestamp,
                    values=tuple(event.pnl_snapshot.equity for event in events),
                )
                for events in zip(*(trace.timeline for trace in traces), strict=True)
            )
    signal_rows: tuple[BehaviorDiffRow, ...] = ()
    execution_rows: tuple[BehaviorDiffRow, ...] = ()
    divergence: BehavioralDivergence | None = None
    computational: BehavioralDivergence | None = None
    decision: BehavioralDivergence | None = None
    trading: BehavioralDivergence | None = None
    if comparability == "STRICTLY_COMPARABLE" and len(traces) == len(run_ids):
        signal_rows = _behavior_rows(traces, _signal_projection)
        execution_rows = _behavior_rows(traces, _execution_projection)
        if len(traces) == 2:
            divergence = _first_divergence((traces[0], traces[1]), parameter_diff)
            capabilities = tuple(trace.metadata.runtime.trace_capabilities for trace in traces)
            if all(item.feature_values != "UNAVAILABLE" for item in capabilities):
                computational = _first_divergence(
                    (traces[0], traces[1]), parameter_diff, ("FEATURE",)
                )
            if all(item.decision_events != "UNAVAILABLE" for item in capabilities):
                decision = _first_divergence(
                    (traces[0], traces[1]), parameter_diff, ("CONDITION", "SIGNAL")
                )
            if all(
                item.orders != "UNAVAILABLE" or item.executions != "UNAVAILABLE"
                for item in capabilities
            ):
                trading = _first_divergence(
                    (traces[0], traces[1]), parameter_diff, ("ORDER", "EXECUTION")
                )
    return RunComparisonReport(
        run_ids=run_ids,
        comparability=comparability,
        context_diff=context,
        parameter_diff=parameter_diff,
        metric_diff=_metric_diff(manifests),
        equity_comparison=equity,
        signal_comparison=signal_rows,
        execution_comparison=execution_rows,
        first_behavioral_divergence=divergence,
        first_computational_divergence=computational,
        first_decision_divergence=decision,
        first_trading_divergence=trading,
    )


def compare_runs(repository: RunRepository, run_ids: tuple[str, ...]) -> RunComparisonReport:
    manifests = tuple(repository.get_manifest(run_id) for run_id in run_ids)
    traces: tuple[BacktestTrace, ...] = ()
    if all(manifest.trace_id is not None for manifest in manifests):
        traces = tuple(repository.load_trace_for_run(run_id) for run_id in run_ids)
    return compare_run_records(manifests, traces)
