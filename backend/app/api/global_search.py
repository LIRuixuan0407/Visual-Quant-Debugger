from typing import Annotated, cast

from fastapi import APIRouter, Query

from app.datasets import dataset_registry
from app.discovery import hypothesis_repository
from app.factor_relationships import factor_relationship_repository
from app.factors import factor_research_repository
from app.factors.registry import factor_registry
from app.global_search import (
    SEARCH_ENTITY_TYPES,
    GlobalSearchResponse,
    GlobalSearchService,
    SearchEntityType,
)
from app.portfolio_lab import portfolio_research_repository
from app.research_snapshots import research_snapshot_repository
from app.runs import run_store
from app.sdk.registry import strategy_registry
from app.strategy_drift import strategy_drift_repository
from app.walk_forward import walk_forward_repository
from app.workspaces import WorkspaceNotFoundError

from .workspaces import workspace_service

router = APIRouter(prefix="/api/search", tags=["global-search"])
_search_service: GlobalSearchService | None = None


def _service() -> GlobalSearchService:
    global _search_service
    if _search_service is None or _search_service.runs is not run_store.repository:
        _search_service = GlobalSearchService(
            dataset_registry,
            factor_registry,
            factor_research_repository,
            factor_relationship_repository,
            walk_forward_repository,
            portfolio_research_repository,
            hypothesis_repository,
            strategy_registry,
            run_store.repository,
            research_snapshot_repository,
            drift_reports=strategy_drift_repository,
        )
    return _search_service


@router.get("", response_model=GlobalSearchResponse)
def global_search(
    q: Annotated[str, Query(max_length=200)] = "",
    types: Annotated[list[SearchEntityType] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    workspace_id: str | None = None,
) -> GlobalSearchResponse:
    allowed = None
    if workspace_id is not None:
        try:
            allowed = frozenset(
                (cast(SearchEntityType, item.object_type), item.object_id)
                for item in workspace_service().memberships(workspace_id)
                if item.object_type in SEARCH_ENTITY_TYPES
            )
        except WorkspaceNotFoundError as exc:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=404, detail=f"Workspace '{workspace_id}' was not found"
            ) from exc
    return _service().search(
        q,
        entity_types=tuple(types or ()),
        limit=limit,
        allowed_entities=allowed,
    )
