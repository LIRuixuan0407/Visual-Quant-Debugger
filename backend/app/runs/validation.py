from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Literal, cast

from app.paper.models import PaperBrokerEvent, PaperSessionSnapshot
from app.paper.service import PaperSessionService
from app.trace.models import BacktestTrace, ExecutionEvent, TimelineEvent

from .models import (
    AttributionComponent,
    AttributionEvidenceStatus,
    Comparability,
    PnLAttribution,
    RunManifest,
    RunValidationReport,
    ValidationCheck,
    ValidationDivergence,
    ValidationRequest,
)
from .repository import RunRepository

AnyLayer = Literal["DATA", "FEATURE", "DECISION", "ORDER", "EXECUTION", "PORTFOLIO", "P&L"]


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _execution(manifest: RunManifest) -> str:
    return f"{manifest.execution_model.execution_model_id}@{manifest.execution_model.version}"


def _execution_family(manifest: RunManifest) -> str:
    execution_id = manifest.execution_model.execution_model_id
    if execution_id in {"next-close", "paper-next-close"}:
        return "NEXT_CLOSE"
    return f"{execution_id}@{manifest.execution_model.version}"


def _checks(backtest: RunManifest, paper: RunManifest) -> tuple[ValidationCheck, ...]:
    values: tuple[tuple[str, object, object], ...] = (
        (
            "strategy_revision",
            backtest.strategy.source_fingerprint,
            paper.strategy.source_fingerprint,
        ),
        ("parameters", backtest.parameters, paper.parameters),
        ("symbols", backtest.dataset.symbols, paper.dataset.symbols),
        (
            "market_path",
            backtest.dataset.content_fingerprint,
            paper.dataset.content_fingerprint,
        ),
        ("execution_model", _execution(backtest), _execution(paper)),
    )
    return tuple(
        ValidationCheck(
            field=cast(
                Literal[
                    "strategy_revision",
                    "parameters",
                    "symbols",
                    "market_path",
                    "execution_model",
                ],
                field,
            ),
            same=left == right,
            reference_value=_json(left),
            paper_value=_json(right),
        )
        for field, left, right in values
    )


def _historical_comparability(
    backtest: RunManifest, paper: RunManifest, checks: tuple[ValidationCheck, ...]
) -> Comparability:
    same_period = (
        backtest.period.start == paper.period.start and backtest.period.end == paper.period.end
    )
    if same_period and all(item.same for item in checks):
        return "STRICTLY_COMPARABLE"
    return "DESCRIPTIVE_ONLY"


def _projection(event: TimelineEvent, layer: str) -> object:
    if layer == "DATA":
        return event.market_snapshot.model_dump(mode="json")
    if layer == "FEATURE":
        return [item.model_dump(mode="json") for item in event.feature_snapshots]
    if layer == "DECISION":
        return _decision_signature(event)
    if layer == "ORDER":
        return [item.model_dump(mode="json") for item in event.order_events]
    if layer == "EXECUTION":
        return [item.model_dump(mode="json") for item in event.execution_events]
    if layer == "PORTFOLIO":
        return event.position_snapshot.model_dump(mode="json")
    return {
        "cost": event.cost_snapshot.model_dump(mode="json"),
        "pnl": event.pnl_snapshot.model_dump(mode="json"),
    }


def _first_divergence(reference: BacktestTrace, paper: BacktestTrace) -> ValidationDivergence:
    layers = ("DATA", "FEATURE", "DECISION", "ORDER", "EXECUTION", "PORTFOLIO", "P&L")
    total = max(len(reference.timeline), len(paper.timeline))
    for index in range(total):
        left = reference.timeline[index] if index < len(reference.timeline) else None
        right = paper.timeline[index] if index < len(paper.timeline) else None
        if left is None or right is None or left.timestamp != right.timestamp:
            available = left if left is not None else right
            return ValidationDivergence(
                status="DIVERGENCE",
                layer="DATA",
                timestamp=None if available is None else available.timestamp,
                reference_value="missing" if left is None else left.timestamp.isoformat(),
                paper_value="missing" if right is None else right.timestamp.isoformat(),
                difference="Recorded timelines have different event coverage",
                reference_event_id=None if left is None else left.event_id,
                paper_event_id=None if right is None else right.event_id,
            )
        for layer in layers:
            left_value = _projection(left, layer)
            right_value = _projection(right, layer)
            if left_value != right_value:
                return ValidationDivergence(
                    status="DIVERGENCE",
                    layer=cast(AnyLayer, layer),
                    timestamp=left.timestamp,
                    reference_value=_json(left_value),
                    paper_value=_json(right_value),
                    difference=f"First recorded-feed difference at {layer}",
                    reference_event_id=left.event_id,
                    paper_event_id=right.event_id,
                )
    return ValidationDivergence(
        status="MATCH",
        difference="Recorded Feed Reference and Paper match at every validation layer",
    )


def _decision_signature(event: TimelineEvent) -> object:
    signal = event.signal_evaluation
    return {
        "conditions": [
            {
                "left_operand": item.left_operand,
                "left_value": item.left_value,
                "operator": item.operator,
                "right_operand": item.right_operand,
                "right_value": item.right_value,
                "result": item.result,
            }
            for item in signal.conditions
        ],
        "previous_state": signal.previous_state,
        "next_state": signal.next_state,
        "target_position": signal.target_position,
        "target_positions": signal.target_positions,
    }


def _event_pairs(
    reference: BacktestTrace, paper: BacktestTrace
) -> tuple[tuple[TimelineEvent, TimelineEvent], ...]:
    paper_by_time = {event.timestamp: event for event in paper.timeline}
    return tuple(
        (event, paper_by_time[event.timestamp])
        for event in reference.timeline
        if event.timestamp in paper_by_time
    )


def _market_path_component(
    backtest: RunManifest,
    reference: RunManifest,
    backtest_trace: BacktestTrace | None = None,
    reference_trace: BacktestTrace | None = None,
    session: PaperSessionSnapshot | None = None,
) -> AttributionComponent:
    if backtest.metrics is None or reference.metrics is None:
        return AttributionComponent(
            layer="MARKET_PATH",
            status="INSUFFICIENT_EVIDENCE",
            summary="P&L metrics are unavailable for the historical-to-recorded-feed bridge",
        )
    same_strategy = backtest.strategy.source_fingerprint == reference.strategy.source_fingerprint
    same_parameters = backtest.parameters == reference.parameters
    same_execution = _execution_family(backtest) == _execution_family(reference)
    if not (same_strategy and same_parameters and same_execution):
        return AttributionComponent(
            layer="MARKET_PATH",
            status="INSUFFICIENT_EVIDENCE",
            summary=(
                "The historical and Recorded Feed Reference runs do not share equivalent "
                "strategy, parameters, and execution semantics"
            ),
            evidence=(
                f"strategy revision match: {same_strategy}",
                f"parameters match: {same_parameters}",
                f"execution semantics match: {same_execution}",
            ),
        )
    amount = reference.metrics.net_pnl - backtest.metrics.net_pnl
    same_path = backtest.dataset.content_fingerprint == reference.dataset.content_fingerprint
    status: AttributionEvidenceStatus = (
        "MATCH" if same_path and abs(amount) <= 1e-9 else "ATTRIBUTED"
    )
    evidence = [
        (
            f"Backtest dataset: {backtest.dataset.dataset_id} · "
            f"{backtest.dataset.content_fingerprint}"
        ),
        (
            f"Recorded Feed dataset: {reference.dataset.dataset_id} · "
            f"{reference.dataset.content_fingerprint}"
        ),
        f"Backtest net P&L: {backtest.metrics.net_pnl:.10g}",
        f"Recorded Feed Reference net P&L: {reference.metrics.net_pnl:.10g}",
        (
            "Backtest period: "
            f"{backtest.period.start.isoformat() if backtest.period.start else 'unknown'} → "
            f"{backtest.period.end.isoformat() if backtest.period.end else 'unknown'}"
        ),
        (
            "Recorded Feed period: "
            f"{reference.period.start.isoformat() if reference.period.start else 'unknown'} → "
            f"{reference.period.end.isoformat() if reference.period.end else 'unknown'}"
        ),
        (
            "Backtest data view: "
            f"{backtest.price_adjustment_policy} · universe={backtest.universe_id or 'none'} · "
            f"corporate_actions={backtest.corporate_action_dataset_id or 'none'}"
        ),
    ]
    if backtest_trace is not None:
        evidence.append(f"Backtest trace bars: {backtest_trace.metadata.bar_count}")
    if reference_trace is not None:
        evidence.append(f"Recorded Feed trace bars: {reference_trace.metadata.bar_count}")
    if session is not None:
        evidence.extend(
            (
                f"Recorded market events: {session.last_event_sequence}",
                f"Recorded corrections: {session.correction_count}",
                f"Recorded duplicates: {session.duplicate_count}",
                f"Recorded out-of-order events: {session.out_of_order_count}",
            )
        )
    return AttributionComponent(
        layer="MARKET_PATH",
        amount=amount,
        status=status,
        summary=(
            "Historical and recorded market paths reconcile to the same P&L"
            if status == "MATCH"
            else (
                "Historical-to-recorded-feed P&L bridge under matched strategy and "
                "execution semantics"
            )
        ),
        evidence=tuple(evidence),
    )


def _decision_component(
    reference_trace: BacktestTrace | None,
    paper_trace: BacktestTrace | None,
) -> AttributionComponent:
    if reference_trace is None or paper_trace is None:
        return AttributionComponent(
            layer="DECISION",
            status="INSUFFICIENT_EVIDENCE",
            summary="Decision attribution requires both Recorded Feed Reference and Paper traces",
        )
    pairs = _event_pairs(reference_trace, paper_trace)
    if not pairs:
        return AttributionComponent(
            layer="DECISION",
            status="INSUFFICIENT_EVIDENCE",
            summary="No aligned recorded-feed events are available for decision comparison",
        )
    for reference_event, paper_event in pairs:
        if _decision_signature(reference_event) != _decision_signature(paper_event):
            return AttributionComponent(
                layer="DECISION",
                status="DETECTED",
                summary=(
                    "Signal state, conditions, or target positions diverged on the "
                    "recorded market path"
                ),
                evidence=(
                    f"Reference decision: {_json(_decision_signature(reference_event))}",
                    f"Paper decision: {_json(_decision_signature(paper_event))}",
                ),
                first_divergence_at=paper_event.timestamp,
                reference_event_id=reference_event.event_id,
                paper_event_id=paper_event.event_id,
                sample_count=len(pairs),
            )
    if len(pairs) != len(reference_trace.timeline) or len(pairs) != len(paper_trace.timeline):
        return AttributionComponent(
            layer="DECISION",
            status="INSUFFICIENT_EVIDENCE",
            summary="Aligned decisions match, but recorded-feed event coverage differs",
            sample_count=len(pairs),
        )
    return AttributionComponent(
        layer="DECISION",
        amount=0.0,
        status="MATCH",
        summary="Recorded Feed Reference and Paper made the same decisions on every aligned event",
        sample_count=len(pairs),
    )


def _flatten_executions(trace: BacktestTrace) -> tuple[tuple[TimelineEvent, ExecutionEvent], ...]:
    return tuple(
        (event, execution) for event in trace.timeline for execution in event.execution_events
    )


def _execution_shape(execution: ExecutionEvent) -> tuple[str, str, float]:
    return (execution.symbol, execution.side, execution.quantity)


def _execution_pairs(
    reference_trace: BacktestTrace,
    paper_trace: BacktestTrace,
) -> tuple[tuple[TimelineEvent, ExecutionEvent, TimelineEvent, ExecutionEvent], ...] | None:
    reference = _flatten_executions(reference_trace)
    paper = _flatten_executions(paper_trace)
    if len(reference) != len(paper):
        return None
    pairs = tuple(
        (reference_event, reference_execution, paper_event, paper_execution)
        for (reference_event, reference_execution), (paper_event, paper_execution) in zip(
            reference, paper, strict=True
        )
    )
    if any(_execution_shape(left) != _execution_shape(right) for _, left, _, right in pairs):
        return None
    return pairs


def _execution_component(
    reference_trace: BacktestTrace | None,
    paper_trace: BacktestTrace | None,
    decision: AttributionComponent,
) -> AttributionComponent:
    if decision.status != "MATCH":
        return AttributionComponent(
            layer="EXECUTION_PRICE",
            status="NOT_APPLICABLE",
            summary=(
                "Execution-price attribution is not isolated because the decision path "
                "already differs"
            ),
        )
    if reference_trace is None or paper_trace is None:
        return AttributionComponent(
            layer="EXECUTION_PRICE",
            status="INSUFFICIENT_EVIDENCE",
            summary=(
                "Execution-price attribution requires both Recorded Feed Reference and Paper traces"
            ),
        )
    pairs = _execution_pairs(reference_trace, paper_trace)
    if pairs is None:
        return AttributionComponent(
            layer="EXECUTION_PRICE",
            status="DETECTED",
            summary=(
                "Execution count, symbol, side, or filled quantity differs despite "
                "matching decisions"
            ),
        )
    if not pairs:
        return AttributionComponent(
            layer="EXECUTION_PRICE",
            amount=0.0,
            status="MATCH",
            summary="Neither path produced an execution",
        )
    for reference_event, reference_execution, paper_event, paper_execution in pairs:
        if abs(reference_execution.fill_price - paper_execution.fill_price) > 1e-12:
            difference_bps = (
                (paper_execution.fill_price / reference_execution.fill_price - 1.0) * 10_000
                if reference_execution.fill_price
                else 0.0
            )
            return AttributionComponent(
                layer="EXECUTION_PRICE",
                status="DETECTED",
                summary=(
                    "Actual Paper fill price differs from the Recorded Feed Reference fill price"
                ),
                evidence=(
                    (
                        f"{paper_execution.symbol} {paper_execution.side} "
                        f"{paper_execution.quantity:.10g}"
                    ),
                    f"Reference fill: {reference_execution.fill_price:.10g}",
                    f"Paper fill: {paper_execution.fill_price:.10g}",
                    f"Price difference: {difference_bps:.4f} bps",
                ),
                first_divergence_at=paper_execution.executed_at,
                reference_event_id=reference_event.event_id,
                paper_event_id=paper_event.event_id,
                sample_count=len(pairs),
            )
    return AttributionComponent(
        layer="EXECUTION_PRICE",
        amount=0.0,
        status="MATCH",
        summary="Comparable Recorded Feed Reference and Paper fills use the same prices",
        sample_count=len(pairs),
    )


def _delay_component(
    reference_trace: BacktestTrace | None,
    paper_trace: BacktestTrace | None,
    decision: AttributionComponent,
    session: PaperSessionSnapshot,
    broker_events: tuple[PaperBrokerEvent, ...] = (),
) -> AttributionComponent:
    if decision.status != "MATCH":
        return AttributionComponent(
            layer="DELAY",
            status="NOT_APPLICABLE",
            summary="Delay attribution is not isolated because the decision path already differs",
        )
    if reference_trace is None or paper_trace is None:
        return AttributionComponent(
            layer="DELAY",
            status="INSUFFICIENT_EVIDENCE",
            summary="Delay attribution requires both Recorded Feed Reference and Paper traces",
        )
    pairs = _execution_pairs(reference_trace, paper_trace)
    if pairs is None:
        return AttributionComponent(
            layer="DELAY",
            status="INSUFFICIENT_EVIDENCE",
            summary="Execution streams cannot be paired one-for-one, so delay cannot be isolated",
        )
    if not pairs:
        return AttributionComponent(
            layer="DELAY",
            amount=0.0,
            status="MATCH",
            summary="No executions were produced, so no execution delay was introduced",
        )
    delays = tuple(
        (paper_execution.executed_at - reference_execution.executed_at).total_seconds() * 1000
        for _, reference_execution, _, paper_execution in pairs
    )
    first_index = next((index for index, delay in enumerate(delays) if abs(delay) > 1e-6), None)
    evidence = [f"Comparable execution pairs: {len(pairs)}"]
    first_reference_execution = pairs[0][1]
    source_order = next(
        (
            order
            for event in reference_trace.timeline
            for order in event.order_events
            if order.order_id == first_reference_execution.source_order_id
        ),
        None,
    )
    if source_order is not None:
        source_signal = next(
            (
                event.signal_evaluation
                for event in reference_trace.timeline
                if event.signal_evaluation.signal_id == source_order.source_signal_id
            ),
            None,
        )
        if source_signal is not None:
            evidence.append(
                f"First signal decision_time: {source_signal.decision_time.isoformat()}"
            )
        evidence.append(f"Reference order submitted_at: {source_order.submitted_at.isoformat()}")
    if session.orders:
        evidence.append(
            f"First Paper order submitted_at: {session.orders[0].submitted_at.isoformat()}"
        )
    if broker_events:
        evidence.append(
            f"First broker event occurred_at: {broker_events[0].occurred_at.isoformat()}"
        )
    if session.fills:
        evidence.append(f"First Paper fill executed_at: {session.fills[0].executed_at.isoformat()}")
    if first_index is None:
        return AttributionComponent(
            layer="DELAY",
            amount=0.0,
            status="MATCH",
            summary=(
                "Paper executions occurred at the same timestamps as the Recorded Feed Reference"
            ),
            evidence=tuple(evidence),
            sample_count=len(pairs),
            average_delay_ms=0.0,
            max_delay_ms=0.0,
        )
    reference_event, reference_execution, paper_event, paper_execution = pairs[first_index]
    evidence.extend(
        (
            f"Reference execution_at: {reference_execution.executed_at.isoformat()}",
            f"Paper execution_at: {paper_execution.executed_at.isoformat()}",
        )
    )
    return AttributionComponent(
        layer="DELAY",
        status="DETECTED",
        summary="Paper execution timing differs from the same-decision Recorded Feed Reference",
        evidence=tuple(evidence),
        first_divergence_at=paper_execution.executed_at,
        reference_event_id=reference_event.event_id,
        paper_event_id=paper_event.event_id,
        sample_count=len(pairs),
        average_delay_ms=sum(delays) / len(delays),
        max_delay_ms=max(abs(delay) for delay in delays),
    )


def _cost_component(
    layer: Literal["FEES", "SLIPPAGE"],
    reference: RunManifest,
    paper: RunManifest,
) -> AttributionComponent:
    if reference.metrics is None or paper.metrics is None:
        return AttributionComponent(
            layer=layer,
            status="INSUFFICIENT_EVIDENCE",
            summary=f"{layer.title()} attribution requires both Reference and Paper metrics",
        )
    reference_value = reference.metrics.fees if layer == "FEES" else reference.metrics.slippage
    paper_value = paper.metrics.fees if layer == "FEES" else paper.metrics.slippage
    amount = -(paper_value - reference_value)
    return AttributionComponent(
        layer=layer,
        amount=amount,
        status="MATCH" if abs(amount) <= 1e-9 else "ATTRIBUTED",
        summary=(
            f"Recorded {layer.lower()} match the Reference path"
            if abs(amount) <= 1e-9
            else f"Recorded {layer.lower()} difference from Reference to Paper"
        ),
        evidence=(
            f"Reference {layer.lower()}: {reference_value:.10g}",
            f"Paper {layer.lower()}: {paper_value:.10g}",
        ),
    )


def _numeric_amounts(components: Iterable[AttributionComponent]) -> float:
    return sum(component.amount for component in components if component.amount is not None)


def _pnl_attribution(
    backtest: RunManifest,
    reference: RunManifest,
    paper: RunManifest,
    backtest_trace: BacktestTrace | None,
    reference_trace: BacktestTrace | None,
    paper_trace: BacktestTrace | None,
    session: PaperSessionSnapshot,
    broker_events: tuple[PaperBrokerEvent, ...],
) -> PnLAttribution:
    if backtest.metrics is None or reference.metrics is None or paper.metrics is None:
        return PnLAttribution(
            total_difference=0.0,
            decision_difference=None,
            execution_price_difference=None,
            fees=0.0,
            slippage=0.0,
            residual_unattributed=0.0,
            status="NOT_AVAILABLE",
        )

    market_path = _market_path_component(
        backtest,
        reference,
        backtest_trace,
        reference_trace,
        session,
    )
    decision = _decision_component(reference_trace, paper_trace)
    execution = _execution_component(reference_trace, paper_trace, decision)
    delay = _delay_component(
        reference_trace,
        paper_trace,
        decision,
        session,
        broker_events,
    )
    fees = _cost_component("FEES", reference, paper)
    slippage = _cost_component("SLIPPAGE", reference, paper)
    known = (market_path, decision, execution, delay, fees, slippage)

    total = paper.metrics.net_pnl - backtest.metrics.net_pnl
    attributed_total = _numeric_amounts(known)
    residual_amount = total - attributed_total
    residual = AttributionComponent(
        layer="RESIDUAL",
        amount=residual_amount,
        status="MATCH" if abs(residual_amount) <= 1e-7 else "ATTRIBUTED",
        summary=(
            "Known attribution layers reconcile the total P&L gap"
            if abs(residual_amount) <= 1e-7
            else (
                "Residual is retained because the remaining P&L gap is not "
                "deterministically isolated by recorded evidence"
            )
        ),
        evidence=("Residual is never force-distributed across known attribution layers.",),
    )
    components = (*known, residual)
    unknown = any(
        component.amount is None and component.status in {"DETECTED", "INSUFFICIENT_EVIDENCE"}
        for component in known
    )
    reconciliation_error = total - attributed_total - residual_amount
    status: Literal["RECONCILED", "PARTIALLY_ATTRIBUTED", "NOT_AVAILABLE"] = (
        "RECONCILED"
        if not unknown and abs(residual_amount) <= 1e-7 and abs(reconciliation_error) <= 1e-9
        else "PARTIALLY_ATTRIBUTED"
    )
    return PnLAttribution(
        total_difference=total,
        market_path_difference=market_path.amount,
        decision_difference=decision.amount,
        execution_price_difference=execution.amount,
        delay_impact=delay.amount,
        fees=fees.amount or 0.0,
        slippage=slippage.amount or 0.0,
        residual_unattributed=residual_amount,
        attributed_total=attributed_total,
        reconciliation_error=reconciliation_error,
        components=components,
        status=status,
    )


def validate_backtest_vs_paper(
    repository: RunRepository,
    paper_service: PaperSessionService,
    request: ValidationRequest,
) -> RunValidationReport:
    backtest = repository.get_manifest(request.backtest_run_id)
    paper = repository.get_manifest(request.paper_run_id)
    if backtest.run_type != "BACKTEST" or paper.run_type != "PAPER":
        raise ValueError("Validation requires one BACKTEST Run and one PAPER Run")
    prefix = "recorded-feed:"
    if not paper.dataset.dataset_id.startswith(prefix):
        raise ValueError("The PAPER Run is not linked to a recorded market feed")
    session_id = paper.dataset.dataset_id.removeprefix(prefix)
    session = paper_service.get(session_id)
    if not session.reference_run_id:
        raise ValueError("Stop the Paper session before validation so its reference can be frozen")
    reference = repository.get_manifest(session.reference_run_id)
    backtest_trace = repository.load_trace_for_run(backtest.run_id) if backtest.trace_id else None
    reference_trace = (
        repository.load_trace_for_run(reference.run_id) if reference.trace_id else None
    )
    paper_trace = repository.load_trace_for_run(paper.run_id) if paper.trace_id else None
    checks = _checks(backtest, paper)
    divergence = (
        ValidationDivergence(status="DIVERGENCE", difference="No comparable trace was recorded")
        if reference_trace is None or paper_trace is None
        else _first_divergence(reference_trace, paper_trace)
    )
    strict_status: Literal["MATCH", "FIRST_DIVERGENCE", "NO_TRACE"] = (
        "NO_TRACE"
        if reference_trace is None or paper_trace is None
        else "MATCH"
        if divergence.status == "MATCH"
        else "FIRST_DIVERGENCE"
    )
    identity = "|".join(
        ("attribution-rule-1.0", backtest.run_id, paper.run_id, reference.run_id)
    ).encode()
    broker_events = paper_service.repository.read_broker_events(session_id)
    report = RunValidationReport(
        report_id=f"validation-{hashlib.sha256(identity).hexdigest()[:20]}",
        backtest_run_id=backtest.run_id,
        paper_run_id=paper.run_id,
        reference_run_id=reference.run_id,
        reference_trace_id=reference.trace_id,
        paper_trace_id=paper.trace_id,
        historical_comparability=_historical_comparability(backtest, paper, checks),
        strict_recorded_feed_status=strict_status,
        checks=checks,
        first_divergence=divergence,
        pnl_attribution=_pnl_attribution(
            backtest,
            reference,
            paper,
            backtest_trace,
            reference_trace,
            paper_trace,
            session,
            broker_events,
        ),
        note=(
            "Attribution uses immutable Historical Backtest, frozen Recorded Feed Reference, "
            "Paper trace, order/fill evidence, and recorded costs. Unknown or overlapping effects "
            "remain in Residual; no causal explanation is guessed."
        ),
    )
    repository.save_validation(report)
    return report
