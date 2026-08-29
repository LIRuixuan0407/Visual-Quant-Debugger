from fastapi import APIRouter, HTTPException

from app.api.forward import forward_store
from app.datasets import dataset_registry
from app.discovery import hypothesis_repository
from app.factors import FactorResearchEngine, factor_research_repository
from app.paper import PaperSessionManifest, PaperSessionNotFoundError, PaperTrace, paper_store
from app.research_snapshots import research_snapshot_repository
from app.runs import run_store
from app.strategy_drift import (
    CreateStrategyDriftReport,
    StrategyDriftEngine,
    StrategyDriftReport,
    StrategyDriftSummary,
    strategy_drift_repository,
)

router = APIRouter(prefix="/api/strategy-drift", tags=["strategy-drift"])


def _paper_source(session_id: str) -> tuple[PaperSessionManifest, PaperTrace] | None:
    try:
        manifest = paper_store.repository.load_manifest(session_id)
        trace = paper_store.service.trace(session_id)
    except PaperSessionNotFoundError:
        return None
    return manifest, trace


def _engine() -> StrategyDriftEngine:
    return StrategyDriftEngine(
        run_store.repository,
        research_snapshot_repository,
        forward_store.get,
        _paper_source,
        factor_research_repository,
        FactorResearchEngine(dataset_registry),
        hypothesis_repository,
    )


@router.post("", response_model=StrategyDriftReport, status_code=201)
def create_strategy_drift_report(
    request: CreateStrategyDriftReport,
) -> StrategyDriftReport:
    try:
        return strategy_drift_repository.save(_engine().build(request))
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Strategy Drift source '{exc.args[0]}' was not found",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=tuple[StrategyDriftSummary, ...])
def list_strategy_drift_reports() -> tuple[StrategyDriftSummary, ...]:
    return strategy_drift_repository.list()


@router.get("/{report_id}", response_model=StrategyDriftReport)
def get_strategy_drift_report(report_id: str) -> StrategyDriftReport:
    try:
        report = strategy_drift_repository.get(report_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"Strategy Drift report '{report_id}' was not found",
        )
    return report
