from __future__ import annotations

from pathlib import Path

from app.workspace import default_workspace_root

from .models import ResearchHypothesis


class HypothesisRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "hypotheses"

    def _path(self, hypothesis_id: str) -> Path:
        return self.root / hypothesis_id / "hypothesis.json"

    def save(self, record: ResearchHypothesis) -> ResearchHypothesis:
        path = self._path(record.hypothesis_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return record

    def get(self, hypothesis_id: str) -> ResearchHypothesis | None:
        path = self._path(hypothesis_id)
        return (
            ResearchHypothesis.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def list(self) -> tuple[ResearchHypothesis, ...]:
        if not self.root.exists():
            return ()
        records = (
            ResearchHypothesis.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.root.glob("*/hypothesis.json")
        )
        return tuple(sorted(records, key=lambda item: item.created_at, reverse=True))

    def family(self, family_id: str) -> tuple[ResearchHypothesis, ...]:
        return tuple(
            sorted(
                (item for item in self.list() if item.family_id == family_id),
                key=lambda item: item.revision,
            )
        )


hypothesis_repository = HypothesisRepository()
