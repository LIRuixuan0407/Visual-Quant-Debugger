from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BundleMode = Literal["REFERENCE_ONLY", "PORTABLE"]
BundleRootKind = Literal["SNAPSHOT", "RUN"]
BundleObjectKind = Literal[
    "SNAPSHOT",
    "DATASET",
    "UNIVERSE",
    "CORPORATE_ACTION_DATASET",
    "FACTOR_RESEARCH",
    "FACTOR_RELATIONSHIP",
    "WALK_FORWARD",
    "HYPOTHESIS",
    "PORTFOLIO_RESEARCH",
    "RUN",
    "DRIFT_REPORT",
    "ATTRIBUTION_REPORT",
]
BundleConflictStatus = Literal["IMPORT", "REUSE", "REJECT", "UNAVAILABLE"]


class BundleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BundleRootObject(BundleModel):
    kind: BundleRootKind
    object_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )


class BundleExportRequest(BundleModel):
    mode: BundleMode = "REFERENCE_ONLY"
    root_objects: tuple[BundleRootObject, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_roots(self) -> BundleExportRequest:
        values = [(item.kind, item.object_id) for item in self.root_objects]
        if len(values) != len(set(values)):
            raise ValueError("Research Bundle root objects must be unique")
        return self


class BundleObject(BundleModel):
    kind: BundleObjectKind
    object_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    fingerprint: str
    path: str
    portable: bool = False


class BundleExternalDependency(BundleModel):
    kind: str
    object_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    reason: str


class ResearchBundleManifest(BundleModel):
    bundle_format_version: Literal["1.0"] = "1.0"
    bundle_id: str = Field(pattern=r"^research-bundle-[0-9a-f]{24}$")
    created_at: datetime
    app_version: str
    mode: BundleMode
    root_objects: tuple[BundleRootObject, ...]
    objects: tuple[BundleObject, ...]
    object_count: int = Field(ge=1)
    frozen_artifact_count: int = Field(default=0, ge=0)
    checksums: dict[str, str]
    external_dependencies: tuple[BundleExternalDependency, ...] = ()

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Research Bundle timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def inventory_is_consistent(self) -> ResearchBundleManifest:
        if self.object_count != len(self.objects):
            raise ValueError("Research Bundle object_count does not match its object inventory")
        keys = [(item.kind, item.object_id) for item in self.objects]
        if len(keys) != len(set(keys)):
            raise ValueError("Research Bundle object inventory contains duplicate identities")
        return self


class BundleConflict(BundleModel):
    kind: BundleObjectKind
    object_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    status: BundleConflictStatus
    detail: str


class BundleImportPreview(BundleModel):
    preview_id: str = Field(pattern=r"^bundle-preview-[0-9a-f]{20}$")
    manifest: ResearchBundleManifest
    valid: bool
    conflicts: tuple[BundleConflict, ...]
    external_dependencies: tuple[BundleExternalDependency, ...]
    errors: tuple[str, ...] = ()


class BundleImportResult(BundleModel):
    bundle_id: str = Field(pattern=r"^research-bundle-[0-9a-f]{24}$")
    imported: tuple[str, ...]
    reused: tuple[str, ...]
    unavailable: tuple[str, ...]
    target_workspace_id: str | None = None


class BundleImportRequest(BundleModel):
    target_workspace_id: str = Field(pattern=r"^(workspace-default|workspace-[0-9a-f]{24})$")
