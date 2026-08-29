from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from app.api.replay import BacktestCreated, BacktestSummary
from app.datasets import dataset_registry
from app.paper import paper_store
from app.runs import (
    AnnotationUpdate,
    ArtifactIntegrityError,
    ComparisonRequest,
    RunComparisonReport,
    RunDetail,
    RunListResponse,
    RunNotFoundError,
    RunValidationReport,
    ValidationRequest,
    run_ledger,
)
from app.runs.comparison import compare_runs
from app.runs.models import RunAnnotations, RunStatus, StrategySourceArtifact
from app.runs.validation import validate_backtest_vs_paper
from app.sdk.registry import strategy_registry
from app.workspaces import WorkspaceNotFoundError

from .workspaces import workspace_service

router = APIRouter(prefix="/api", tags=["runs"])


def _not_found(run_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Run '{run_id}' was not found")


def _integrity(exc: ArtifactIntegrityError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/runs", response_model=RunListResponse)
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    strategy_id: str | None = None,
    dataset_id: str | None = None,
    status: RunStatus | None = None,
    search: str | None = None,
    workspace_id: str | None = None,
) -> RunListResponse:
    try:
        run_ids = (
            None
            if workspace_id is None
            else tuple(
                item.object_id
                for item in workspace_service().memberships(workspace_id)
                if item.object_type == "RUN"
            )
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Workspace '{exc.args[0]}' was not found"
        ) from exc
    return run_ledger.repository.list_runs(
        limit=limit,
        offset=offset,
        strategy_id=strategy_id,
        dataset_id=dataset_id,
        status=status,
        search=search,
        run_ids=run_ids,
    )


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str) -> RunDetail:
    try:
        return run_ledger.detail(run_id, strategy_registry_override=strategy_registry)
    except RunNotFoundError as exc:
        raise _not_found(run_id) from exc
    except ArtifactIntegrityError as exc:
        raise _integrity(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/runs/{run_id}/strategy-source", response_model=StrategySourceArtifact)
def get_strategy_source(run_id: str) -> StrategySourceArtifact:
    try:
        return run_ledger.repository.strategy_source(run_id)
    except RunNotFoundError as exc:
        raise _not_found(run_id) from exc
    except ArtifactIntegrityError as exc:
        raise _integrity(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/runs/{run_id}/annotations", response_model=RunAnnotations)
def update_annotations(run_id: str, update: AnnotationUpdate) -> RunAnnotations:
    try:
        return run_ledger.repository.update_annotations(run_id, update)
    except RunNotFoundError as exc:
        raise _not_found(run_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: str) -> Response:
    try:
        run_ledger.repository.delete(run_id)
    except RunNotFoundError as exc:
        raise _not_found(run_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/runs/{run_id}/rerun", response_model=BacktestCreated, status_code=201)
def rerun_exact_revision(run_id: str) -> BacktestCreated:
    try:
        result = run_ledger.reproduce(
            run_id,
            strategy_registry_override=strategy_registry,
            dataset_registry_override=dataset_registry,
        )
    except RunNotFoundError as exc:
        raise _not_found(run_id) from exc
    except ArtifactIntegrityError as exc:
        raise _integrity(exc) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    manifest = result.manifest
    trace = result.trace
    metrics = manifest.metrics
    summary = (
        None
        if trace is None or metrics is None
        else BacktestSummary(
            total_return=metrics.total_return,
            net_pnl=metrics.net_pnl,
            max_drawdown=metrics.max_drawdown,
            timeline_events=len(trace.timeline),
            signals=sum(event.signal_evaluation.signal_id is not None for event in trace.timeline),
        )
    )
    return BacktestCreated(
        run_id=manifest.run_id,
        run_fingerprint=manifest.run_fingerprint,
        trace_id=manifest.trace_id,
        trace_version="1.0",
        status=(
            "PARTIAL"
            if manifest.status == "PARTIAL"
            else "FAILED"
            if manifest.status == "FAILED"
            else "COMPLETED"
        ),
        summary=summary,
        failure=manifest.failure,
    )


@router.post("/run-comparisons", response_model=RunComparisonReport)
def create_comparison(request: ComparisonRequest) -> RunComparisonReport:
    try:
        return compare_runs(run_ledger.repository, request.run_ids)
    except RunNotFoundError as exc:
        missing = str(exc.args[0])
        raise _not_found(missing) from exc
    except ArtifactIntegrityError as exc:
        raise _integrity(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/run-validations", response_model=RunValidationReport)
def create_validation(request: ValidationRequest) -> RunValidationReport:
    try:
        return validate_backtest_vs_paper(
            run_ledger.repository,
            paper_store.service,
            request,
        )
    except RunNotFoundError as exc:
        missing = str(exc.args[0])
        raise _not_found(missing) from exc
    except ArtifactIntegrityError as exc:
        raise _integrity(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/run-validations/{report_id}", response_model=RunValidationReport)
def get_validation(report_id: str) -> RunValidationReport:
    try:
        return run_ledger.repository.load_validation(report_id)
    except RunNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Validation report '{report_id}' was not found"
        ) from exc
    except ArtifactIntegrityError as exc:
        raise _integrity(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
