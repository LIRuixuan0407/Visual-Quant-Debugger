from __future__ import annotations

from pathlib import Path

from app.workspace import default_workspace_root

from .models import FactorRelationshipRecord


class FactorRelationshipRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "factor-relationships"

    def _path(self, relationship_id: str) -> Path:
        return self.root / relationship_id / "relationship.json"

    def save(self, record: FactorRelationshipRecord) -> FactorRelationshipRecord:
        path = self._path(record.relationship_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return record

    def get(self, relationship_id: str) -> FactorRelationshipRecord | None:
        path = self._path(relationship_id)
        return (
            FactorRelationshipRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def list(self) -> tuple[FactorRelationshipRecord, ...]:
        if not self.root.exists():
            return ()
        records = (
            FactorRelationshipRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.root.glob("*/relationship.json")
        )
        return tuple(sorted(records, key=lambda item: item.created_at, reverse=True))


factor_relationship_repository = FactorRelationshipRepository()
