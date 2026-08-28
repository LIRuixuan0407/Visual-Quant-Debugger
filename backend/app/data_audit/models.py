from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AuditRootType = Literal["DATASET", "FACTOR_RESEARCH", "RUN"]
AuditSeverity = Literal[
    "PASS",
    "INFO",
    "WARNING",
    "VIOLATION",
    "INSUFFICIENT_EVIDENCE",
]
AuditStatus = Literal["PASS", "WARNING", "VIOLATION", "INCOMPLETE"]
AuditSourceState = Literal["MATCHES", "CHANGED", "MISSING"]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Data Audit timestamps must be timezone-aware")
    return value


class DataAuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreateDataAudit(DataAuditModel):
    root_type: AuditRootType
    root_id: str = Field(min_length=1, max_length=200)


class DataAuditFinding(DataAuditModel):
    code: str
    severity: AuditSeverity
    subject: str
    reason: str
    evidence: tuple[str, ...] = ()
    checked_count: int = Field(ge=0)
    affected_count: int = Field(ge=0)


class DataAuditRecord(DataAuditModel):
    audit_version: Literal["1.0"] = "1.0"
    audit_id: str
    root_type: AuditRootType
    root_id: str
    created_at: datetime
    source_fingerprints: dict[str, str]
    status: AuditStatus
    findings: tuple[DataAuditFinding, ...]
    checked_observations: int = Field(ge=0)
    checked_dependencies: int = Field(ge=0)
    checked_future_returns: int = Field(ge=0)
    checked_fundamental_inputs: int = Field(ge=0)
    disclosures: tuple[str, ...]

    _aware_created = field_validator("created_at")(_aware)


class DataAuditSummary(DataAuditModel):
    audit_id: str
    root_type: AuditRootType
    root_id: str
    created_at: datetime
    status: AuditStatus
    finding_count: int = Field(ge=0)
    violation_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)

    _aware_created = field_validator("created_at")(_aware)


class DataAuditDetail(DataAuditModel):
    audit: DataAuditRecord
    source_state: AuditSourceState
    current_source_fingerprints: dict[str, str]


class DataAuditSourceVerification(DataAuditModel):
    audit_id: str
    source_state: AuditSourceState
    recorded_source_fingerprints: dict[str, str]
    current_source_fingerprints: dict[str, str]
