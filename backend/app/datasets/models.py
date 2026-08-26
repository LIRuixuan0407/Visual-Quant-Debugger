from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DatasetModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Dataset timestamps must be timezone-aware")
    return value


class DataQualityReport(DatasetModel):
    status: Literal["VALID", "WARNING"]
    rows: int
    symbols: int
    start: datetime
    end: datetime
    duplicates: int
    missing_required_values: int
    rows_reordered: int
    alignment_gaps: int
    timezone: str
    issues: tuple[str, ...] = ()

    _aware_times = field_validator("start", "end")(lambda value: require_aware(value))


class DatasetProvenance(DatasetModel):
    provider: str
    feed: str
    requested_symbols: tuple[str, ...]
    requested_start: datetime
    requested_end: datetime
    retrieved_at: datetime
    market_timestamp_start: datetime
    market_timestamp_end: datetime

    _aware_times = field_validator(
        "requested_start",
        "requested_end",
        "retrieved_at",
        "market_timestamp_start",
        "market_timestamp_end",
    )(lambda value: require_aware(value))


class DatasetDefinition(DatasetModel):
    dataset_id: str
    name: str
    source_type: Literal["CSV", "BUILT_IN", "PROVIDER"]
    timezone: str
    frequency: str
    symbols: tuple[str, ...]
    fields: tuple[str, ...]
    row_count: int
    synchronized_bar_count: int
    start_time: datetime
    end_time: datetime
    created_at: datetime
    content_fingerprint: str
    source_timezone: str
    column_mapping: dict[str, str]
    quality: DataQualityReport
    provenance: DatasetProvenance | None = None
    security_names: dict[str, str] = Field(default_factory=dict)

    _aware_times = field_validator("start_time", "end_time", "created_at")(
        lambda value: require_aware(value)
    )


class DatasetPreview(DatasetModel):
    preview_id: str
    filename: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    detected_types: dict[str, str]
    detected_timezone: str | None
    candidate_mapping: dict[str, str]


class DatasetImportRequest(DatasetModel):
    preview_id: str
    name: str
    mapping: dict[str, str]
    timezone: str | None = None
    frequency: str | None = None


class CompatibilityCheck(DatasetModel):
    strategy_id: str
    dataset_id: str
    compatible: bool
    required_fields: tuple[str, ...]
    provided_fields: tuple[str, ...]
    required_symbol_count: int | None
    provided_symbol_count: int
    required_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    minimum_history: int
    synchronized_bar_count: int
    reasons: tuple[str, ...]
