from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

UniverseMode = Literal["STATIC", "POINT_IN_TIME"]


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Universe timestamps must be timezone-aware")
    return value


class UniverseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UniverseMembershipProvenance(UniverseModel):
    symbol: str
    source: str
    effective_from: datetime
    effective_to: datetime | None = None
    evidence: str

    _aware_times = field_validator("effective_from", "effective_to")(_aware)


class UniverseSnapshot(UniverseModel):
    effective_date: datetime
    symbols: tuple[str, ...]
    membership_provenance: tuple[UniverseMembershipProvenance, ...]

    _aware_effective = field_validator("effective_date")(_aware)


class HistoricalUniverse(UniverseModel):
    universe_id: str
    name: str
    source: str
    mode: UniverseMode
    dataset_id: str | None = None
    created_at: datetime
    snapshots: tuple[UniverseSnapshot, ...]
    survivorship_bias_free: bool
    disclosure: str

    _aware_created = field_validator("created_at")(_aware)

    @model_validator(mode="after")
    def verified_point_in_time(self) -> HistoricalUniverse:
        if self.mode == "POINT_IN_TIME" and not self.survivorship_bias_free:
            raise ValueError("POINT_IN_TIME universes require verifiable historical membership")
        if not self.snapshots:
            raise ValueError("A historical universe requires at least one snapshot")
        return self

    def symbols_at(self, used_at: datetime) -> tuple[str, ...]:
        eligible = [item for item in self.snapshots if item.effective_date <= used_at]
        return eligible[-1].symbols if eligible else ()
