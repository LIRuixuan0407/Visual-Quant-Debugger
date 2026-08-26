from __future__ import annotations

from pathlib import Path

from app.workspace import default_workspace_root

from .models import WalkForwardResearchRecord


class WalkForwardRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "walk-forward"

    def _path(self, walk_forward_id: str) -> Path:
        return self.root / walk_forward_id / "walk-forward.json"

    def save(self, record: WalkForwardResearchRecord) -> WalkForwardResearchRecord:
        path = self._path(record.walk_forward_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return record

    def get(self, walk_forward_id: str) -> WalkForwardResearchRecord | None:
        path = self._path(walk_forward_id)
        return (
            WalkForwardResearchRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def list(self) -> tuple[WalkForwardResearchRecord, ...]:
        if not self.root.exists():
            return ()
        records = (
            WalkForwardResearchRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.root.glob("*/walk-forward.json")
        )
        return tuple(sorted(records, key=lambda item: item.created_at, reverse=True))


walk_forward_repository = WalkForwardRepository()
