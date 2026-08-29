from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from .models import DatasetFamily

FAMILY_ID_PATTERN = re.compile(r"^dataset-family-[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


class DatasetFamilyRepository:
    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.root = self.workspace_root / ".vqd" / "dataset-families"

    @staticmethod
    def new_id() -> str:
        return f"dataset-family-{secrets.token_hex(12)}"

    def _path(self, dataset_family_id: str) -> Path:
        if not FAMILY_ID_PATTERN.fullmatch(dataset_family_id):
            raise ValueError(f"Invalid Dataset Family id '{dataset_family_id}'")
        path = (self.root / dataset_family_id / "family.json").resolve()
        if path.parent.parent != self.root.resolve():
            raise ValueError("Dataset Family path escaped the workspace")
        return path

    def get(self, dataset_family_id: str) -> DatasetFamily | None:
        path = self._path(dataset_family_id)
        if not path.exists():
            return None
        family = DatasetFamily.model_validate_json(path.read_text(encoding="utf-8"))
        if family.dataset_family_id != dataset_family_id:
            raise ValueError(
                f"Dataset Family '{dataset_family_id}' identity does not match its path"
            )
        return family

    def list(self) -> tuple[DatasetFamily, ...]:
        if not self.root.exists():
            return ()
        families = []
        for path in sorted(self.root.glob("*/family.json")):
            family = self.get(path.parent.name)
            if family is not None:
                families.append(family)
        return tuple(sorted(families, key=lambda item: (item.name.lower(), item.dataset_family_id)))

    def create(self, family: DatasetFamily) -> DatasetFamily:
        path = self._path(family.dataset_family_id)
        target = path.parent
        if path.exists():
            existing = DatasetFamily.model_validate_json(path.read_text(encoding="utf-8"))
            if existing != family:
                raise ValueError(f"Dataset family '{family.dataset_family_id}' already exists")
            return existing
        target.mkdir(parents=True, exist_ok=False)
        self._write(path, family)
        return family

    def save(self, family: DatasetFamily) -> DatasetFamily:
        path = self._path(family.dataset_family_id)
        target = path.parent
        target.mkdir(parents=True, exist_ok=True)
        self._write(path, family)
        return family

    @staticmethod
    def _write(path: Path, family: DatasetFamily) -> None:
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(family.model_dump_json(indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
