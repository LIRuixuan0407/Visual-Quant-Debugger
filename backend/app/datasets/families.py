from __future__ import annotations

import secrets
from pathlib import Path

from .models import DatasetFamily


class DatasetFamilyRepository:
    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.root = self.workspace_root / ".vqd" / "dataset-families"

    @staticmethod
    def new_id() -> str:
        return f"dataset-family-{secrets.token_hex(12)}"

    def get(self, dataset_family_id: str) -> DatasetFamily | None:
        path = self.root / dataset_family_id / "family.json"
        if not path.exists():
            return None
        return DatasetFamily.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> tuple[DatasetFamily, ...]:
        if not self.root.exists():
            return ()
        families = [
            DatasetFamily.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.root.glob("*/family.json"))
        ]
        return tuple(sorted(families, key=lambda item: (item.name.lower(), item.dataset_family_id)))

    def create(self, family: DatasetFamily) -> DatasetFamily:
        target = self.root / family.dataset_family_id
        path = target / "family.json"
        if path.exists():
            existing = DatasetFamily.model_validate_json(path.read_text(encoding="utf-8"))
            if existing != family:
                raise ValueError(f"Dataset family '{family.dataset_family_id}' already exists")
            return existing
        target.mkdir(parents=True, exist_ok=False)
        self._write(path, family)
        return family

    def save(self, family: DatasetFamily) -> DatasetFamily:
        target = self.root / family.dataset_family_id
        target.mkdir(parents=True, exist_ok=True)
        self._write(target / "family.json", family)
        return family

    @staticmethod
    def _write(path: Path, family: DatasetFamily) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(family.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
