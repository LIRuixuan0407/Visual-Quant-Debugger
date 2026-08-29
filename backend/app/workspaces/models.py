from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WorkspaceObjectType = Literal[
    "DATASET",
    "UNIVERSE",
    "CORPORATE_ACTION_DATASET",
    "FACTOR_RESEARCH",
    "FACTOR_RELATIONSHIP",
    "WALK_FORWARD",
    "PORTFOLIO_RESEARCH",
    "HYPOTHESIS",
    "STRATEGY",
    "RUN",
    "SNAPSHOT",
    "FORWARD_SESSION",
    "PAPER_SESSION",
    "DRIFT_REPORT",
    "ATTRIBUTION_REPORT",
    "RESEARCH_BUNDLE",
]
WorkspaceReferenceStatus = Literal["AVAILABLE", "MISSING_REFERENCE"]
WorkspaceIntegrityStatus = Literal["OK", "DEGRADED"]

WORKSPACE_OBJECT_TYPES: tuple[WorkspaceObjectType, ...] = (
    "DATASET",
    "UNIVERSE",
    "CORPORATE_ACTION_DATASET",
    "FACTOR_RESEARCH",
    "FACTOR_RELATIONSHIP",
    "WALK_FORWARD",
    "PORTFOLIO_RESEARCH",
    "HYPOTHESIS",
    "STRATEGY",
    "RUN",
    "SNAPSHOT",
    "FORWARD_SESSION",
    "PAPER_SESSION",
    "DRIFT_REPORT",
    "ATTRIBUTION_REPORT",
    "RESEARCH_BUNDLE",
)
DEFAULT_WORKSPACE_ID = "workspace-default"


class WorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Workspace timestamps must be timezone-aware")
    return value


class CreateWorkspace(WorkspaceModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)


class UpdateWorkspace(WorkspaceModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def at_least_one_change(self) -> UpdateWorkspace:
        if "name" not in self.model_fields_set and "description" not in self.model_fields_set:
            raise ValueError("Workspace update must include name or description")
        return self


class Workspace(WorkspaceModel):
    workspace_id: str = Field(pattern=r"^(workspace-default|workspace-[0-9a-f]{24})$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    is_default: bool = False

    _aware_times = field_validator("created_at", "updated_at")(_aware)

    @field_validator("archived_at")
    @classmethod
    def aware_archived(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)

    @model_validator(mode="after")
    def default_identity(self) -> Workspace:
        if self.is_default != (self.workspace_id == DEFAULT_WORKSPACE_ID):
            raise ValueError("Default Workspace identity and is_default must agree")
        return self


class AddWorkspaceMembership(WorkspaceModel):
    object_type: WorkspaceObjectType
    object_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )


class WorkspaceMembership(WorkspaceModel):
    workspace_id: str
    object_type: WorkspaceObjectType
    object_id: str
    added_at: datetime

    _aware_added = field_validator("added_at")(_aware)


class WorkspaceMembershipView(WorkspaceMembership):
    reference_status: WorkspaceReferenceStatus


class WorkspaceStatistics(WorkspaceModel):
    membership_count: int = Field(ge=0)
    counts: dict[WorkspaceObjectType, int]


class WorkspaceOverview(WorkspaceModel):
    workspace: Workspace
    statistics: WorkspaceStatistics
    recent_activity: tuple[WorkspaceMembershipView, ...]


class WorkspaceIntegrity(WorkspaceModel):
    workspace_id: str
    status: WorkspaceIntegrityStatus
    membership_count: int = Field(ge=0)
    missing_references: tuple[WorkspaceMembership, ...]


class WorkspaceMigrationRecord(WorkspaceModel):
    migration_version: Literal["1.0"] = "1.0"
    completed_at: datetime
    migrated_membership_count: int = Field(ge=0)

    _aware_completed = field_validator("completed_at")(_aware)
