from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.adapters.models import RuntimeDescriptor, native_runtime
from app.backtest import BacktestParameters
from app.corporate_actions.models import PriceAdjustmentPolicy
from app.models import MarketBar, MarketFrame
from app.sdk.models import RuntimeFailure
from app.trace.models import BacktestTrace, TraceScalar

RunStatus = Literal["RUNNING", "COMPLETED", "FAILED", "PARTIAL"]
Comparability = Literal["STRICTLY_COMPARABLE", "CONTEXTUALLY_COMPARABLE", "DESCRIPTIVE_ONLY"]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Run timestamps must be timezone-aware")
    return value


class RunModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StrategyRevision(RunModel):
    strategy_id: str
    name: str
    version: str
    class_name: str
    source_fingerprint: str
    original_source_path: str


class DatasetRevision(RunModel):
    dataset_id: str
    name: str
    content_fingerprint: str
    dataset_family_id: str | None = None
    revision: int = Field(default=1, ge=1)
    source_timezone: str
    symbols: tuple[str, ...] = ()


class ResearchPeriod(RunModel):
    start: datetime | None
    end: datetime | None
    cutoff: datetime | None

    @field_validator("start", "end", "cutoff")
    @classmethod
    def aware_when_present(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)


class ExecutionModelRevision(RunModel):
    execution_model_id: str = "next-close"
    version: str = "1.0"
    description: str = "signal at close(t); execute at close(t+1)"


class EnvironmentSnapshot(RunModel):
    python_version: str
    platform: str
    vqd_version: str


class RunMetrics(RunModel):
    total_return: float
    sharpe: float
    max_drawdown: float
    turnover: float
    trades: int
    final_equity: float
    fees: float
    slippage: float
    net_pnl: float


class ArtifactHashes(RunModel):
    strategy_source_sha256: str
    trace_sha256: str | None = None
    diagnostics_sha256: str | None = None
    pnl_autopsy_sha256: str | None = None
    adapter_manifest_sha256: str | None = None
    recorded_market_events_sha256: str | None = None
    runtime_consistency_sha256: str | None = None
    broker_events_sha256: str | None = None


class RunManifest(RunModel):
    run_version: Literal["1.0", "1.1"] = "1.1"
    run_id: str
    run_type: Literal["BACKTEST", "PAPER", "REFERENCE"] = "BACKTEST"
    run_fingerprint: str
    status: RunStatus
    created_at: datetime
    completed_at: datetime | None
    strategy: StrategyRevision
    dataset: DatasetRevision
    universe_id: str | None = None
    corporate_action_dataset_id: str | None = None
    price_adjustment_policy: PriceAdjustmentPolicy = "RAW"
    unresolved_corporate_action_ids: tuple[str, ...] = ()
    period: ResearchPeriod
    parameters: dict[str, int | float]
    execution_model: ExecutionModelRevision
    runtime: RuntimeDescriptor = Field(default_factory=native_runtime)
    engine: EnvironmentSnapshot
    trace_version: Literal["1.0"] = "1.0"
    trace_id: str | None = None
    metrics: RunMetrics | None = None
    artifacts: ArtifactHashes
    failure: RuntimeFailure | None = None
    reproduced_from_run_id: str | None = None

    _aware_created = field_validator("created_at")(_aware)

    @field_validator("completed_at")
    @classmethod
    def aware_completed(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)


class RunAnnotations(RunModel):
    display_name: str = ""
    note: str = ""
    tags: tuple[str, ...] = ()


class RunArtifactAvailability(RunModel):
    strategy_source: bool
    trace: bool
    diagnostics: bool
    pnl_autopsy: bool
    adapter_manifest: bool = False
    recorded_market_events: bool = False
    runtime_consistency: bool = False
    broker_events: bool = False


class RunListItem(RunModel):
    run_id: str
    run_type: Literal["BACKTEST", "PAPER", "REFERENCE"] = "BACKTEST"
    trace_id: str | None
    status: RunStatus
    created_at: datetime
    completed_at: datetime | None
    strategy_id: str
    strategy_name: str
    strategy_fingerprint: str
    dataset_id: str
    dataset_name: str
    dataset_fingerprint: str
    universe_id: str | None = None
    corporate_action_dataset_id: str | None = None
    price_adjustment_policy: PriceAdjustmentPolicy = "RAW"
    parameters: dict[str, int | float]
    period: ResearchPeriod
    metrics: RunMetrics | None
    run_fingerprint: str
    reproduced_from_run_id: str | None
    annotations: RunAnnotations
    runtime: RuntimeDescriptor = Field(default_factory=native_runtime)

    _aware_created = field_validator("created_at")(_aware)


class RunListResponse(RunModel):
    items: tuple[RunListItem, ...]
    total: int
    limit: int
    offset: int


class RunDetail(RunModel):
    manifest: RunManifest
    annotations: RunAnnotations
    artifacts: RunArtifactAvailability
    integrity: Literal["VERIFIED"] = "VERIFIED"
    current_strategy_fingerprint: str | None = None
    current_source_matches: bool | None = None


class StrategySourceArtifact(RunModel):
    run_id: str
    filename: Literal["strategy.py"] = "strategy.py"
    sha256: str
    source: str


class AnnotationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=10_000)
    tags: tuple[str, ...] = ()


class ContextComparison(RunModel):
    field: Literal[
        "strategy_revision",
        "dataset_revision",
        "market_evidence",
        "evaluation_period",
        "execution_model",
        "runtime",
    ]
    same: bool
    values: tuple[str, ...]


class ParameterComparison(RunModel):
    parameter: str
    values: tuple[TraceScalar | None, ...]
    changed: bool


class MetricComparison(RunModel):
    metric: str
    values: tuple[float | int | None, ...]
    differences_from_first: tuple[float | None, ...]


class EquityComparisonPoint(RunModel):
    timestamp: datetime
    values: tuple[float, ...]

    _aware_timestamp = field_validator("timestamp")(_aware)


class BehaviorDiffRow(RunModel):
    timestamp: datetime
    values: tuple[str, ...]
    event_ids: tuple[str | None, ...]

    _aware_timestamp = field_validator("timestamp")(_aware)


class BehavioralDivergence(RunModel):
    status: Literal["DIVERGENCE", "NO_BEHAVIORAL_DIVERGENCE"]
    kind: Literal["FEATURE", "CONDITION", "SIGNAL", "POSITION", "ORDER", "EXECUTION"] | None
    timestamp: datetime | None
    event_ids: tuple[str | None, ...]
    summary: str
    run_values: tuple[str, ...]
    associated_parameter_differences: tuple[str, ...]

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)


class RunComparisonReport(RunModel):
    report_version: Literal["1.0"] = "1.0"
    run_ids: tuple[str, ...]
    comparability: Comparability
    context_diff: tuple[ContextComparison, ...]
    parameter_diff: tuple[ParameterComparison, ...]
    metric_diff: tuple[MetricComparison, ...]
    equity_comparison: tuple[EquityComparisonPoint, ...]
    signal_comparison: tuple[BehaviorDiffRow, ...]
    execution_comparison: tuple[BehaviorDiffRow, ...]
    first_behavioral_divergence: BehavioralDivergence | None
    first_computational_divergence: BehavioralDivergence | None = None
    first_decision_divergence: BehavioralDivergence | None = None
    first_trading_divergence: BehavioralDivergence | None = None


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: tuple[str, ...] = Field(min_length=2, max_length=4)


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backtest_run_id: str
    paper_run_id: str


class ValidationCheck(RunModel):
    field: Literal["strategy_revision", "parameters", "symbols", "market_path", "execution_model"]
    same: bool
    reference_value: str
    paper_value: str


class ValidationDivergence(RunModel):
    status: Literal["MATCH", "DIVERGENCE"]
    layer: (
        Literal["DATA", "FEATURE", "DECISION", "ORDER", "EXECUTION", "PORTFOLIO", "P&L"] | None
    ) = None
    timestamp: datetime | None = None
    reference_value: str = ""
    paper_value: str = ""
    difference: str = ""
    reference_event_id: str | None = None
    paper_event_id: str | None = None

    @field_validator("timestamp")
    @classmethod
    def aware_divergence_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)


class PnLAttribution(RunModel):
    total_difference: float
    decision_difference: float | None
    execution_price_difference: float | None
    fees: float
    slippage: float
    residual_unattributed: float
    status: Literal["RECONCILED", "PARTIALLY_ATTRIBUTED", "NOT_AVAILABLE"]


class RunValidationReport(RunModel):
    report_version: Literal["1.0"] = "1.0"
    report_id: str
    backtest_run_id: str
    paper_run_id: str
    reference_run_id: str
    reference_trace_id: str | None
    paper_trace_id: str | None
    historical_comparability: Comparability
    strict_recorded_feed_status: Literal["MATCH", "FIRST_DIVERGENCE", "NO_TRACE"]
    checks: tuple[ValidationCheck, ...]
    first_divergence: ValidationDivergence
    pnl_attribution: PnLAttribution
    note: str


@dataclass(frozen=True, slots=True)
class BacktestRunRecord:
    run_id: str
    trace: BacktestTrace
    strategy_id: str
    parameters: BacktestParameters
    bars: tuple[MarketBar, ...]
    dataset_source: str
    frames: tuple[MarketFrame, ...] = ()
    parameter_values: dict[str, int | float] | None = None
    strategy_version: str = "0.1"
    strategy_fingerprint: str = "built-in"
    strategy_source_path: Path | None = None
    strategy_class_name: str | None = None
    dataset_id: str = "pairs-sample-v1"
    dataset_fingerprint: str = ""
    created_at: datetime | None = None
    research_cutoff: datetime | None = None
    status: Literal["COMPLETED", "PARTIAL"] = "COMPLETED"
