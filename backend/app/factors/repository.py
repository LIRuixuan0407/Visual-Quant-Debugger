from __future__ import annotations

import json
from pathlib import Path

from app.workspace import default_workspace_root

from .models import FactorResearchRecord, FactorResearchSummary


class FactorResearchRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "factor-research"

    def _path(self, research_id: str) -> Path:
        return self.root / research_id / "research.json"

    def save(self, record: FactorResearchRecord) -> FactorResearchRecord:
        path = self._path(record.research_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return record

    def get(self, research_id: str) -> FactorResearchRecord | None:
        path = self._path(research_id)
        return (
            FactorResearchRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def list(self) -> tuple[FactorResearchSummary, ...]:
        if not self.root.exists():
            return ()
        records = [
            FactorResearchRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.root.glob("*/research.json")
        ]
        summaries: list[FactorResearchSummary] = []
        for record in sorted(records, key=lambda item: item.created_at, reverse=True):
            research = record.evaluations[0].horizons[0]
            summaries.append(
                FactorResearchSummary(
                    research_id=record.research_id,
                    name=record.name,
                    created_at=record.created_at,
                    dataset_id=record.dataset_id,
                    factor_id=record.factor.factor_id,
                    symbols=len(record.universe),
                    revealed_stage=record.revealed_stage,
                    research_ic=research.ic,
                    research_rank_ic=research.rank_ic,
                    factor_category=record.factor.category,
                    data_source=record.factor.data_source,
                    factor_origin=record.factor.origin,
                    direction=record.factor.direction,
                )
            )
        return tuple(summaries)

    def export_json(self, research_id: str) -> str:
        record = self.get(research_id)
        if record is None:
            raise KeyError(research_id)
        return json.dumps(record.model_dump(mode="json"), indent=2)


factor_research_repository = FactorResearchRepository()
