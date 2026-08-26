from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from app.datasets.models import DatasetDefinition
from app.workspace import default_workspace_root

from .models import (
    HistoricalUniverse,
    UniverseMembershipProvenance,
    UniverseSnapshot,
)


class UniverseRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "universes"

    def _path(self, universe_id: str) -> Path:
        return self.root / universe_id / "universe.json"

    def save(self, universe: HistoricalUniverse) -> HistoricalUniverse:
        path = self._path(universe.universe_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(universe.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return universe

    def get(self, universe_id: str) -> HistoricalUniverse | None:
        path = self._path(universe_id)
        return (
            HistoricalUniverse.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def list(self) -> tuple[HistoricalUniverse, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(
                (
                    HistoricalUniverse.model_validate_json(path.read_text(encoding="utf-8"))
                    for path in self.root.glob("*/universe.json")
                ),
                key=lambda item: item.created_at,
                reverse=True,
            )
        )

    def static_for_dataset(self, dataset: DatasetDefinition) -> HistoricalUniverse:
        identity = hashlib.sha256(
            f"STATIC:{dataset.dataset_id}:{dataset.content_fingerprint}".encode()
        ).hexdigest()[:16]
        universe_id = f"universe-{identity}"
        existing = self.get(universe_id)
        if existing is not None:
            return existing
        provenance = tuple(
            UniverseMembershipProvenance(
                symbol=symbol,
                source=f"dataset:{dataset.dataset_id}",
                effective_from=dataset.start_time,
                effective_to=None,
                evidence="User-selected constituent stored with the immutable market dataset.",
            )
            for symbol in dataset.symbols
        )
        return self.save(
            HistoricalUniverse(
                universe_id=universe_id,
                name=f"{dataset.name} · static universe",
                source=f"dataset:{dataset.dataset_id}",
                mode="STATIC",
                dataset_id=dataset.dataset_id,
                created_at=datetime.now(UTC),
                snapshots=(
                    UniverseSnapshot(
                        effective_date=dataset.start_time,
                        symbols=dataset.symbols,
                        membership_provenance=provenance,
                    ),
                ),
                survivorship_bias_free=False,
                disclosure=(
                    "STATIC universe: current user-selected constituents are carried through "
                    "history; this is not survivorship-bias free."
                ),
            )
        )


universe_repository = UniverseRepository()
