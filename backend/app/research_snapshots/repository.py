from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from app.workspace import default_workspace_root

from .models import ResearchSnapshot, ResearchSnapshotSummary, snapshot_content_fingerprint


class SnapshotIntegrityError(ValueError):
    pass


class ResearchSnapshotRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "research-snapshots"

    def _path(self, snapshot_id: str) -> Path:
        if not snapshot_id.startswith("research-snapshot-"):
            raise ValueError(f"Invalid Research Snapshot id '{snapshot_id}'")
        target = (self.root / snapshot_id / "snapshot.json").resolve()
        if target.parent.parent != self.root.resolve():
            raise ValueError("Research Snapshot path escaped the workspace")
        return target

    @staticmethod
    def _verify(record: ResearchSnapshot) -> None:
        if snapshot_content_fingerprint(record) != record.content_fingerprint:
            raise SnapshotIntegrityError(
                f"Research Snapshot '{record.snapshot_id}' content fingerprint does not match"
            )

    def save(self, record: ResearchSnapshot) -> ResearchSnapshot:
        self._verify(record)
        path = self._path(record.snapshot_id)
        content = (record.model_dump_json(indent=2) + "\n").encode()
        if path.exists():
            existing = self.get(record.snapshot_id)
            if existing == record:
                return existing
            raise SnapshotIntegrityError(
                f"Research Snapshot '{record.snapshot_id}' is immutable and already exists"
            )
        path.parent.mkdir(parents=True, exist_ok=False)
        temporary = path.with_suffix(".json.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            with suppress(OSError):
                path.parent.rmdir()
            raise
        return record

    def get(self, snapshot_id: str) -> ResearchSnapshot | None:
        path = self._path(snapshot_id)
        if not path.exists():
            return None
        try:
            record = ResearchSnapshot.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise SnapshotIntegrityError(
                f"Research Snapshot '{snapshot_id}' is invalid: {exc}"
            ) from exc
        if record.snapshot_id != snapshot_id:
            raise SnapshotIntegrityError(
                f"Research Snapshot '{snapshot_id}' identity does not match its path"
            )
        self._verify(record)
        return record

    @staticmethod
    def _summary(record: ResearchSnapshot) -> ResearchSnapshotSummary:
        lineage = record.lineage
        return ResearchSnapshotSummary(
            snapshot_id=record.snapshot_id,
            name=record.name,
            created_at=record.created_at,
            content_fingerprint=record.content_fingerprint,
            hypothesis_id=lineage.hypothesis_id,
            hypothesis_revision=lineage.hypothesis_revision,
            dataset_id=lineage.dataset_id,
            dataset_family_id=lineage.dataset_family_id,
            dataset_revision=lineage.dataset_revision,
            factor_count=len(lineage.factor_research_ids),
            strategy_id=lineage.strategy_id,
            run_count=len(lineage.run_ids),
            trace_count=len(lineage.trace_ids),
        )

    def list(self) -> tuple[ResearchSnapshotSummary, ...]:
        if not self.root.exists():
            return ()
        records = []
        for path in self.root.glob("*/snapshot.json"):
            record = self.get(path.parent.name)
            if record is not None:
                records.append(record)
        return tuple(
            self._summary(item)
            for item in sorted(records, key=lambda value: value.created_at, reverse=True)
        )

    def list_ids(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(
                path.parent.name
                for path in self.root.glob("research-snapshot-*/snapshot.json")
                if path.is_file()
            )
        )

    def list_available(self) -> tuple[ResearchSnapshotSummary, ...]:
        records: list[ResearchSnapshot] = []
        for snapshot_id in self.list_ids():
            try:
                record = self.get(snapshot_id)
            except SnapshotIntegrityError:
                continue
            if record is not None:
                records.append(record)
        return tuple(
            self._summary(item)
            for item in sorted(records, key=lambda value: value.created_at, reverse=True)
        )


research_snapshot_repository = ResearchSnapshotRepository()
