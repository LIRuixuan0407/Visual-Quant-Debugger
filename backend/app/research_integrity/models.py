from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IntegrityCheckCode = Literal[
    "POST_HOLDOUT_MODIFICATION",
    "FUTURE_DATA_LEAK",
    "DATASET_SILENT_CHANGE",
    "STRATEGY_SEMANTIC_MISMATCH",
    "MISSING_LINEAGE",
    "MISSING_REVISION",
]
IntegritySeverity = Literal["PASS", "WARNING", "VIOLATION"]
IntegrityStatus = Literal["PASS", "WARNING", "VIOLATION"]

INTEGRITY_DISCLOSURE = (
    "Research Integrity Guardrails audit recorded lineage, dataset revisions, time "
    "boundaries, and strategy semantics against the append-only research ledger. They "
    "report evidence and reasons; they do not modify records, reveal Holdout, or judge "
    "whether a hypothesis is profitable."
)


class IntegrityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Research Integrity timestamps must be timezone-aware")
    return value


class IntegrityFinding(IntegrityModel):
    code: IntegrityCheckCode
    severity: IntegritySeverity
    subject: str
    reason: str
    evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def pass_has_no_evidence(self) -> IntegrityFinding:
        if self.severity == "PASS" and self.evidence:
            raise ValueError("PASS findings cannot carry evidence of a problem")
        return self


class HypothesisIntegrityReport(IntegrityModel):
    report_version: Literal["1.0"] = "1.0"
    hypothesis_id: str
    family_id: str
    title: str
    revision: int = Field(ge=1)
    lifecycle_status: str
    checked_at: datetime
    findings: tuple[IntegrityFinding, ...]
    violation_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    overall_status: IntegrityStatus
    disclosure: str = INTEGRITY_DISCLOSURE

    _aware_checked = field_validator("checked_at")(_aware)

    @model_validator(mode="after")
    def counts_match_findings(self) -> HypothesisIntegrityReport:
        violations = sum(1 for item in self.findings if item.severity == "VIOLATION")
        warnings = sum(1 for item in self.findings if item.severity == "WARNING")
        if violations != self.violation_count or warnings != self.warning_count:
            raise ValueError("Integrity counts do not match the recorded findings")
        expected: IntegrityStatus = "VIOLATION" if violations else "WARNING" if warnings else "PASS"
        if self.overall_status != expected:
            raise ValueError("Integrity overall status does not match the recorded findings")
        return self


class HypothesisIntegritySummary(IntegrityModel):
    hypothesis_id: str
    family_id: str
    title: str
    revision: int
    lifecycle_status: str
    overall_status: IntegrityStatus
    violation_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)


class WorkspaceIntegrityReport(IntegrityModel):
    report_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    hypotheses: tuple[HypothesisIntegritySummary, ...]
    overall_status: IntegrityStatus
    total_violations: int = Field(ge=0)
    total_warnings: int = Field(ge=0)
    disclosure: str = INTEGRITY_DISCLOSURE

    _aware_generated = field_validator("generated_at")(_aware)

    @model_validator(mode="after")
    def counts_match_hypotheses(self) -> WorkspaceIntegrityReport:
        violations = sum(item.violation_count for item in self.hypotheses)
        warnings = sum(item.warning_count for item in self.hypotheses)
        if violations != self.total_violations or warnings != self.total_warnings:
            raise ValueError("Workspace Integrity counts do not match the hypothesis summaries")
        expected: IntegrityStatus = "VIOLATION" if violations else "WARNING" if warnings else "PASS"
        if self.overall_status != expected:
            raise ValueError("Workspace Integrity status does not match the hypothesis summaries")
        return self
