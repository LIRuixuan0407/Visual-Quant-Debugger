from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DriftBaselineType = Literal["RUN", "SNAPSHOT"]
DriftObservedType = Literal["FORWARD_SESSION", "PAPER_SESSION", "PAPER_RUN"]
DriftDimension = Literal["FACTOR", "SIGNAL", "TURNOVER", "EXPOSURE", "PERFORMANCE"]
DriftMetricStatus = Literal["STABLE", "WATCH", "DRIFT", "INSUFFICIENT_EVIDENCE"]
DriftOverallStatus = Literal["STABLE", "WATCH", "DRIFT", "INCOMPLETE"]
DriftComparability = Literal[
    "STRICTLY_COMPARABLE",
    "CONTEXTUALLY_COMPARABLE",
    "DESCRIPTIVE_ONLY",
    "CONFIGURATION_CHANGED",
]
DriftSourceStatus = Literal["COMPLETED", "PARTIAL"]

DRIFT_DISCLOSURE = (
    "Strategy Drift detects and locates deterministic distribution and behavior changes in "
    "recorded evidence. It does not explain causes, predict future performance, optimize "
    "thresholds, or declare that a strategy has permanently failed."
)


class DriftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware_when_present(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("Strategy Drift timestamps must be timezone-aware")
    return value


class CreateStrategyDriftReport(DriftModel):
    baseline_type: DriftBaselineType
    baseline_id: str = Field(min_length=1)
    observed_type: DriftObservedType
    observed_id: str = Field(min_length=1)
    window_bars: int = Field(default=20, ge=5, le=500)


class DriftSource(DriftModel):
    source_type: DriftBaselineType | DriftObservedType
    source_id: str
    resolved_run_id: str | None = None
    trace_id: str | None = None
    strategy_id: str
    strategy_fingerprint: str | None
    parameters: dict[str, str | int | float | bool]
    execution_model: str
    runtime: str
    dataset_id: str
    dataset_revision: str | None
    sample_size: int = Field(ge=0)
    observed_until: datetime | None = None
    status: DriftSourceStatus

    _aware_observed = field_validator("observed_until")(_aware_when_present)


class DriftComparabilityCheck(DriftModel):
    field: Literal[
        "strategy_id",
        "strategy_fingerprint",
        "parameters",
        "execution_model",
        "runtime",
    ]
    baseline_value: str
    observed_value: str
    same: bool
    blocking: bool


class DriftMetric(DriftModel):
    metric: str
    baseline_value: float | None
    observed_value: float | None
    relative_change: float | None
    normalized_distance: float | None
    status: DriftMetricStatus


class DriftDimensionReport(DriftModel):
    dimension: DriftDimension
    status: DriftMetricStatus
    metrics: tuple[DriftMetric, ...]
    first_drift_at: datetime | None = None
    first_drift_event_id: str | None = None
    evidence: tuple[str, ...]

    _aware_first = field_validator("first_drift_at")(_aware_when_present)


class DriftWindowDimension(DriftModel):
    dimension: DriftDimension
    status: DriftMetricStatus
    maximum_normalized_distance: float | None


class DriftTimelineWindow(DriftModel):
    window_index: int = Field(ge=1)
    start_at: datetime
    end_at: datetime
    end_event_id: str
    sample_size: int = Field(ge=1)
    complete: bool
    dimensions: tuple[DriftWindowDimension, ...]

    _aware_times = field_validator("start_at", "end_at")(_aware_when_present)


class StrategyDriftReport(DriftModel):
    drift_report_id: str
    drift_rule_version: Literal["1.0"] = "1.0"
    baseline_type: DriftBaselineType
    baseline_id: str
    observed_type: DriftObservedType
    observed_id: str
    created_at: datetime
    window_bars: int = Field(ge=5, le=500)
    baseline: DriftSource
    observed: DriftSource
    comparability: DriftComparability
    comparability_checks: tuple[DriftComparabilityCheck, ...]
    overall_status: DriftOverallStatus
    dimensions: tuple[DriftDimensionReport, ...]
    timeline: tuple[DriftTimelineWindow, ...]
    first_drift_at: datetime | None = None
    first_drift_dimension: DriftDimension | None = None
    first_drift_event_id: str | None = None
    disclosure: str = DRIFT_DISCLOSURE

    _aware_times = field_validator("created_at", "first_drift_at")(_aware_when_present)


class StrategyDriftSummary(DriftModel):
    drift_report_id: str
    baseline_type: DriftBaselineType
    baseline_id: str
    observed_type: DriftObservedType
    observed_id: str
    created_at: datetime
    comparability: DriftComparability
    overall_status: DriftOverallStatus
    first_drift_at: datetime | None
    first_drift_dimension: DriftDimension | None
    sample_size: int = Field(ge=0)

    _aware_times = field_validator("created_at", "first_drift_at")(_aware_when_present)
