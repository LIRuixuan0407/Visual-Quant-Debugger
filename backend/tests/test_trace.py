import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.backtest import BacktestParameters, run_backtest
from app.data import load_pair_csv
from app.models import BacktestResult
from app.strategies import PairsTradingParameters
from app.trace import collect_look_ahead_diagnostics, trace_from_json, trace_to_json


@pytest.fixture(scope="module")
def golden_result() -> BacktestResult:
    project_root = Path(__file__).parents[2]
    return run_backtest(
        load_pair_csv(project_root / "sample_data" / "pairs_daily.csv"),
        BacktestParameters(strategy=PairsTradingParameters(lookback=5, entry_z=1.0, exit_z=0.8)),
    )


def test_trace_is_deterministic_and_timeline_is_ordered(golden_result: BacktestResult) -> None:
    project_root = Path(__file__).parents[2]
    repeated = run_backtest(
        load_pair_csv(project_root / "sample_data" / "pairs_daily.csv"),
        BacktestParameters(strategy=PairsTradingParameters(lookback=5, entry_z=1.0, exit_z=0.8)),
    )
    assert repeated.trace == golden_result.trace
    timestamps = [event.timestamp for event in golden_result.trace.timeline]
    assert timestamps == sorted(timestamps)


def test_trace_ids_are_stable_and_unique(golden_result: BacktestResult) -> None:
    trace = golden_result.trace
    id_groups = (
        [event.event_id for event in trace.timeline],
        [feature.feature_id for event in trace.timeline for feature in event.feature_snapshots],
        [event.signal_evaluation.evaluation_id for event in trace.timeline],
        [
            event.signal_evaluation.signal_id
            for event in trace.timeline
            if event.signal_evaluation.signal_id is not None
        ],
        [order.order_id for event in trace.timeline for order in event.order_events],
        [
            execution.execution_id
            for event in trace.timeline
            for execution in event.execution_events
        ],
    )
    for identifiers in id_groups:
        assert len(identifiers) == len(set(identifiers))
    assert id_groups[0][0] == "timeline-000001"
    assert id_groups[1][0] == "feature-000001"
    assert id_groups[3][0] == "signal-0001"


def test_feature_lineage_reaches_market_windows(golden_result: BacktestResult) -> None:
    trace = golden_result.trace
    features = {
        feature.feature_id: feature
        for event in trace.timeline
        for feature in event.feature_snapshots
    }
    entry_event = next(
        event for event in trace.timeline if event.signal_evaluation.signal_id == "signal-0001"
    )
    current = {feature.name: feature for feature in entry_event.feature_snapshots}

    assert [features[item].name for item in current["zscore"].inputs] == [
        "spread",
        "rolling_mean",
        "rolling_std",
    ]
    assert len(current["rolling_mean"].inputs) == 5
    assert len(current["rolling_std"].inputs) == 5
    assert all(features[item].name == "spread" for item in current["rolling_mean"].inputs)
    assert current["spread"].inputs == (current["hedge_ratio"].feature_id,)

    dependencies = {item.dependency_id: item for item in entry_event.data_dependencies}
    spread_sources = [dependencies[item].symbol for item in current["spread"].data_dependencies]
    assert spread_sources == ["ASSET_A", "ASSET_B"]
    assert len(current["hedge_ratio"].data_dependencies) == 10
    assert current["hedge_ratio"].window_start is not None
    assert current["hedge_ratio"].window_end == entry_event.timestamp


def test_signal_lineage_and_stateful_transitions(golden_result: BacktestResult) -> None:
    trace = golden_result.trace
    entry = next(
        event for event in trace.timeline if event.signal_evaluation.signal_id == "signal-0001"
    )
    zscore = next(feature for feature in entry.feature_snapshots if feature.name == "zscore")
    condition = entry.signal_evaluation.conditions[0]
    assert entry.signal_evaluation.dependencies == (zscore.feature_id,)
    assert condition.left_value == zscore.value
    assert condition.right_value == trace.parameters["entry_z"]
    assert condition.result is True
    assert entry.signal_evaluation.previous_state == "FLAT"
    assert entry.signal_evaluation.next_state == "SHORT_SPREAD"

    exit_event = next(
        event for event in trace.timeline if event.signal_evaluation.signal_id == "signal-0002"
    )
    assert exit_event.signal_evaluation.previous_state == "SHORT_SPREAD"
    assert exit_event.signal_evaluation.next_state == "FLAT"
    assert exit_event.signal_evaluation.conditions[0].result is True


def test_order_execution_lineage_and_next_bar_timing(golden_result: BacktestResult) -> None:
    trace = golden_result.trace
    signal_events = {
        event.signal_evaluation.signal_id: (index, event)
        for index, event in enumerate(trace.timeline)
        if event.signal_evaluation.signal_id is not None
    }
    orders = {order.order_id: order for event in trace.timeline for order in event.order_events}
    for event_index, event in enumerate(trace.timeline):
        for order in event.order_events:
            signal_index, signal_event = signal_events[order.source_signal_id]
            assert event_index == signal_index + 1
            assert signal_event.signal_evaluation.decision_time < order.submitted_at
            assert order.expected_execution_at == event.timestamp
        for execution in event.execution_events:
            assert execution.source_order_id in orders
            assert execution.executed_at == event.timestamp


def test_trace_pnl_and_costs_reconcile_with_phase_one(golden_result: BacktestResult) -> None:
    for domain_row, event in zip(golden_result.timeline, golden_result.trace.timeline, strict=True):
        assert event.pnl_snapshot.equity == pytest.approx(domain_row.portfolio.equity)
        assert event.pnl_snapshot.cumulative_net_pnl == pytest.approx(
            domain_row.portfolio.equity - 100_000
        )
        assert event.cost_snapshot.fees == pytest.approx(
            sum(execution.fee for execution in domain_row.executions)
        )
        assert event.cost_snapshot.slippage == pytest.approx(
            sum(execution.slippage for execution in domain_row.executions)
        )
    final = golden_result.trace.timeline[-1]
    assert final.pnl_snapshot.cumulative_net_pnl == pytest.approx(golden_result.metrics.net_pnl)
    assert final.pnl_snapshot.cumulative_gross_pnl == pytest.approx(golden_result.metrics.gross_pnl)


def test_look_ahead_validation_detects_only_future_availability(
    golden_result: BacktestResult,
) -> None:
    trace = golden_result.trace
    assert trace.diagnostics == ()
    dependency = trace.timeline[0].data_dependencies[0]
    future_dependency = dependency.model_copy(
        update={"available_at": dependency.used_at + timedelta(days=1)}
    )
    future_event = trace.timeline[0].model_copy(
        update={"data_dependencies": (future_dependency,) + trace.timeline[0].data_dependencies[1:]}
    )
    future_trace = trace.model_copy(update={"timeline": (future_event,) + trace.timeline[1:]})
    diagnostics = collect_look_ahead_diagnostics(future_trace)
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "LOOK_AHEAD_WARNING"
    assert diagnostics[0].event_id == future_event.event_id
    assert diagnostics[0].dependency_id == dependency.dependency_id


def test_trace_json_roundtrip_preserves_semantics_and_timezone(
    golden_result: BacktestResult,
) -> None:
    payload = trace_to_json(golden_result.trace)
    restored = trace_from_json(payload)
    assert restored == golden_result.trace
    assert restored.timeline[0].timestamp.utcoffset() == timedelta(0)
    assert trace_to_json(restored) == payload


def test_trade_groups_entry_and_exit_lineage(golden_result: BacktestResult) -> None:
    first = golden_result.trace.trades[0]
    assert first.status == "CLOSED"
    assert first.entry_signal_id == "signal-0001"
    assert first.exit_signal_id == "signal-0002"
    assert first.entry_event_id == "timeline-000013"
    assert first.exit_event_id == "timeline-000017"
    assert len(first.order_ids) == 4
    assert len(first.execution_ids) == 4


def _event_projection(event: Any) -> dict[str, Any]:
    features = {feature.name: feature for feature in event.feature_snapshots}
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "market": {value.symbol: value.value for value in event.market_snapshot.values},
        "features": {
            name: features[name].value
            for name in ("hedge_ratio", "spread", "rolling_mean", "rolling_std", "zscore")
        },
        "signal": {
            "signal_id": event.signal_evaluation.signal_id,
            "action": event.signal_evaluation.signal,
            "previous_state": event.signal_evaluation.previous_state,
            "next_state": event.signal_evaluation.next_state,
            "conditions": [
                {
                    "left_value": condition.left_value,
                    "operator": condition.operator,
                    "right_value": condition.right_value,
                    "result": condition.result,
                }
                for condition in event.signal_evaluation.conditions
            ],
        },
        "orders": [
            {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side,
                "quantity": order.quantity,
                "source_signal_id": order.source_signal_id,
            }
            for order in event.order_events
        ],
        "executions": [
            {
                "execution_id": execution.execution_id,
                "source_order_id": execution.source_order_id,
                "reference_price": execution.reference_price,
                "fill_price": execution.fill_price,
                "fee": execution.fee,
                "slippage": execution.slippage,
            }
            for execution in event.execution_events
        ],
        "cost": {
            "fees": event.cost_snapshot.fees,
            "slippage": event.cost_snapshot.slippage,
        },
        "pnl": {
            "period_net_pnl": event.pnl_snapshot.period_net_pnl,
            "cumulative_net_pnl": event.pnl_snapshot.cumulative_net_pnl,
            "equity": event.pnl_snapshot.equity,
        },
    }


def _normalize_numeric(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 12)
    if isinstance(value, list):
        return [_normalize_numeric(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_numeric(item) for key, item in value.items()}
    return value


def test_human_reviewable_golden_trace(golden_result: BacktestResult) -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "golden_trace.json"
    expected: dict[str, Any] = json.loads(fixture_path.read_text(encoding="utf-8"))
    trace = golden_result.trace
    selected_ids = [event["event_id"] for event in expected["events"]]
    events = {event.event_id: event for event in trace.timeline}
    actual = {
        "trace_version": trace.trace_version,
        "dataset_id": trace.metadata.dataset_id,
        "diagnostic_count": len(trace.diagnostics),
        "trade": trace.trades[0].model_dump(mode="json"),
        "events": [_event_projection(events[event_id]) for event_id in selected_ids],
    }
    assert _normalize_numeric(actual) == _normalize_numeric(expected)
