from __future__ import annotations

import os
import re
from contextlib import suppress
from pathlib import Path

from app.workspace import default_workspace_root

from .models import DataAuditRecord, DataAuditSummary

AUDIT_ID_PATTERN = re.compile(r"^data-audit-[0-9a-f]{20}$")


class DataAuditIntegrityError(ValueError):
    pass


class DataAuditRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "data-audits"

    def _path(self, audit_id: str) -> Path:
        if not AUDIT_ID_PATTERN.fullmatch(audit_id):
            raise ValueError(f"Invalid Data Audit id '{audit_id}'")
        target = (self.root / audit_id / "audit.json").resolve()
        if target.parent.parent != self.root.resolve():
            raise ValueError("Data Audit path escaped the workspace")
        return target

    def save(self, record: DataAuditRecord) -> DataAuditRecord:
        path = self._path(record.audit_id)
        if path.exists():
            existing = self.get(record.audit_id)
            if existing == record:
                return existing
            raise DataAuditIntegrityError(
                f"Data Audit '{record.audit_id}' is immutable and already exists"
            )
        path.parent.mkdir(parents=True, exist_ok=False)
        temporary = path.with_suffix(".json.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write((record.model_dump_json(indent=2) + "\n").encode())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            with suppress(OSError):
                path.parent.rmdir()
            raise
        return record

    def get(self, audit_id: str) -> DataAuditRecord | None:
        path = self._path(audit_id)
        if not path.exists():
            return None
        try:
            record = DataAuditRecord.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise DataAuditIntegrityError(f"Data Audit '{audit_id}' is invalid: {exc}") from exc
        if record.audit_id != audit_id:
            raise DataAuditIntegrityError(f"Data Audit '{audit_id}' identity does not match")
        return record

    @staticmethod
    def _summary(record: DataAuditRecord) -> DataAuditSummary:
        return DataAuditSummary(
            audit_id=record.audit_id,
            root_type=record.root_type,
            root_id=record.root_id,
            created_at=record.created_at,
            status=record.status,
            finding_count=len(record.findings),
            violation_count=sum(item.severity == "VIOLATION" for item in record.findings),
            warning_count=sum(item.severity == "WARNING" for item in record.findings),
        )

    def list(self) -> tuple[DataAuditSummary, ...]:
        if not self.root.exists():
            return ()
        records: list[DataAuditRecord] = []
        for path in self.root.glob("*/audit.json"):
            record = self.get(path.parent.name)
            if record is not None:
                records.append(record)
        return tuple(
            self._summary(item)
            for item in sorted(records, key=lambda value: value.created_at, reverse=True)
        )


data_audit_repository = DataAuditRepository()
