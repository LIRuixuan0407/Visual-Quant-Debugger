from __future__ import annotations

from pathlib import Path

from app.workspace import default_workspace_root

from .models import ResearchLedgerEntry


class ResearchLedgerRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "research-ledger"

    def save(self, entry: ResearchLedgerEntry) -> ResearchLedgerEntry:
        path = self.root / entry.kind.lower() / f"{entry.entry_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(entry.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return entry

    def list(self) -> tuple[ResearchLedgerEntry, ...]:
        if not self.root.exists():
            return ()
        entries = [
            ResearchLedgerEntry.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.root.glob("*/*.json")
        ]
        return tuple(sorted(entries, key=lambda item: item.created_at, reverse=True))


research_ledger = ResearchLedgerRepository()
