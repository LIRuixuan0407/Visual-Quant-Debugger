from fastapi import APIRouter, HTTPException

from app.datasets import dataset_registry
from app.discovery import hypothesis_repository
from app.factor_relationships import factor_relationship_repository
from app.factors import factor_research_repository
from app.portfolio_lab.repository import portfolio_research_repository
from app.research_integrity import ResearchIntegrityEngine
from app.research_ledger import research_ledger
from app.research_snapshots import research_snapshot_repository
from app.research_workspace import (
    ResearchWorkspace,
    ResearchWorkspaceEngine,
    ResearchWorkspaceSummary,
)
from app.runs import run_store
from app.sdk.registry import strategy_registry
from app.strategy_drift import strategy_drift_repository
from app.walk_forward import walk_forward_repository

router = APIRouter(prefix="/api/research-workspaces", tags=["research-workspaces"])


def _engine() -> ResearchWorkspaceEngine:
    integrity = ResearchIntegrityEngine(
        dataset_registry,
        factor_research_repository,
        factor_relationship_repository,
        walk_forward_repository,
        hypothesis_repository,
        portfolio_research_repository,
        strategy_registry,
        run_store.repository,
        research_ledger,
    )
    return ResearchWorkspaceEngine(
        dataset_registry,
        factor_research_repository,
        factor_relationship_repository,
        walk_forward_repository,
        hypothesis_repository,
        portfolio_research_repository,
        strategy_registry,
        run_store.repository,
        research_snapshot_repository,
        integrity,
        research_ledger,
        strategy_drift_repository,
    )


@router.get("", response_model=tuple[ResearchWorkspaceSummary, ...])
def list_research_workspaces() -> tuple[ResearchWorkspaceSummary, ...]:
    return _engine().list()


@router.get("/{hypothesis_id}", response_model=ResearchWorkspace)
def get_research_workspace(hypothesis_id: str) -> ResearchWorkspace:
    try:
        return _engine().get(hypothesis_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Research Idea '{hypothesis_id}' was not found",
        ) from exc
