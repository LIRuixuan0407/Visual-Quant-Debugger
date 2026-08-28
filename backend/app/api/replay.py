from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.adapters.models import RuntimeDescriptor
from app.backtest import BacktestParameters
from app.corporate_actions.models import PriceAdjustmentPolicy
from app.datasets import dataset_registry
from app.runs import ArtifactIntegrityError, RunNotFoundError, run_ledger
from app.sdk.models import RuntimeFailure
from app.sdk.registry import strategy_registry
from app.trace import BacktestTrace

router = APIRouter(prefix="/api", tags=["replay"])

ENGINE_DEFAULTS = BacktestParameters()


class ReplayParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback: int = Field(default=ENGINE_DEFAULTS.strategy.lookback, ge=2)
    entry_z: float = Field(default=ENGINE_DEFAULTS.strategy.entry_z, gt=0)
    exit_z: float = Field(default=ENGINE_DEFAULTS.strategy.exit_z, ge=0)
    fee_bps: float = Field(default=ENGINE_DEFAULTS.fee_bps, ge=0)
    slippage_bps: float = Field(default=ENGINE_DEFAULTS.slippage_bps, ge=0)


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str | None = None
    strategy_id: str | None = None
    dataset_id: str | None = None
    parameters: dict[str, int | float] = Field(default_factory=dict)
    research_cutoff: datetime | None = None
    universe_id: str | None = None
    corporate_action_dataset_id: str | None = None
    price_adjustment_policy: PriceAdjustmentPolicy = "RAW"

    @model_validator(mode="after")
    def require_one_strategy_id(self) -> "BacktestRequest":
        if self.strategy and self.strategy_id and self.strategy != self.strategy_id:
            raise ValueError("strategy and strategy_id must match when both are supplied")
        return self


class BacktestSummary(BaseModel):
    total_return: float
    net_pnl: float
    max_drawdown: float
    timeline_events: int
    signals: int


class BacktestCreated(BaseModel):
    run_id: str
    run_fingerprint: str
    trace_id: str | None
    trace_version: Literal["1.0"]
    status: Literal["COMPLETED", "FAILED", "PARTIAL"] = "COMPLETED"
    summary: BacktestSummary | None
    failure: RuntimeFailure | None = None


class RunContext(BaseModel):
    run_id: str
    trace_id: str
    strategy_id: str
    strategy_version: str
    strategy_fingerprint: str
    dataset_id: str
    dataset_fingerprint: str
    parameters: dict[str, int | float]
    execution_model: str
    execution_model_id: str
    execution_model_version: str
    created_at: datetime
    research_cutoff: datetime | None
    status: Literal["COMPLETED", "PARTIAL"]
    runtime: RuntimeDescriptor


@router.post("/backtests", response_model=BacktestCreated, status_code=201)
def create_backtest(request: BacktestRequest) -> BacktestCreated:
    strategy_id = request.strategy_id or request.strategy or "pairs-trading"
    dataset_id = request.dataset_id or "pairs-sample-v1"
    defaults = ReplayParameters().model_dump()
    values = (
        {**defaults, **request.parameters} if strategy_id == "pairs-trading" else request.parameters
    )
    try:
        persisted = run_ledger.create(
            strategy_id=strategy_id,
            dataset_id=dataset_id,
            parameters=values,
            research_cutoff=request.research_cutoff,
            strategy_registry_override=strategy_registry,
            dataset_registry_override=dataset_registry,
            universe_id=request.universe_id,
            corporate_action_dataset_id=request.corporate_action_dataset_id,
            price_adjustment_policy=request.price_adjustment_policy,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    manifest = persisted.manifest
    trace = persisted.trace
    if trace is None:
        return BacktestCreated(
            run_id=manifest.run_id,
            run_fingerprint=manifest.run_fingerprint,
            trace_id=None,
            trace_version="1.0",
            status="FAILED",
            summary=None,
            failure=manifest.failure,
        )
    signal_count = sum(event.signal_evaluation.signal_id is not None for event in trace.timeline)
    metrics = manifest.metrics
    if metrics is None:
        raise RuntimeError("A trace-bearing run must have metrics")
    return BacktestCreated(
        run_id=manifest.run_id,
        run_fingerprint=manifest.run_fingerprint,
        trace_id=manifest.trace_id,
        trace_version=trace.trace_version,
        status="PARTIAL" if manifest.status == "PARTIAL" else "COMPLETED",
        summary=BacktestSummary(
            total_return=metrics.total_return,
            net_pnl=metrics.net_pnl,
            max_drawdown=metrics.max_drawdown,
            timeline_events=len(trace.timeline),
            signals=signal_count,
        ),
        failure=manifest.failure,
    )


@router.get("/traces/{trace_id}", response_model=BacktestTrace)
def get_trace(trace_id: str) -> BacktestTrace:
    try:
        trace = run_ledger.repository.load_trace(trace_id)
    except ArtifactIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' was not found")
    return trace


@router.get("/traces/{trace_id}/context", response_model=RunContext)
def get_run_context(trace_id: str) -> RunContext:
    run_id = run_ledger.repository.run_id_for_trace(trace_id)
    if run_id is None:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' was not found")
    try:
        manifest = run_ledger.repository.get_manifest(run_id)
    except (ArtifactIntegrityError, RunNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RunContext(
        run_id=run_id,
        trace_id=trace_id,
        strategy_id=manifest.strategy.strategy_id,
        strategy_version=manifest.strategy.version,
        strategy_fingerprint=manifest.strategy.source_fingerprint,
        dataset_id=manifest.dataset.dataset_id,
        dataset_fingerprint=manifest.dataset.content_fingerprint,
        parameters=manifest.parameters,
        execution_model=manifest.execution_model.description,
        execution_model_id=manifest.execution_model.execution_model_id,
        execution_model_version=manifest.execution_model.version,
        created_at=manifest.created_at,
        research_cutoff=manifest.period.cutoff,
        status="PARTIAL" if manifest.status == "PARTIAL" else "COMPLETED",
        runtime=manifest.runtime,
    )
