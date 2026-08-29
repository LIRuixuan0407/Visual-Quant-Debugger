from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.datasets import dataset_registry
from app.discovery import hypothesis_repository
from app.factor_relationships import factor_relationship_repository
from app.factors import factor_research_repository
from app.portfolio_lab.repository import portfolio_research_repository
from app.research_integrity import ResearchIntegrityEngine
from app.research_ledger import research_ledger
from app.research_lineage import (
    LineageDirection,
    LineageNodeType,
    ResearchLineageBuilder,
    ResearchLineageGraph,
    ResearchLineageService,
    ResearchLineageSummary,
)
from app.research_snapshots import research_snapshot_repository
from app.runs import run_store
from app.sdk.registry import strategy_registry
from app.strategy_drift import strategy_drift_repository
from app.walk_forward import walk_forward_repository
from app.workspaces import WorkspaceNotFoundError

from .workspaces import workspace_service

router = APIRouter(prefix="/api/research-lineage", tags=["research-lineage"])


def _service() -> ResearchLineageService:
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
    return ResearchLineageService(
        ResearchLineageBuilder(
            dataset_registry,
            factor_research_repository,
            factor_relationship_repository,
            walk_forward_repository,
            portfolio_research_repository,
            hypothesis_repository,
            strategy_registry,
            run_store.repository,
            research_snapshot_repository,
            integrity,
            drift_reports=strategy_drift_repository,
        )
    )


@router.get("/summary", response_model=ResearchLineageSummary)
def research_lineage_summary() -> ResearchLineageSummary:
    return _service().summary()


@router.get("", response_model=ResearchLineageGraph)
def research_lineage(
    root_type: LineageNodeType | None = None,
    root_id: str | None = None,
    direction: LineageDirection = "BOTH",
    max_depth: Annotated[int, Query(ge=1, le=8)] = 8,
    node_types: Annotated[list[LineageNodeType] | None, Query()] = None,
    workspace_id: str | None = None,
) -> ResearchLineageGraph:
    try:
        members = (
            None
            if workspace_id is None
            else frozenset(
                (item.object_type, item.object_id)
                for item in workspace_service().memberships(workspace_id)
            )
        )
        return _service().graph(
            root_type=root_type,
            root_id=root_id,
            direction=direction,
            max_depth=max_depth,
            node_types=tuple(node_types or ()),
            workspace_members=members,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Workspace '{exc.args[0]}' was not found"
        ) from exc
    except KeyError as exc:
        detail = f"Lineage root '{exc.args[0]}' was not found"
        raise HTTPException(status_code=404, detail=detail) from exc
