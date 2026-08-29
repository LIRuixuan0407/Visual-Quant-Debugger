from __future__ import annotations

from collections.abc import Iterable

from fastapi import APIRouter, HTTPException, Query, Response

from app.api.forward import forward_store
from app.corporate_actions import corporate_action_repository
from app.datasets import dataset_registry
from app.discovery import hypothesis_repository
from app.factor_relationships import factor_relationship_repository
from app.factors import factor_research_repository
from app.paper import paper_store
from app.paper.repository import PaperSessionNotFoundError
from app.portfolio_lab import portfolio_research_repository
from app.research_bundles import ResearchBundleService
from app.research_snapshots import research_snapshot_repository
from app.runs import ArtifactIntegrityError, RunNotFoundError, run_store
from app.sdk.registry import strategy_registry
from app.strategy_drift import strategy_drift_repository
from app.universes import universe_repository
from app.walk_forward import walk_forward_repository
from app.workspaces import (
    AddWorkspaceMembership,
    CreateWorkspace,
    UpdateWorkspace,
    Workspace,
    WorkspaceConflictError,
    WorkspaceIntegrity,
    WorkspaceMembership,
    WorkspaceMembershipView,
    WorkspaceNotFoundError,
    WorkspaceObjectType,
    WorkspaceOverview,
    WorkspaceRepository,
    WorkspaceRepositoryError,
    WorkspaceService,
)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])
_workspace_service: WorkspaceService | None = None


def _bundle_service() -> ResearchBundleService:
    return ResearchBundleService(
        research_snapshot_repository,
        dataset_registry,
        run_store.repository,
    )


def _run_ids() -> Iterable[str]:
    offset = 0
    while True:
        page = run_store.repository.list_runs(limit=500, offset=offset)
        yield from (item.run_id for item in page.items)
        offset += len(page.items)
        if offset >= page.total or not page.items:
            return


def _asset_ids() -> Iterable[tuple[WorkspaceObjectType, str]]:
    yield from (("DATASET", item.dataset_id) for item in dataset_registry.list())
    yield from (("UNIVERSE", item.universe_id) for item in universe_repository.list())
    yield from (
        ("CORPORATE_ACTION_DATASET", item.corporate_action_dataset_id)
        for item in corporate_action_repository.list()
    )
    yield from (("FACTOR_RESEARCH", item.research_id) for item in factor_research_repository.list())
    yield from (
        ("FACTOR_RELATIONSHIP", item.relationship_id)
        for item in factor_relationship_repository.list()
    )
    yield from (("WALK_FORWARD", item.walk_forward_id) for item in walk_forward_repository.list())
    yield from (
        ("PORTFOLIO_RESEARCH", item.portfolio_research_id)
        for item in portfolio_research_repository.list()
    )
    yield from (("HYPOTHESIS", item.hypothesis_id) for item in hypothesis_repository.list())
    yield from (("STRATEGY", item.strategy_id) for item in strategy_registry.list())
    yield from (("RUN", run_id) for run_id in _run_ids())
    yield from (
        ("SNAPSHOT", snapshot_id) for snapshot_id in research_snapshot_repository.list_ids()
    )
    yield from (("FORWARD_SESSION", session_id) for session_id in forward_store.list_ids())
    yield from (
        ("PAPER_SESSION", item.session_id) for item in paper_store.repository.list_manifests()
    )
    yield from (("DRIFT_REPORT", item.drift_report_id) for item in strategy_drift_repository.list())
    yield from (
        ("ATTRIBUTION_REPORT", item.report_id) for item in run_store.repository.list_validations()
    )
    yield from (("RESEARCH_BUNDLE", bundle_id) for bundle_id in _bundle_service().list_archives())


def _asset_exists(object_type: WorkspaceObjectType, object_id: str) -> bool:
    try:
        if object_type == "DATASET":
            return dataset_registry.get(object_id) is not None
        if object_type == "UNIVERSE":
            return universe_repository.get(object_id) is not None
        if object_type == "CORPORATE_ACTION_DATASET":
            return corporate_action_repository.get(object_id) is not None
        if object_type == "FACTOR_RESEARCH":
            return factor_research_repository.get(object_id) is not None
        if object_type == "FACTOR_RELATIONSHIP":
            return factor_relationship_repository.get(object_id) is not None
        if object_type == "WALK_FORWARD":
            return walk_forward_repository.get(object_id) is not None
        if object_type == "PORTFOLIO_RESEARCH":
            return portfolio_research_repository.get(object_id) is not None
        if object_type == "HYPOTHESIS":
            return hypothesis_repository.get(object_id) is not None
        if object_type == "STRATEGY":
            strategy_registry.load(object_id)
            return True
        if object_type == "RUN":
            run_store.repository.get_manifest(object_id)
            return True
        if object_type == "SNAPSHOT":
            return research_snapshot_repository.get(object_id) is not None
        if object_type == "FORWARD_SESSION":
            return forward_store.get(object_id) is not None
        if object_type == "PAPER_SESSION":
            paper_store.repository.load_manifest(object_id)
            return True
        if object_type == "DRIFT_REPORT":
            return strategy_drift_repository.get(object_id) is not None
        if object_type == "ATTRIBUTION_REPORT":
            run_store.repository.load_validation(object_id)
            return True
        return object_id in _bundle_service().list_archives()
    except (
        ArtifactIntegrityError,
        KeyError,
        PaperSessionNotFoundError,
        RunNotFoundError,
        TypeError,
        ValueError,
    ):
        return False


def workspace_service() -> WorkspaceService:
    global _workspace_service
    workspace_root = run_store.repository.workspace_root
    if _workspace_service is None or _workspace_service.repository.workspace_root != workspace_root:
        _workspace_service = WorkspaceService(
            WorkspaceRepository(workspace_root), _asset_exists, _asset_ids
        )
    return _workspace_service


def _not_found(exc: WorkspaceNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Workspace '{exc.args[0]}' was not found")


@router.get("", response_model=tuple[Workspace, ...])
def list_workspaces(include_archived: bool = Query(default=False)) -> tuple[Workspace, ...]:
    return workspace_service().list(include_archived=include_archived)


@router.post("", response_model=Workspace, status_code=201)
def create_workspace(request: CreateWorkspace) -> Workspace:
    return workspace_service().create(request)


@router.get("/{workspace_id}", response_model=WorkspaceOverview)
def get_workspace(workspace_id: str) -> WorkspaceOverview:
    try:
        return workspace_service().overview(workspace_id)
    except WorkspaceNotFoundError as exc:
        raise _not_found(exc) from exc


@router.patch("/{workspace_id}", response_model=Workspace)
def update_workspace(workspace_id: str, request: UpdateWorkspace) -> Workspace:
    try:
        return workspace_service().update(workspace_id, request)
    except WorkspaceNotFoundError as exc:
        raise _not_found(exc) from exc
    except WorkspaceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{workspace_id}/archive", response_model=Workspace)
def archive_workspace(workspace_id: str) -> Workspace:
    try:
        return workspace_service().archive(workspace_id)
    except WorkspaceNotFoundError as exc:
        raise _not_found(exc) from exc
    except WorkspaceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{workspace_id}/restore", response_model=Workspace)
def restore_workspace(workspace_id: str) -> Workspace:
    try:
        return workspace_service().restore(workspace_id)
    except WorkspaceNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/{workspace_id}/memberships",
    response_model=tuple[WorkspaceMembershipView, ...],
)
def list_workspace_memberships(
    workspace_id: str,
) -> tuple[WorkspaceMembershipView, ...]:
    try:
        return workspace_service().memberships(workspace_id)
    except WorkspaceNotFoundError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{workspace_id}/memberships",
    response_model=WorkspaceMembership,
    status_code=201,
)
def add_workspace_membership(
    workspace_id: str, request: AddWorkspaceMembership
) -> WorkspaceMembership:
    try:
        return workspace_service().add_membership(workspace_id, request)
    except WorkspaceNotFoundError as exc:
        raise _not_found(exc) from exc
    except WorkspaceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete(
    "/{workspace_id}/memberships/{object_type}/{object_id}",
    status_code=204,
)
def remove_workspace_membership(
    workspace_id: str, object_type: WorkspaceObjectType, object_id: str
) -> Response:
    try:
        workspace_service().remove_membership(workspace_id, object_type, object_id)
    except WorkspaceNotFoundError as exc:
        raise _not_found(exc) from exc
    except WorkspaceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/{workspace_id}/integrity", response_model=WorkspaceIntegrity)
def workspace_integrity(workspace_id: str) -> WorkspaceIntegrity:
    try:
        return workspace_service().integrity(workspace_id)
    except WorkspaceNotFoundError as exc:
        raise _not_found(exc) from exc
    except WorkspaceRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
