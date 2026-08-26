from __future__ import annotations

import hashlib
import json
from typing import Literal, cast

from app.paper.service import PaperSessionService
from app.trace.models import BacktestTrace, TimelineEvent

from .models import (
    Comparability,
    PnLAttribution,
    RunManifest,
    RunValidationReport,
    ValidationCheck,
    ValidationDivergence,
    ValidationRequest,
)
from .repository import RunRepository


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _execution(manifest: RunManifest) -> str:
    return f"{manifest.execution_model.execution_model_id}@{manifest.execution_model.version}"


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
        return event.signal_evaluation.model_dump(mode="json")
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


AnyLayer = Literal["DATA", "FEATURE", "DECISION", "ORDER", "EXECUTION", "PORTFOLIO", "P&L"]


def _pnl_attribution(backtest: RunManifest, paper: RunManifest) -> PnLAttribution:
    if backtest.metrics is None or paper.metrics is None:
        return PnLAttribution(
            total_difference=0.0,
            decision_difference=None,
            execution_price_difference=None,
            fees=0.0,
            slippage=0.0,
            residual_unattributed=0.0,
            status="NOT_AVAILABLE",
        )
    total = paper.metrics.net_pnl - backtest.metrics.net_pnl
    fee_effect = -(paper.metrics.fees - backtest.metrics.fees)
    slippage_effect = -(paper.metrics.slippage - backtest.metrics.slippage)
    residual = total - fee_effect - slippage_effect
    return PnLAttribution(
        total_difference=total,
        decision_difference=None,
        execution_price_difference=None,
        fees=fee_effect,
        slippage=slippage_effect,
        residual_unattributed=residual,
        status="PARTIALLY_ATTRIBUTED",
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
    identity = "|".join((backtest.run_id, paper.run_id, reference.run_id)).encode()
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
        pnl_attribution=_pnl_attribution(backtest, paper),
        note=(
            "Historical Backtest vs Paper is descriptive unless period and market path match. "
            "Strict status compares the frozen Recorded Feed Reference with Paper."
        ),
    )
    target = repository.vqd_root / "validations"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{report.report_id}.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return report
