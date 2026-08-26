from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LedgerKind = Literal["PORTFOLIO", "WALK_FORWARD", "FACTOR_RELATIONSHIP", "HYPOTHESIS"]
LedgerMetric = str | int | float | bool | None


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResearchLedgerEntry(LedgerModel):
    entry_id: str
    kind: LedgerKind
    artifact_id: str
    revision: int = Field(ge=1)
    created_at: datetime
    dataset_ids: tuple[str, ...] = ()
    dataset_fingerprints: tuple[str, ...] = ()
    factor_ids: tuple[str, ...] = ()
    factor_revisions: tuple[str, ...] = ()
    strategy_id: str | None = None
    strategy_revision: str | None = None
    known_evidence: tuple[str, ...] = ()
    result_refs: tuple[str, ...] = ()
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    walk_forward_id: str | None = None
    window_definitions: tuple[dict[str, str | int], ...] = ()
    research_results: tuple[dict[str, LedgerMetric], ...] = ()
    validation_results: tuple[dict[str, LedgerMetric], ...] = ()
    forward_results: tuple[dict[str, LedgerMetric], ...] = ()
    strategy_results: tuple[dict[str, LedgerMetric], ...] = ()
    factor_relationship_id: str | None = None
    hypothesis_id: str | None = None
    portfolio_research_id: str | None = None

    @field_validator("created_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Research ledger timestamps must be timezone-aware")
        return value

    @classmethod
    def new(cls, **values: object) -> ResearchLedgerEntry:
        return cls(created_at=datetime.now(UTC), **values)  # type: ignore[arg-type]
