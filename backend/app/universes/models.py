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

    @model_validator(mode="after")
    def valid_membership_period(self) -> UniverseMembershipProvenance:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("Universe membership effective_to cannot precede effective_from")
        return self


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
        if not self.snapshots:
            raise ValueError("A historical universe requires at least one snapshot")
        dates = tuple(item.effective_date for item in self.snapshots)
        if tuple(sorted(dates)) != dates or len(dates) != len(set(dates)):
            raise ValueError("Universe snapshot effective_date values must be strictly increasing")
        issues = membership_provenance_issues(self.snapshots)
        if self.survivorship_bias_free and (self.mode != "POINT_IN_TIME" or issues):
            raise ValueError(
                "Only a POINT_IN_TIME universe with complete membership provenance can be "
                "survivorship-bias free"
            )
        return self

    def symbols_at(self, used_at: datetime) -> tuple[str, ...]:
        eligible = [item for item in self.snapshots if item.effective_date <= used_at]
        return eligible[-1].symbols if eligible else ()


class CreateHistoricalUniverse(UniverseModel):
    name: str
    source: str
    mode: UniverseMode
    dataset_id: str | None = None
    snapshots: tuple[UniverseSnapshot, ...]
    disclosure: str


def membership_provenance_issues(
    snapshots: tuple[UniverseSnapshot, ...],
) -> tuple[str, ...]:
    issues: list[str] = []
    for snapshot in snapshots:
        if len(snapshot.symbols) != len(set(snapshot.symbols)):
            issues.append(f"{snapshot.effective_date.isoformat()}: duplicate symbols in snapshot")
        by_symbol: dict[str, list[UniverseMembershipProvenance]] = {}
        for item in snapshot.membership_provenance:
            by_symbol.setdefault(item.symbol, []).append(item)
        for symbol in snapshot.symbols:
            eligible = tuple(
                item
                for item in by_symbol.get(symbol, ())
                if item.effective_from <= snapshot.effective_date
                and (item.effective_to is None or snapshot.effective_date <= item.effective_to)
                and item.source.strip()
                and item.evidence.strip()
            )
            if not eligible:
                issues.append(
                    f"{snapshot.effective_date.isoformat()}: {symbol} has no verifiable "
                    "membership provenance"
                )
    return tuple(issues)
