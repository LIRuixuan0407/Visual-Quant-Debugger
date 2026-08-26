from fastapi import APIRouter, HTTPException

from app.datasets import dataset_registry
from app.discovery import hypothesis_repository
from app.factor_relationships import factor_relationship_repository
from app.factors import factor_research_repository
from app.portfolio_lab.repository import portfolio_research_repository
from app.research_ledger import research_ledger
from app.research_snapshots import (
    CreateResearchSnapshot,
    ExperimentComparisonReport,
    ExperimentComparisonRequest,
    ResearchSnapshot,
    ResearchSnapshotEngine,
    ResearchSnapshotSummary,
    compare_experiments,
    research_snapshot_repository,
)
from app.runs import run_store
from app.sdk.registry import strategy_registry
from app.walk_forward import walk_forward_repository

router = APIRouter(prefix="/api/research-snapshots", tags=["research-snapshots"])


def _engine() -> ResearchSnapshotEngine:
    return ResearchSnapshotEngine(
        dataset_registry,
        factor_research_repository,
        factor_relationship_repository,
        walk_forward_repository,
        hypothesis_repository,
        portfolio_research_repository,
        strategy_registry,
        run_store.repository,
        research_snapshot_repository,
        research_ledger,
    )


@router.get("", response_model=tuple[ResearchSnapshotSummary, ...])
def list_research_snapshots() -> tuple[ResearchSnapshotSummary, ...]:
    return research_snapshot_repository.list()


@router.post("/compare", response_model=ExperimentComparisonReport)
def create_experiment_comparison(
    request: ExperimentComparisonRequest,
) -> ExperimentComparisonReport:
    try:
        return compare_experiments(research_snapshot_repository, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{snapshot_id}", response_model=ResearchSnapshot)
def get_research_snapshot(snapshot_id: str) -> ResearchSnapshot:
    try:
        snapshot = research_snapshot_repository.get(snapshot_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"Research Snapshot '{snapshot_id}' was not found",
        )
    return snapshot


@router.post("", response_model=ResearchSnapshot, status_code=201)
def create_research_snapshot(request: CreateResearchSnapshot) -> ResearchSnapshot:
    try:
        return _engine().create(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
