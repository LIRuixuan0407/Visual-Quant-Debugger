from __future__ import annotations

import math
import secrets
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.backtest import BacktestParameters, run_backtest
from app.data import load_pair_csv
from app.datasets import dataset_registry
from app.forward.engine import ForwardSession
from app.forward.models import (
    ConsistencyCheck,
    FirstDivergence,
    ForwardComparisonReport,
    ForwardSessionSnapshot,
    ForwardTrace,
    ResearchForwardMetrics,
)
from app.forward.open_session import OpenForwardSession
from app.models import BacktestResult
from app.runs import execute_open_run
from app.sdk.registry import strategy_registry
from app.strategies import PairsTradingParameters
from app.trace.models import BacktestTrace

router = APIRouter(prefix="/api/forward-sessions", tags=["forward"])


class ForwardParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lookback: int = Field(default=5, ge=2)
    entry_z: float = Field(default=1.0, gt=0)
    exit_z: float = Field(default=0.8, ge=0)
    fee_bps: float = Field(default=5.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)


class CreateForwardSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str = "pairs-trading"
    dataset_id: str = "forward-demo-v1"
    parameters: dict[str, int | float] = Field(default_factory=dict)
    research_cutoff: datetime | None = None


ForwardSessionLike = ForwardSession | OpenForwardSession


class ForwardSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, ForwardSessionLike] = {}

    def create(self, request: CreateForwardSession) -> ForwardSessionLike:
        session_id = f"forward-{secrets.token_hex(8)}"
        registration = strategy_registry.get_registration(request.strategy_id)
        if registration is not None and registration.runtime_kind == "framework":
            raise HTTPException(
                status_code=422,
                detail=(
                    "This Framework Adapter currently supports historical research runs only. "
                    "Forward and Live Paper currently support VQD Native Strategy only."
                ),
            )
        if request.strategy_id == "pairs-trading" and request.dataset_id == "forward-demo-v1":
            defaults = ForwardParameters().model_dump()
            values = {**defaults, **request.parameters}
            if float(values["exit_z"]) >= float(values["entry_z"]):
                raise HTTPException(status_code=422, detail="exit_z must be smaller than entry_z")
            root = Path(__file__).parents[3]
            bars = load_pair_csv(root / "sample_data" / "forward_pairs_daily.csv")
            parameters = BacktestParameters(
                strategy=PairsTradingParameters(
                    lookback=int(values["lookback"]),
                    entry_z=float(values["entry_z"]),
                    exit_z=float(values["exit_z"]),
                ),
                fee_bps=float(values["fee_bps"]),
                slippage_bps=float(values["slippage_bps"]),
            )
            session: ForwardSessionLike = ForwardSession(
                session_id=session_id,
                strategy_id=request.strategy_id,
                dataset_id=request.dataset_id,
                source_bars=bars,
                parameters=parameters,
                strategy_fingerprint=strategy_registry.load(request.strategy_id).source_fingerprint,
                dataset_revision="bundled:forward-demo-v1@1.0",
            )
        else:
            if request.research_cutoff is None:
                raise HTTPException(
                    status_code=422,
                    detail="research_cutoff is required for a user dataset forward holdout",
                )
            if (
                request.research_cutoff.tzinfo is None
                or request.research_cutoff.utcoffset() is None
            ):
                raise HTTPException(
                    status_code=422, detail="research_cutoff must be timezone-aware"
                )
            try:
                strategy, loaded = strategy_registry.instantiate(request.strategy_id)
                definition = dataset_registry.get(request.dataset_id)
                if definition is None:
                    raise KeyError(f"Dataset '{request.dataset_id}' was not found")
                requirements = strategy.metadata.data_requirements
                symbols = requirements.symbols
                if not symbols:
                    available = definition.symbols
                    count = requirements.symbol_count or len(available)
                    symbols = available[:count]
                frames = tuple(
                    frame
                    for frame in dataset_registry.load_frames(request.dataset_id, symbols)
                    if frame.timestamp > request.research_cutoff
                )
                if not frames:
                    raise ValueError("Forward holdout contains no synchronized bars after cutoff")
                execute_open_run(
                    strategy_id=request.strategy_id,
                    dataset_id=request.dataset_id,
                    parameters=request.parameters,
                    research_cutoff=request.research_cutoff,
                    strategy_registry=strategy_registry,
                    dataset_registry=dataset_registry,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            session = OpenForwardSession(
                session_id=session_id,
                strategy_id=request.strategy_id,
                dataset_id=request.dataset_id,
                source_frames=frames,
                strategy_class=loaded.strategy_class,
                strategy_name=strategy.metadata.name,
                strategy_version=strategy.metadata.version,
                parameters=request.parameters,
                research_cutoff=request.research_cutoff,
                strategy_fingerprint=loaded.source_fingerprint,
                dataset_revision=definition.content_fingerprint,
                fee_bps=float(request.parameters.get("fee_bps", 5.0)),
                slippage_bps=float(request.parameters.get("slippage_bps", 5.0)),
            )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> ForwardSessionLike | None:
        return self._sessions.get(session_id)


forward_store = ForwardSessionStore()


def _session_or_404(session_id: str) -> ForwardSessionLike:
    session = forward_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Forward session '{session_id}' was not found")
    return session


def _transition(
    operation: Callable[[], None], session: ForwardSessionLike
) -> ForwardSessionSnapshot:
    try:
        operation()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return session.snapshot()


@router.post("", response_model=ForwardSessionSnapshot, status_code=201)
def create_forward_session(request: CreateForwardSession) -> ForwardSessionSnapshot:
    return forward_store.create(request).snapshot()


@router.get("/{session_id}", response_model=ForwardSessionSnapshot)
def get_forward_session(session_id: str) -> ForwardSessionSnapshot:
    return _session_or_404(session_id).snapshot()


@router.post("/{session_id}/start", response_model=ForwardSessionSnapshot)
def start_forward_session(session_id: str) -> ForwardSessionSnapshot:
    session = _session_or_404(session_id)
    return _transition(session.start, session)


@router.post("/{session_id}/pause", response_model=ForwardSessionSnapshot)
def pause_forward_session(session_id: str) -> ForwardSessionSnapshot:
    session = _session_or_404(session_id)
    return _transition(session.pause, session)


@router.post("/{session_id}/resume", response_model=ForwardSessionSnapshot)
def resume_forward_session(session_id: str) -> ForwardSessionSnapshot:
    session = _session_or_404(session_id)
    return _transition(session.resume, session)


@router.post("/{session_id}/stop", response_model=ForwardSessionSnapshot)
def stop_forward_session(session_id: str) -> ForwardSessionSnapshot:
    session = _session_or_404(session_id)
    return _transition(session.stop, session)


@router.post("/{session_id}/step", response_model=ForwardSessionSnapshot)
def step_forward_session(session_id: str) -> ForwardSessionSnapshot:
    session = _session_or_404(session_id)
    return _transition(session.step, session)


@router.get("/{session_id}/trace", response_model=ForwardTrace)
def get_forward_trace(session_id: str) -> ForwardTrace:
    return _session_or_404(session_id).trace()


def _metric_projection(result: BacktestResult, label: str) -> ResearchForwardMetrics:
    trace = result.trace
    return ResearchForwardMetrics(
        period_label=label,
        total_return=result.metrics.total_return,
        sharpe=result.metrics.sharpe,
        max_drawdown=result.metrics.max_drawdown,
        turnover=result.metrics.turnover,
        trades=len(trace.trades),
        fees=result.metrics.total_fees,
        slippage=result.metrics.total_slippage,
        final_equity=trace.timeline[-1].pnl_snapshot.equity,
    )


def _forward_projection(session: ForwardSession) -> ResearchForwardMetrics:
    summary = session.summary()
    if not session.rows:
        return ResearchForwardMetrics(
            period_label="Forward holdout",
            total_return=0.0,
            sharpe=0.0,
            max_drawdown=0.0,
            turnover=0.0,
            trades=0,
            fees=0.0,
            slippage=0.0,
            final_equity=session.parameters.initial_cash,
        )
    from app.diagnostics import calculate_metrics

    metrics = calculate_metrics(tuple(session.rows), session.parameters.initial_cash)
    return ResearchForwardMetrics(
        period_label="Forward holdout",
        total_return=metrics.total_return,
        sharpe=metrics.sharpe,
        max_drawdown=metrics.max_drawdown,
        turnover=metrics.turnover,
        trades=summary.closed_trade_count + summary.open_trade_count,
        fees=metrics.total_fees,
        slippage=metrics.total_slippage,
        final_equity=summary.final_equity,
    )


def _trace_projection(trace: BacktestTrace, label: str) -> ResearchForwardMetrics:
    metrics = trace.metrics
    return ResearchForwardMetrics(
        period_label=label,
        total_return=float(metrics["total_return"]),
        sharpe=float(metrics["sharpe"]),
        max_drawdown=float(metrics["max_drawdown"]),
        turnover=float(metrics["turnover"]),
        trades=len(trace.trades),
        fees=float(metrics["total_fees"]),
        slippage=float(metrics["total_slippage"]),
        final_equity=trace.timeline[-1].pnl_snapshot.equity,
    )


def _open_forward_projection(session: OpenForwardSession) -> ResearchForwardMetrics:
    summary = session.summary()
    if not session.runtime.rows:
        return ResearchForwardMetrics(
            period_label="Forward holdout",
            total_return=0.0,
            sharpe=0.0,
            max_drawdown=0.0,
            turnover=0.0,
            trades=0,
            fees=0.0,
            slippage=0.0,
            final_equity=session.initial_cash,
        )
    trace = session.same_path_batch()
    if trace is None:
        raise RuntimeError("A processed open forward session must have a batch trace")
    projection = _trace_projection(trace, "Forward holdout")
    return projection.model_copy(
        update={
            "trades": summary.closed_trade_count + summary.open_trade_count,
            "fees": summary.fees,
            "slippage": summary.slippage,
            "final_equity": summary.final_equity,
        }
    )


def _numeric_check(
    field: str, batch: float | int, forward: float | int, tolerance: float = 1e-9
) -> ConsistencyCheck:
    difference = float(forward) - float(batch)
    return ConsistencyCheck(
        field=field,
        batch_value=batch,
        forward_value=forward,
        difference=difference,
        status="MATCH"
        if math.isclose(float(batch), float(forward), rel_tol=0.0, abs_tol=tolerance)
        else "DIVERGENCE",
    )


def _sequence_check(field: str, batch: list[str], forward: list[str]) -> ConsistencyCheck:
    return ConsistencyCheck(
        field=field,
        batch_value=" | ".join(batch),
        forward_value=" | ".join(forward),
        difference=None,
        status="MATCH" if batch == forward else "DIVERGENCE",
    )


def _open_comparison(session: OpenForwardSession) -> ForwardComparisonReport:
    research_result = execute_open_run(
        strategy_id=session.strategy_id,
        dataset_id=session.dataset_id,
        parameters=session.parameters,
        research_cutoff=session.research_cutoff,
        strategy_registry=strategy_registry,
        dataset_registry=dataset_registry,
    )
    if research_result.trace is None:
        raise HTTPException(status_code=422, detail="Research segment failed before tracing")
    same_path = session.same_path_batch()
    research_metrics = _trace_projection(research_result.trace, "Historical research")
    forward_metrics = _open_forward_projection(session)
    if same_path is None:
        checks: tuple[ConsistencyCheck, ...] = ()
    else:
        batch_signals = [
            f"{event.timestamp.isoformat()}:{event.signal_evaluation.signal}"
            for event in same_path.timeline
            if event.signal_evaluation.signal_id
        ]
        forward_signals = [
            f"{event.timestamp.isoformat()}:{event.signal_evaluation.signal}"
            for event in session.events
            if event.signal_evaluation.signal_id
        ]
        batch_orders = [
            f"{order.submitted_at.isoformat()}:{order.symbol}:{order.side}:"
            f"{order.quantity:.10f}:{order.source_signal_id}"
            for event in same_path.timeline
            for order in event.order_events
        ]
        forward_orders = [
            f"{order.submitted_at.isoformat()}:{order.symbol}:{order.side}:"
            f"{order.quantity:.10f}:{order.source_signal_id}"
            for event in session.events
            for order in event.order_events
        ]
        batch_executions = [
            f"{item.executed_at.isoformat()}:{item.symbol}:{item.side}:"
            f"{item.quantity:.10f}:{item.fill_price:.10f}"
            for event in same_path.timeline
            for item in event.execution_events
        ]
        forward_executions = [
            f"{item.executed_at.isoformat()}:{item.symbol}:{item.side}:"
            f"{item.quantity:.10f}:{item.fill_price:.10f}"
            for event in session.events
            for item in event.execution_events
        ]
        batch_positions = [
            f"{item.symbol}:{item.quantity:.10f}"
            for item in same_path.timeline[-1].position_snapshot.asset_positions
        ]
        forward_positions = [
            f"{symbol}:{quantity:.10f}"
            for symbol, quantity in session.runtime.portfolio.positions.items()
        ]
        summary = session.summary()
        checks = (
            _sequence_check("signals", batch_signals, forward_signals),
            _sequence_check("orders", batch_orders, forward_orders),
            _sequence_check("executions", batch_executions, forward_executions),
            _sequence_check("positions", batch_positions, forward_positions),
            _numeric_check("fees", float(same_path.metrics["total_fees"]), summary.fees),
            _numeric_check(
                "slippage", float(same_path.metrics["total_slippage"]), summary.slippage
            ),
            _numeric_check(
                "net_pnl",
                float(same_path.metrics["net_pnl"]),
                summary.final_equity - summary.initial_equity,
            ),
            _numeric_check(
                "final_equity",
                same_path.timeline[-1].pnl_snapshot.equity,
                summary.final_equity,
            ),
        )
    status: Literal["MATCH", "DIVERGENCE"] = (
        "MATCH" if all(item.status == "MATCH" for item in checks) else "DIVERGENCE"
    )
    first_check = next((item for item in checks if item.status == "DIVERGENCE"), None)
    first = (
        None
        if first_check is None
        else FirstDivergence(
            field=first_check.field,
            batch_value=first_check.batch_value,
            forward_value=first_check.forward_value,
        )
    )
    return ForwardComparisonReport(
        session_id=session.session_id,
        research=research_metrics,
        forward=forward_metrics,
        consistency=checks,
        consistency_status=status,
        first_divergence=first,
    )


@router.get("/{session_id}/comparison", response_model=ForwardComparisonReport)
def get_forward_comparison(session_id: str) -> ForwardComparisonReport:
    session = _session_or_404(session_id)
    if isinstance(session, OpenForwardSession):
        return _open_comparison(session)
    root = Path(__file__).parents[3]
    research = run_backtest(
        load_pair_csv(root / "sample_data" / "pairs_daily.csv"), session.parameters
    )
    same_path = session.same_path_batch()
    research_metrics = _metric_projection(research, "Historical research")
    forward_metrics = _forward_projection(session)
    if same_path is None:
        checks: tuple[ConsistencyCheck, ...] = ()
        first = None
        status: Literal["MATCH", "DIVERGENCE"] = "MATCH"
    else:
        batch_signals = [
            f"{event.timestamp.isoformat()}:{event.signal_evaluation.signal}"
            for event in same_path.trace.timeline
            if event.signal_evaluation.signal_id
        ]
        forward_signals = [
            f"{event.timestamp.isoformat()}:{event.signal_evaluation.signal}"
            for event in session.events
            if event.signal_evaluation.signal_id
        ]
        batch_orders = [
            f"{order.submitted_at.isoformat()}:{order.symbol}:{order.side}:{order.quantity:.10f}:{order.source_signal_id}"
            for event in same_path.trace.timeline
            for order in event.order_events
        ]
        forward_orders = [
            f"{order.submitted_at.isoformat()}:{order.symbol}:{order.side}:{order.quantity:.10f}:{order.source_signal_id}"
            for event in session.events
            for order in event.order_events
        ]
        batch_exec = [
            f"{ex.executed_at.isoformat()}:{ex.symbol}:{ex.side}:{ex.quantity:.10f}:{ex.fill_price:.10f}"
            for event in same_path.trace.timeline
            for ex in event.execution_events
        ]
        forward_exec = [
            f"{ex.executed_at.isoformat()}:{ex.symbol}:{ex.side}:{ex.quantity:.10f}:{ex.fill_price:.10f}"
            for event in session.events
            for ex in event.execution_events
        ]
        s = session.summary()
        batch_final = same_path.trace.timeline[-1].position_snapshot.asset_positions
        checks = (
            _sequence_check("signals", batch_signals, forward_signals),
            _sequence_check("orders", batch_orders, forward_orders),
            _sequence_check("executions", batch_exec, forward_exec),
            _numeric_check("fees", same_path.metrics.total_fees, s.fees),
            _numeric_check("slippage", same_path.metrics.total_slippage, s.slippage),
            _numeric_check("net_pnl", same_path.metrics.net_pnl, s.final_equity - s.initial_equity),
            _numeric_check(
                "final_equity", same_path.trace.timeline[-1].pnl_snapshot.equity, s.final_equity
            ),
            _numeric_check("quantity_a", batch_final[0].quantity, session.portfolio.quantity_a),
            _numeric_check("quantity_b", batch_final[1].quantity, session.portfolio.quantity_b),
        )
        status = "MATCH" if all(check.status == "MATCH" for check in checks) else "DIVERGENCE"
        first_check = next((check for check in checks if check.status == "DIVERGENCE"), None)
        first = (
            None
            if first_check is None
            else FirstDivergence(
                field=first_check.field,
                batch_value=first_check.batch_value,
                forward_value=first_check.forward_value,
            )
        )
    return ForwardComparisonReport(
        session_id=session_id,
        research=research_metrics,
        forward=forward_metrics,
        consistency=checks,
        consistency_status=status,
        first_divergence=first,
    )
