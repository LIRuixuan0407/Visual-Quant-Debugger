from fastapi import APIRouter, HTTPException

from app.datasets import dataset_registry
from app.discovery import hypothesis_repository
from app.factor_relationships import factor_relationship_repository
from app.factors import factor_research_repository
from app.portfolio_lab.repository import portfolio_research_repository
from app.research_integrity import (
    HypothesisIntegrityReport,
    ResearchIntegrityEngine,
    WorkspaceIntegrityReport,
)
from app.research_ledger import research_ledger
from app.runs import run_store
from app.sdk.registry import strategy_registry
from app.walk_forward import walk_forward_repository

router = APIRouter(prefix="/api/research-integrity", tags=["research-integrity"])


def _engine() -> ResearchIntegrityEngine:
    return ResearchIntegrityEngine(
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


@router.get("", response_model=WorkspaceIntegrityReport)
def workspace_integrity() -> WorkspaceIntegrityReport:
    return _engine().overview()


@router.get("/{hypothesis_id}", response_model=HypothesisIntegrityReport)
def hypothesis_integrity(hypothesis_id: str) -> HypothesisIntegrityReport:
    try:
        return _engine().audit(hypothesis_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Hypothesis '{hypothesis_id}' was not found",
        ) from exc
