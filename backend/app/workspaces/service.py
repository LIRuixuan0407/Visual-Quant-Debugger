from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from .models import (
    DEFAULT_WORKSPACE_ID,
    WORKSPACE_OBJECT_TYPES,
    AddWorkspaceMembership,
    CreateWorkspace,
    UpdateWorkspace,
    Workspace,
    WorkspaceIntegrity,
    WorkspaceMembership,
    WorkspaceMembershipView,
    WorkspaceMigrationRecord,
    WorkspaceObjectType,
    WorkspaceOverview,
    WorkspaceStatistics,
)
from .repository import WorkspaceNotFoundError, WorkspaceRepository

AssetExists = Callable[[WorkspaceObjectType, str], bool]
EnumerateAssets = Callable[[], Iterable[tuple[WorkspaceObjectType, str]]]


class WorkspaceConflictError(ValueError):
    pass


class WorkspaceService:
    def __init__(
        self,
        repository: WorkspaceRepository,
        asset_exists: AssetExists,
        enumerate_assets: EnumerateAssets,
    ) -> None:
        self.repository = repository
        self.asset_exists = asset_exists
        self.enumerate_assets = enumerate_assets

    def ensure_default_workspace(self) -> Workspace:
        now = datetime.now(UTC)
        try:
            default = self.repository.get(DEFAULT_WORKSPACE_ID)
        except WorkspaceNotFoundError:
            default = self.repository.create(
                Workspace(
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    name="Default Workspace",
                    description="Existing and uncategorized research assets",
                    created_at=now,
                    updated_at=now,
                    is_default=True,
                )
            )
        if self.repository.migration() is not None:
            return default
        existing = self.repository.memberships(DEFAULT_WORKSPACE_ID)
        by_key = {(item.object_type, item.object_id): item for item in existing}
        for object_type, object_id in self.enumerate_assets():
            by_key.setdefault(
                (object_type, object_id),
                WorkspaceMembership(
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    object_type=object_type,
                    object_id=object_id,
                    added_at=now,
                ),
            )
        migrated = self.repository.save_memberships(DEFAULT_WORKSPACE_ID, tuple(by_key.values()))
        if len(migrated) != len(existing):
            default = self.repository.save(default.model_copy(update={"updated_at": now}))
        self.repository.save_migration(
            WorkspaceMigrationRecord(
                completed_at=now,
                migrated_membership_count=len(migrated),
            )
        )
        return default

    def list(self, *, include_archived: bool = False) -> tuple[Workspace, ...]:
        self.ensure_default_workspace()
        return tuple(
            item for item in self.repository.list() if include_archived or item.archived_at is None
        )

    def create(self, request: CreateWorkspace) -> Workspace:
        self.ensure_default_workspace()
        now = datetime.now(UTC)
        return self.repository.create(
            Workspace(
                workspace_id=self.repository.new_workspace_id(),
                name=request.name.strip(),
                description=(
                    None if request.description is None else request.description.strip() or None
                ),
                created_at=now,
                updated_at=now,
            )
        )

    def update(self, workspace_id: str, request: UpdateWorkspace) -> Workspace:
        workspace = self.repository.get(workspace_id)
        if workspace.archived_at is not None:
            raise WorkspaceConflictError("Archived Workspace is read-only")
        changes: dict[str, object] = {"updated_at": datetime.now(UTC)}
        if "name" in request.model_fields_set:
            changes["name"] = request.name.strip() if request.name is not None else workspace.name
        if "description" in request.model_fields_set:
            changes["description"] = (
                None if request.description is None else request.description.strip() or None
            )
        return self.repository.save(workspace.model_copy(update=changes))

    def archive(self, workspace_id: str) -> Workspace:
        workspace = self.repository.get(workspace_id)
        if workspace.is_default:
            raise WorkspaceConflictError("Default Workspace cannot be archived")
        if workspace.archived_at is not None:
            return workspace
        now = datetime.now(UTC)
        return self.repository.save(
            workspace.model_copy(update={"archived_at": now, "updated_at": now})
        )

    def restore(self, workspace_id: str) -> Workspace:
        workspace = self.repository.get(workspace_id)
        if workspace.archived_at is None:
            return workspace
        return self.repository.save(
            workspace.model_copy(update={"archived_at": None, "updated_at": datetime.now(UTC)})
        )

    def add_membership(
        self, workspace_id: str, request: AddWorkspaceMembership
    ) -> WorkspaceMembership:
        workspace = self.repository.get(workspace_id)
        if workspace.archived_at is not None:
            raise WorkspaceConflictError("Archived Workspace is read-only")
        if not self.asset_exists(request.object_type, request.object_id):
            raise ValueError(
                f"{request.object_type} '{request.object_id}' is not an available research asset"
            )
        values = self.repository.memberships(workspace_id)
        existing = next(
            (
                item
                for item in values
                if item.object_type == request.object_type and item.object_id == request.object_id
            ),
            None,
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        membership = WorkspaceMembership(
            workspace_id=workspace_id,
            object_type=request.object_type,
            object_id=request.object_id,
            added_at=now,
        )
        self.repository.save_memberships(workspace_id, (*values, membership))
        self.repository.save(workspace.model_copy(update={"updated_at": now}))
        return membership

    def add_many(
        self,
        workspace_id: str,
        values: Iterable[tuple[WorkspaceObjectType, str]],
    ) -> tuple[WorkspaceMembership, ...]:
        workspace = self.repository.get(workspace_id)
        if workspace.archived_at is not None:
            raise WorkspaceConflictError("Archived Workspace is read-only")
        distinct = tuple(dict.fromkeys(values))
        for object_type, object_id in distinct:
            if not self.asset_exists(object_type, object_id):
                raise ValueError(f"{object_type} '{object_id}' is not an available research asset")
        current = self.repository.memberships(workspace_id)
        by_key = {(item.object_type, item.object_id): item for item in current}
        now = datetime.now(UTC)
        added: list[WorkspaceMembership] = []
        for object_type, object_id in distinct:
            membership = by_key.get((object_type, object_id))
            if membership is None:
                membership = WorkspaceMembership(
                    workspace_id=workspace_id,
                    object_type=object_type,
                    object_id=object_id,
                    added_at=now,
                )
                by_key[(object_type, object_id)] = membership
            added.append(membership)
        if len(by_key) != len(current):
            self.repository.save_memberships(workspace_id, tuple(by_key.values()))
            self.repository.save(workspace.model_copy(update={"updated_at": now}))
        return tuple(added)

    def remove_membership(
        self, workspace_id: str, object_type: WorkspaceObjectType, object_id: str
    ) -> bool:
        workspace = self.repository.get(workspace_id)
        if workspace.archived_at is not None:
            raise WorkspaceConflictError("Archived Workspace is read-only")
        values = self.repository.memberships(workspace_id)
        retained = tuple(
            item
            for item in values
            if not (item.object_type == object_type and item.object_id == object_id)
        )
        if len(retained) == len(values):
            return False
        self.repository.save_memberships(workspace_id, retained)
        self.repository.save(workspace.model_copy(update={"updated_at": datetime.now(UTC)}))
        return True

    def memberships(self, workspace_id: str) -> tuple[WorkspaceMembershipView, ...]:
        return tuple(
            WorkspaceMembershipView(
                **item.model_dump(),
                reference_status=(
                    "AVAILABLE"
                    if self.asset_exists(item.object_type, item.object_id)
                    else "MISSING_REFERENCE"
                ),
            )
            for item in self.repository.memberships(workspace_id)
        )

    def integrity(self, workspace_id: str) -> WorkspaceIntegrity:
        values = self.repository.memberships(workspace_id)
        missing = tuple(
            item for item in values if not self.asset_exists(item.object_type, item.object_id)
        )
        return WorkspaceIntegrity(
            workspace_id=workspace_id,
            status="DEGRADED" if missing else "OK",
            membership_count=len(values),
            missing_references=missing,
        )

    def overview(self, workspace_id: str) -> WorkspaceOverview:
        workspace = self.repository.get(workspace_id)
        memberships = self.memberships(workspace_id)
        counts = {object_type: 0 for object_type in WORKSPACE_OBJECT_TYPES}
        for item in memberships:
            counts[item.object_type] += 1
        return WorkspaceOverview(
            workspace=workspace,
            statistics=WorkspaceStatistics(membership_count=len(memberships), counts=counts),
            recent_activity=tuple(
                sorted(memberships, key=lambda item: item.added_at, reverse=True)[:12]
            ),
        )
