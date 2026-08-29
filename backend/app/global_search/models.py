from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SearchEntityType = Literal[
    "DATASET",
    "UNIVERSE",
    "CORPORATE_ACTION_DATASET",
    "FACTOR",
    "FACTOR_RESEARCH",
    "FACTOR_RELATIONSHIP",
    "WALK_FORWARD",
    "PORTFOLIO_RESEARCH",
    "HYPOTHESIS",
    "STRATEGY",
    "RUN",
    "TRACE",
    "SNAPSHOT",
    "DRIFT_REPORT",
]
type SearchScalar = str | int | float | bool | None

SEARCH_ENTITY_TYPES: tuple[SearchEntityType, ...] = (
    "DATASET",
    "UNIVERSE",
    "CORPORATE_ACTION_DATASET",
    "FACTOR",
    "FACTOR_RESEARCH",
    "FACTOR_RELATIONSHIP",
    "WALK_FORWARD",
    "PORTFOLIO_RESEARCH",
    "HYPOTHESIS",
    "STRATEGY",
    "RUN",
    "TRACE",
    "SNAPSHOT",
    "DRIFT_REPORT",
)


class SearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware_when_present(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("Global Search timestamps must be timezone-aware")
    return value


class SearchDocument(SearchModel):
    entity_type: SearchEntityType
    entity_id: str
    title: str
    subtitle: str = ""
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    created_at: datetime | None = None
    route: str
    metadata: dict[str, SearchScalar] = Field(default_factory=dict)

    _aware_created = field_validator("created_at")(_aware_when_present)


class SearchResult(SearchModel):
    entity_type: SearchEntityType
    entity_id: str
    title: str
    subtitle: str
    score: int = Field(ge=0)
    route: str
    highlights: tuple[str, ...]
    metadata: dict[str, SearchScalar] = Field(default_factory=dict)


class GlobalSearchResponse(SearchModel):
    query: str
    normalized_query: str
    results: tuple[SearchResult, ...]
