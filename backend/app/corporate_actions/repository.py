from __future__ import annotations

import os
import re
from contextlib import suppress
from pathlib import Path

from app.workspace import default_workspace_root

from .models import CorporateActionDataset

ID_PATTERN = re.compile(r"^corporate-actions-[0-9a-f]{20}$")


class CorporateActionIntegrityError(ValueError):
    pass


class CorporateActionRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "corporate-actions"

    def _path(self, dataset_id: str) -> Path:
        if not ID_PATTERN.fullmatch(dataset_id):
            raise ValueError(f"Invalid Corporate Action dataset id '{dataset_id}'")
        target = (self.root / dataset_id / "actions.json").resolve()
        if target.parent.parent != self.root.resolve():
            raise ValueError("Corporate Action path escaped the workspace")
        return target

    def save(self, record: CorporateActionDataset) -> CorporateActionDataset:
        path = self._path(record.corporate_action_dataset_id)
        content = (record.model_dump_json(indent=2) + "\n").encode()
        if path.exists():
            existing = self.get(record.corporate_action_dataset_id)
            if existing == record:
                return existing
            raise CorporateActionIntegrityError(
                f"Corporate Action dataset '{record.corporate_action_dataset_id}' is immutable"
            )
        path.parent.mkdir(parents=True, exist_ok=False)
        temporary = path.with_suffix(".json.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            with suppress(OSError):
                path.parent.rmdir()
            raise
        return record

    def get(self, dataset_id: str) -> CorporateActionDataset | None:
        path = self._path(dataset_id)
        if not path.exists():
            return None
        try:
            record = CorporateActionDataset.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise CorporateActionIntegrityError(
                f"Corporate Action dataset '{dataset_id}' is invalid: {exc}"
            ) from exc
        if record.corporate_action_dataset_id != dataset_id:
            raise CorporateActionIntegrityError(
                f"Corporate Action dataset '{dataset_id}' identity does not match its path"
            )
        return record

    def list(self) -> tuple[CorporateActionDataset, ...]:
        if not self.root.exists():
            return ()
        records = []
        for path in self.root.glob("*/actions.json"):
            record = self.get(path.parent.name)
            if record is not None:
                records.append(record)
        return tuple(sorted(records, key=lambda item: item.retrieved_at, reverse=True))


corporate_action_repository = CorporateActionRepository()
