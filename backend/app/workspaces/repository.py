from __future__ import annotations

import json
import os
import re
import secrets
from contextlib import suppress
from pathlib import Path
from threading import RLock

from app.workspace import default_workspace_root

from .models import (
    Workspace,
    WorkspaceMembership,
    WorkspaceMigrationRecord,
)

WORKSPACE_ID_PATTERN = re.compile(r"^(workspace-default|workspace-[0-9a-f]{24})$")


class WorkspaceRepositoryError(ValueError):
    pass


class WorkspaceNotFoundError(KeyError):
    pass


class WorkspaceRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "workspaces"
        self.migration_path = self.root / ".default-migration-v1.json"
        self._lock = RLock()

    @staticmethod
    def new_workspace_id() -> str:
        return f"workspace-{secrets.token_hex(12)}"

    def _directory(self, workspace_id: str) -> Path:
        if not WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
            raise ValueError(f"Invalid Workspace id '{workspace_id}'")
        target = (self.root / workspace_id).resolve()
        if target.parent != self.root.resolve():
            raise ValueError("Workspace path escaped the workspace repository")
        return target

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _payload(value: Workspace | WorkspaceMigrationRecord) -> bytes:
        return (value.model_dump_json(indent=2) + "\n").encode()

    @staticmethod
    def _membership_payload(values: tuple[WorkspaceMembership, ...]) -> bytes:
        rows = [item.model_dump(mode="json") for item in values]
        return (json.dumps(rows, indent=2, sort_keys=True) + "\n").encode()

    def create(self, workspace: Workspace) -> Workspace:
        directory = self._directory(workspace.workspace_id)
        with self._lock:
            if directory.exists():
                raise WorkspaceRepositoryError(
                    f"Workspace '{workspace.workspace_id}' already exists"
                )
            directory.mkdir(parents=True, exist_ok=False)
            try:
                self._atomic_write(directory / "workspace.json", self._payload(workspace))
                self._atomic_write(directory / "memberships.json", self._membership_payload(()))
            except Exception:
                (directory / "workspace.json").unlink(missing_ok=True)
                (directory / "memberships.json").unlink(missing_ok=True)
                with suppress(OSError):
                    directory.rmdir()
                raise
        return workspace

    def save(self, workspace: Workspace) -> Workspace:
        path = self._directory(workspace.workspace_id) / "workspace.json"
        with self._lock:
            current = self.get(workspace.workspace_id)
            if current.workspace_id != workspace.workspace_id:
                raise WorkspaceRepositoryError("Workspace identity is immutable")
            if current.created_at != workspace.created_at:
                raise WorkspaceRepositoryError("Workspace created_at is immutable")
            if current.is_default != workspace.is_default:
                raise WorkspaceRepositoryError("Workspace is_default is immutable")
            self._atomic_write(path, self._payload(workspace))
        return workspace

    def get(self, workspace_id: str) -> Workspace:
        path = self._directory(workspace_id) / "workspace.json"
        if not path.is_file():
            raise WorkspaceNotFoundError(workspace_id)
        try:
            workspace = Workspace.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise WorkspaceRepositoryError(
                f"Workspace '{workspace_id}' metadata is invalid: {exc}"
            ) from exc
        if workspace.workspace_id != workspace_id:
            raise WorkspaceRepositoryError(
                f"Workspace '{workspace_id}' identity does not match its path"
            )
        return workspace

    def list(self) -> tuple[Workspace, ...]:
        if not self.root.exists():
            return ()
        records: list[Workspace] = []
        for path in self.root.glob("workspace-*/workspace.json"):
            if WORKSPACE_ID_PATTERN.fullmatch(path.parent.name):
                records.append(self.get(path.parent.name))
        return tuple(
            sorted(
                records,
                key=lambda item: (
                    item.archived_at is not None,
                    not item.is_default,
                    item.name.casefold(),
                    item.workspace_id,
                ),
            )
        )

    def memberships(self, workspace_id: str) -> tuple[WorkspaceMembership, ...]:
        self.get(workspace_id)
        path = self._directory(workspace_id) / "memberships.json"
        if not path.is_file():
            raise WorkspaceRepositoryError(f"Workspace '{workspace_id}' memberships are missing")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            values = tuple(WorkspaceMembership.model_validate(item) for item in payload)
        except (OSError, TypeError, ValueError) as exc:
            raise WorkspaceRepositoryError(
                f"Workspace '{workspace_id}' memberships are invalid: {exc}"
            ) from exc
        keys = [(item.object_type, item.object_id) for item in values]
        if len(keys) != len(set(keys)):
            raise WorkspaceRepositoryError(
                f"Workspace '{workspace_id}' contains duplicate memberships"
            )
        if any(item.workspace_id != workspace_id for item in values):
            raise WorkspaceRepositoryError(
                f"Workspace '{workspace_id}' membership identity does not match"
            )
        return tuple(
            sorted(values, key=lambda item: (item.added_at, item.object_type, item.object_id))
        )

    def save_memberships(
        self, workspace_id: str, values: tuple[WorkspaceMembership, ...]
    ) -> tuple[WorkspaceMembership, ...]:
        path = self._directory(workspace_id) / "memberships.json"
        self.get(workspace_id)
        keys = [(item.object_type, item.object_id) for item in values]
        if len(keys) != len(set(keys)):
            raise WorkspaceRepositoryError("Workspace memberships must be unique")
        if any(item.workspace_id != workspace_id for item in values):
            raise WorkspaceRepositoryError("Workspace membership identity does not match")
        ordered = tuple(
            sorted(values, key=lambda item: (item.added_at, item.object_type, item.object_id))
        )
        with self._lock:
            self._atomic_write(path, self._membership_payload(ordered))
        return ordered

    def migration(self) -> WorkspaceMigrationRecord | None:
        if not self.migration_path.is_file():
            return None
        try:
            return WorkspaceMigrationRecord.model_validate_json(self.migration_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise WorkspaceRepositoryError(
                f"Default Workspace migration record is invalid: {exc}"
            ) from exc

    def save_migration(self, record: WorkspaceMigrationRecord) -> None:
        with self._lock:
            if self.migration_path.exists():
                current = self.migration()
                if current != record:
                    raise WorkspaceRepositoryError(
                        "Default Workspace migration record is immutable"
                    )
                return
            self._atomic_write(self.migration_path, self._payload(record))


workspace_repository = WorkspaceRepository()
