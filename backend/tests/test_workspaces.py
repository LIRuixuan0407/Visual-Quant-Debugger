from __future__ import annotations

from pathlib import Path

import pytest

from app.workspaces import (
    DEFAULT_WORKSPACE_ID,
    AddWorkspaceMembership,
    CreateWorkspace,
    UpdateWorkspace,
    WorkspaceConflictError,
    WorkspaceRepository,
    WorkspaceService,
)
from app.workspaces.models import WorkspaceObjectType


class Assets:
    def __init__(self) -> None:
        self.values: dict[tuple[WorkspaceObjectType, str], str] = {
            ("DATASET", "dataset-exact"): "sha256:dataset",
            ("RUN", "run-0123456789abcdef01234567"): "sha256:run",
            ("SNAPSHOT", "research-snapshot-exact"): "sha256:snapshot",
        }

    def exists(self, object_type: WorkspaceObjectType, object_id: str) -> bool:
        return (object_type, object_id) in self.values

    def enumerate(self) -> tuple[tuple[WorkspaceObjectType, str], ...]:
        return tuple(self.values)


def service_at(tmp_path: Path) -> tuple[WorkspaceService, Assets]:
    assets = Assets()
    return (
        WorkspaceService(WorkspaceRepository(tmp_path), assets.exists, assets.enumerate),
        assets,
    )


def test_creates_and_reloads_workspace(tmp_path: Path) -> None:
    service, assets = service_at(tmp_path)
    created = service.create(CreateWorkspace(name="Momentum", description="Research"))

    restarted = WorkspaceService(WorkspaceRepository(tmp_path), assets.exists, assets.enumerate)
    loaded = restarted.overview(created.workspace_id).workspace

    assert loaded == created
    assert loaded.workspace_id.startswith("workspace-")


def test_updates_name_without_changing_identity_or_creation_time(tmp_path: Path) -> None:
    service, _ = service_at(tmp_path)
    created = service.create(CreateWorkspace(name="Momentum"))
    updated = service.update(
        created.workspace_id,
        UpdateWorkspace(name="Quality", description="Fundamental research"),
    )

    assert updated.name == "Quality"
    assert updated.workspace_id == created.workspace_id
    assert updated.created_at == created.created_at


def test_archive_and_restore_preserve_memberships(tmp_path: Path) -> None:
    service, _ = service_at(tmp_path)
    workspace = service.create(CreateWorkspace(name="Pairs"))
    membership = service.add_membership(
        workspace.workspace_id,
        AddWorkspaceMembership(object_type="DATASET", object_id="dataset-exact"),
    )

    archived = service.archive(workspace.workspace_id)
    assert archived.archived_at is not None
    assert service.memberships(workspace.workspace_id)[0].object_id == membership.object_id
    restored = service.restore(workspace.workspace_id)
    assert restored.archived_at is None
    assert service.memberships(workspace.workspace_id)[0].object_id == membership.object_id


def test_default_workspace_cannot_be_archived(tmp_path: Path) -> None:
    service, _ = service_at(tmp_path)
    service.ensure_default_workspace()

    with pytest.raises(WorkspaceConflictError, match="cannot be archived"):
        service.archive(DEFAULT_WORKSPACE_ID)


def test_workspace_path_traversal_is_rejected(tmp_path: Path) -> None:
    repository = WorkspaceRepository(tmp_path)

    with pytest.raises(ValueError, match="Invalid Workspace id"):
        repository.get("../outside")


def test_duplicate_membership_is_idempotent(tmp_path: Path) -> None:
    service, _ = service_at(tmp_path)
    workspace = service.create(CreateWorkspace(name="Momentum"))
    request = AddWorkspaceMembership(object_type="RUN", object_id="run-0123456789abcdef01234567")

    first = service.add_membership(workspace.workspace_id, request)
    second = service.add_membership(workspace.workspace_id, request)

    assert second == first
    assert len(service.memberships(workspace.workspace_id)) == 1


def test_remove_membership_does_not_mutate_underlying_asset(tmp_path: Path) -> None:
    service, assets = service_at(tmp_path)
    workspace = service.create(CreateWorkspace(name="Momentum"))
    request = AddWorkspaceMembership(object_type="DATASET", object_id="dataset-exact")
    service.add_membership(workspace.workspace_id, request)

    assert service.remove_membership(workspace.workspace_id, "DATASET", "dataset-exact")
    assert assets.values[("DATASET", "dataset-exact")] == "sha256:dataset"
    assert service.memberships(workspace.workspace_id) == ()


def test_same_asset_can_belong_to_two_workspaces_without_copy(tmp_path: Path) -> None:
    service, assets = service_at(tmp_path)
    first = service.create(CreateWorkspace(name="Momentum"))
    second = service.create(CreateWorkspace(name="Quality"))
    request = AddWorkspaceMembership(object_type="DATASET", object_id="dataset-exact")

    a = service.add_membership(first.workspace_id, request)
    b = service.add_membership(second.workspace_id, request)

    assert a.object_id == b.object_id == "dataset-exact"
    assert len(assets.values) == 3


def test_missing_asset_is_preserved_as_integrity_evidence(tmp_path: Path) -> None:
    service, assets = service_at(tmp_path)
    workspace = service.create(CreateWorkspace(name="Momentum"))
    service.add_membership(
        workspace.workspace_id,
        AddWorkspaceMembership(object_type="DATASET", object_id="dataset-exact"),
    )
    del assets.values[("DATASET", "dataset-exact")]

    integrity = service.integrity(workspace.workspace_id)
    memberships = service.memberships(workspace.workspace_id)

    assert integrity.status == "DEGRADED"
    assert integrity.missing_references[0].object_id == "dataset-exact"
    assert memberships[0].reference_status == "MISSING_REFERENCE"


def test_new_membership_rejects_missing_asset(tmp_path: Path) -> None:
    service, _ = service_at(tmp_path)
    workspace = service.create(CreateWorkspace(name="Momentum"))

    with pytest.raises(ValueError, match="not an available research asset"):
        service.add_membership(
            workspace.workspace_id,
            AddWorkspaceMembership(object_type="DATASET", object_id="dataset-missing"),
        )


def test_default_migration_preserves_ids_fingerprints_and_added_at(tmp_path: Path) -> None:
    service, assets = service_at(tmp_path)
    before = dict(assets.values)
    default = service.ensure_default_workspace()
    first = service.memberships(default.workspace_id)

    restarted = WorkspaceService(WorkspaceRepository(tmp_path), assets.exists, assets.enumerate)
    restarted.ensure_default_workspace()
    second = restarted.memberships(default.workspace_id)

    assert [(item.object_type, item.object_id) for item in first] == list(before)
    assert second == first
    assert assets.values == before
    assert len(restarted.list(include_archived=True)) == 1


def test_migration_marker_does_not_capture_later_assets(tmp_path: Path) -> None:
    service, assets = service_at(tmp_path)
    service.ensure_default_workspace()
    assets.values[("RUN", "run-fedcba9876543210fedcba98")] = "sha256:new-run"

    service.ensure_default_workspace()

    ids = {item.object_id for item in service.memberships(DEFAULT_WORKSPACE_ID)}
    assert "run-fedcba9876543210fedcba98" not in ids


def test_archived_workspace_is_read_only_but_visible(tmp_path: Path) -> None:
    service, _ = service_at(tmp_path)
    workspace = service.create(CreateWorkspace(name="Archived"))
    service.archive(workspace.workspace_id)

    with pytest.raises(WorkspaceConflictError, match="read-only"):
        service.add_membership(
            workspace.workspace_id,
            AddWorkspaceMembership(object_type="DATASET", object_id="dataset-exact"),
        )
    assert service.overview(workspace.workspace_id).workspace.archived_at is not None
