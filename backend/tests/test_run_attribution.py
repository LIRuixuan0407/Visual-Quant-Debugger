from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_forward import _bars, _parameters

from app.forward.engine import ForwardSession
from app.runs.models import AttributionComponent
from app.runs.validation import (
    _cost_component,
    _decision_component,
    _delay_component,
    _execution_component,
    _first_divergence,
    _market_path_component,
)


def _trace():  # type: ignore[no-untyped-def]
    session = ForwardSession(
        "attribution", "pairs-trading", "forward-demo-v1", _bars(), _parameters()
    )
    session.start()
    while session.status == "RUNNING":
        session.step()
    batch = session.same_path_batch()
    assert batch is not None
    assert batch.trace.timeline
    return batch.trace


def _replace_event(trace, index, event):  # type: ignore[no-untyped-def]
    timeline = list(trace.timeline)
    timeline[index] = event
    return trace.model_copy(update={"timeline": tuple(timeline)})


def _execution_index(trace) -> int:  # type: ignore[no-untyped-def]
    return next(index for index, event in enumerate(trace.timeline) if event.execution_events)


def _manifest_like(
    net_pnl: float,
    *,
    fees: float = 0.0,
    slippage: float = 0.0,
    dataset: str = "a",
):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        metrics=SimpleNamespace(net_pnl=net_pnl, fees=fees, slippage=slippage),
        strategy=SimpleNamespace(source_fingerprint="sha256:strategy"),
        parameters={"lookback": 5},
        execution_model=SimpleNamespace(execution_model_id="next-close", version="1.0"),
        dataset=SimpleNamespace(
            dataset_id=f"dataset-{dataset}",
            content_fingerprint=f"sha256:{dataset}",
        ),
        period=SimpleNamespace(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 31, tzinfo=UTC),
        ),
        price_adjustment_policy="RAW",
        universe_id=None,
        corporate_action_dataset_id=None,
    )


def test_market_path_bridge_uses_reference_run_without_guessing_other_layers() -> None:
    backtest = _manifest_like(100.0, dataset="historical")
    reference = _manifest_like(145.0, dataset="recorded")

    component = _market_path_component(backtest, reference)  # type: ignore[arg-type]

    assert component.status == "ATTRIBUTED"
    assert component.amount == pytest.approx(45.0)
    assert component.layer == "MARKET_PATH"


def test_recorded_fee_and_slippage_effects_are_reference_to_paper_deltas() -> None:
    reference = _manifest_like(100.0, fees=10.0, slippage=5.0)
    paper = _manifest_like(80.0, fees=14.0, slippage=8.5)

    fees = _cost_component("FEES", reference, paper)  # type: ignore[arg-type]
    slippage = _cost_component("SLIPPAGE", reference, paper)  # type: ignore[arg-type]

    assert fees.amount == pytest.approx(-4.0)
    assert slippage.amount == pytest.approx(-3.5)
    assert fees.status == "ATTRIBUTED"
    assert slippage.status == "ATTRIBUTED"


def test_decision_difference_is_detected_without_inventing_pnl_amount() -> None:
    reference = _trace()
    index = next(
        index
        for index, event in enumerate(reference.timeline)
        if event.signal_evaluation.signal_id is not None
    )
    event = reference.timeline[index]
    changed_signal = event.signal_evaluation.model_copy(
        update={
            "next_state": f"{event.signal_evaluation.next_state}-changed",
            "target_position": 0,
        }
    )
    paper = _replace_event(
        reference, index, event.model_copy(update={"signal_evaluation": changed_signal})
    )

    component = _decision_component(reference, paper)

    assert component.layer == "DECISION"
    assert component.status == "DETECTED"
    assert component.amount is None
    assert component.reference_event_id == event.event_id
    assert component.paper_event_id == event.event_id


def test_execution_price_difference_is_only_isolated_when_decisions_match() -> None:
    reference = _trace()
    decision = _decision_component(reference, reference)
    assert decision.status == "MATCH"
    index = _execution_index(reference)
    event = reference.timeline[index]
    execution = event.execution_events[0]
    changed_execution = execution.model_copy(update={"fill_price": execution.fill_price + 1.0})
    paper = _replace_event(
        reference,
        index,
        event.model_copy(
            update={"execution_events": (changed_execution, *event.execution_events[1:])}
        ),
    )

    component = _execution_component(reference, paper, decision)

    assert component.status == "DETECTED"
    assert component.amount is None
    assert any("Price difference" in item for item in component.evidence)

    blocked = _execution_component(
        reference,
        paper,
        AttributionComponent(
            layer="DECISION",
            status="DETECTED",
            summary="decision changed",
        ),
    )
    assert blocked.status == "NOT_APPLICABLE"


def test_delay_uses_recorded_execution_timestamps_and_keeps_effect_unquantified() -> None:
    reference = _trace()
    decision = _decision_component(reference, reference)
    index = _execution_index(reference)
    event = reference.timeline[index]
    execution = event.execution_events[0]
    changed_execution = execution.model_copy(
        update={"executed_at": execution.executed_at + timedelta(seconds=2)}
    )
    paper = _replace_event(
        reference,
        index,
        event.model_copy(
            update={"execution_events": (changed_execution, *event.execution_events[1:])}
        ),
    )

    component = _delay_component(
        reference,
        paper,
        decision,
        SimpleNamespace(orders=(), fills=()),  # type: ignore[arg-type]
    )

    assert component.status == "DETECTED"
    assert component.amount is None
    assert component.max_delay_ms == pytest.approx(2_000.0)
    assert component.first_divergence_at == changed_execution.executed_at


def test_first_divergence_keeps_exact_replay_event_ids() -> None:
    reference = _trace()
    index = _execution_index(reference)
    event = reference.timeline[index]
    execution = event.execution_events[0]
    changed_execution = execution.model_copy(update={"fill_price": execution.fill_price + 1.0})
    paper = _replace_event(
        reference,
        index,
        event.model_copy(
            update={"execution_events": (changed_execution, *event.execution_events[1:])}
        ),
    )

    divergence = _first_divergence(reference, paper)

    assert divergence.status == "DIVERGENCE"
    assert divergence.layer == "EXECUTION"
    assert divergence.reference_event_id == event.event_id
    assert divergence.paper_event_id == event.event_id
