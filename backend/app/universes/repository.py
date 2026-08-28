from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from app.datasets.models import DatasetDefinition
from app.workspace import default_workspace_root

from .models import (
    CreateHistoricalUniverse,
    HistoricalUniverse,
    UniverseMembershipProvenance,
    UniverseSnapshot,
    membership_provenance_issues,
)

UNIVERSE_ID_PATTERN = re.compile(r"^universe-[A-Za-z0-9._-]+$")


class UniverseIntegrityError(ValueError):
    pass


class UniverseRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "universes"

    def _path(self, universe_id: str) -> Path:
        if not UNIVERSE_ID_PATTERN.fullmatch(universe_id):
            raise ValueError(f"Invalid Universe id '{universe_id}'")
        target = (self.root / universe_id / "universe.json").resolve()
        if target.parent.parent != self.root.resolve():
            raise ValueError("Universe path escaped the workspace")
        return target

    def save(self, universe: HistoricalUniverse) -> HistoricalUniverse:
        path = self._path(universe.universe_id)
        if path.exists():
            existing = self.get(universe.universe_id)
            if existing == universe:
                return existing
            raise UniverseIntegrityError(f"Universe '{universe.universe_id}' is immutable")
        path.parent.mkdir(parents=True, exist_ok=False)
        temporary = path.with_suffix(".json.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write((universe.model_dump_json(indent=2) + "\n").encode())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            with suppress(OSError):
                path.parent.rmdir()
            raise
        return universe

    def create(self, request: CreateHistoricalUniverse) -> HistoricalUniverse:
        snapshots = tuple(sorted(request.snapshots, key=lambda item: item.effective_date))
        issues = membership_provenance_issues(snapshots)
        safe = request.mode == "POINT_IN_TIME" and not issues
        semantic = json.dumps(
            request.model_copy(update={"snapshots": snapshots}).model_dump(mode="json"),
            separators=(",", ":"),
            sort_keys=True,
        )
        identity = hashlib.sha256(semantic.encode()).hexdigest()[:20]
        return self.save(
            HistoricalUniverse(
                universe_id=f"universe-{identity}",
                name=request.name.strip(),
                source=request.source.strip(),
                mode=request.mode,
                dataset_id=request.dataset_id,
                created_at=datetime.now(UTC),
                snapshots=snapshots,
                survivorship_bias_free=safe,
                disclosure=request.disclosure.strip(),
            )
        )

    def get(self, universe_id: str) -> HistoricalUniverse | None:
        path = self._path(universe_id)
        if not path.exists():
            return None
        try:
            record = HistoricalUniverse.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise UniverseIntegrityError(f"Universe '{universe_id}' is invalid: {exc}") from exc
        if record.universe_id != universe_id:
            raise UniverseIntegrityError(f"Universe '{universe_id}' identity does not match path")
        return record

    def list(self) -> tuple[HistoricalUniverse, ...]:
        if not self.root.exists():
            return ()
        records = []
        for path in self.root.glob("*/universe.json"):
            record = self.get(path.parent.name)
            if record is not None:
                records.append(record)
        return tuple(sorted(records, key=lambda item: item.created_at, reverse=True))

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
