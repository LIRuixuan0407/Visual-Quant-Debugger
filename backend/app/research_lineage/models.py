from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LineageNodeType = Literal[
    "DATASET",
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
]
LineageEdgeType = Literal[
    "USES_DATASET",
    "RESEARCHES_FACTOR",
    "RELATES_FACTORS",
    "VALIDATES_FACTOR",
    "COMBINES_FACTORS",
    "SUPPORTS_HYPOTHESIS",
    "USES_PORTFOLIO",
    "GENERATES_STRATEGY",
    "EXECUTES_STRATEGY",
    "PRODUCES_TRACE",
    "FREEZES_RESEARCH",
]
LineageNodeStatus = Literal["RESOLVED", "MISSING_SOURCE", "ORPHAN"]
LineageDirection = Literal["UPSTREAM", "DOWNSTREAM", "BOTH"]
LineageScalar = str | int | float | bool | None

LINEAGE_DISCLOSURE = (
    "Global Research Lineage is a deterministic read model over explicit identifiers stored "
    "by existing research records. It does not infer relationships from names, timestamps, "
    "shared datasets, strategies, parameters, or similarity, and it stores no new facts."
)


class LineageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware_when_present(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("Research Lineage timestamps must be timezone-aware")
    return value


class LineageNode(LineageModel):
    node_id: str
    node_type: LineageNodeType
    artifact_id: str
    revision: str | int | None
    label: str
    created_at: datetime | None
    status: LineageNodeStatus
    route: str | None
    metadata: dict[str, LineageScalar] = Field(default_factory=dict)

    _aware_created = field_validator("created_at")(_aware_when_present)


class LineageEdge(LineageModel):
    edge_id: str
    edge_type: LineageEdgeType
    source_node_id: str
    target_node_id: str
    source_field: str


class ResearchLineageGraph(LineageModel):
    graph_version: Literal["1.0"] = "1.0"
    root_type: LineageNodeType | None = None
    root_id: str | None = None
    direction: LineageDirection = "BOTH"
    max_depth: int = Field(default=8, ge=1, le=8)
    nodes: tuple[LineageNode, ...]
    edges: tuple[LineageEdge, ...]
    disclosure: str = LINEAGE_DISCLOSURE


class LineageTypeCount(LineageModel):
    node_type: LineageNodeType
    count: int = Field(ge=0)


class ResearchLineageSummary(LineageModel):
    graph_version: Literal["1.0"] = "1.0"
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    missing_source_count: int = Field(ge=0)
    orphan_count: int = Field(ge=0)
    nodes_by_type: tuple[LineageTypeCount, ...]
    disclosure: str = LINEAGE_DISCLOSURE
