from __future__ import annotations

from pathlib import Path

from app.workspace import default_workspace_root

from .models import PortfolioResearchRecord, PortfolioResearchSummary


class PortfolioResearchRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "portfolio-research"

    def _path(self, research_id: str) -> Path:
        return self.root / research_id / "portfolio.json"

    def save(self, record: PortfolioResearchRecord) -> PortfolioResearchRecord:
        path = self._path(record.portfolio_research_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return record

    def get(self, research_id: str) -> PortfolioResearchRecord | None:
        path = self._path(research_id)
        if not path.exists():
            return None
        return PortfolioResearchRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> tuple[PortfolioResearchSummary, ...]:
        if not self.root.exists():
            return ()
        records = [
            PortfolioResearchRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.root.glob("*/portfolio.json")
        ]
        return tuple(
            PortfolioResearchSummary(
                portfolio_research_id=item.portfolio_research_id,
                name=item.name,
                created_at=item.created_at,
                dataset_id=item.dataset_id,
                factor_count=len(item.factor_refs),
                combination=item.combination,
                revealed_stage=item.revealed_stage,
                net_return=item.stages[-1].cost_preview.net_return,
                turnover=item.stages[-1].cost_preview.turnover,
            )
            for item in sorted(records, key=lambda value: value.created_at, reverse=True)
        )


portfolio_research_repository = PortfolioResearchRepository()
