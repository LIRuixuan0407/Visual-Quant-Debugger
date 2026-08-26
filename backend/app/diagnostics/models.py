from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _finite(value: float) -> float:
    if (
        not isinstance(value, (int, float))
        or value != value
        or value in (float("inf"), -float("inf"))
    ):
        raise ValueError("Diagnostic metrics must be finite")
    return float(value)


class DiagnosisModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class DiagnosticMetrics(DiagnosisModel):
    status: Literal["OK", "INSUFFICIENT_DATA", "NO_TRADES", "UNDEFINED_SHARPE"]
    total_return: float = Field(serialization_alias="return")
    sharpe: float
    max_drawdown: float
    turnover: float
    trade_count: int
    final_equity: float
    bar_count: int
    note: str | None = None

    _finite_values = field_validator(
        "total_return", "sharpe", "max_drawdown", "turnover", "final_equity"
    )(_finite)


class TrainTestSplit(DiagnosisModel):
    method: Literal["chronological-70-30"] = "chronological-70-30"
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_bar_count: int
    test_bar_count: int
    feature_context_policy: str
    pnl_isolation_policy: str
    train: DiagnosticMetrics
    test: DiagnosticMetrics


class LookbackSensitivityPoint(DiagnosisModel):
    lookback: int
    is_current: bool
    train: DiagnosticMetrics
    test: DiagnosticMetrics


class CostStressPoint(DiagnosisModel):
    total_friction_bps: float
    fee_bps: float
    slippage_bps: float
    metrics: DiagnosticMetrics


class ExecutionDelayPoint(DiagnosisModel):
    additional_delay_bars: Literal[0, 1, 2]
    execution_offset_bars: Literal[1, 2, 3]
    unfilled_signal_count: int
    metrics: DiagnosticMetrics


class DiagnosisObservation(DiagnosisModel):
    observation_id: str
    title: str
    detail: str
    evidence: str


class DiagnosisSourceRun(DiagnosisModel):
    trace_id: str
    strategy_id: str
    dataset_id: str
    dataset_name: str
    dataset_source: str
    bar_count: int
    current_lookback: int
    fee_bps: float
    slippage_bps: float
    sensitivity_parameter: str | None = "lookback"


class DiagnosticSupportSet(DiagnosisModel):
    train_test: Literal["AVAILABLE", "NOT_SUPPORTED"] = "AVAILABLE"
    parameter_sensitivity: Literal["AVAILABLE", "NOT_SUPPORTED"] = "AVAILABLE"
    cost_stress: Literal["AVAILABLE", "NOT_SUPPORTED"] = "AVAILABLE"
    execution_delay: Literal["AVAILABLE", "NOT_SUPPORTED"] = "AVAILABLE"


class DiagnosisReport(DiagnosisModel):
    report_version: Literal["1.0"] = "1.0"
    source_run: DiagnosisSourceRun
    train_test: TrainTestSplit
    lookback_sensitivity: tuple[LookbackSensitivityPoint, ...]
    cost_stress: tuple[CostStressPoint, ...]
    execution_delay: tuple[ExecutionDelayPoint, ...]
    observations: tuple[DiagnosisObservation, ...]
    sensitivity_available: bool = True
    support: DiagnosticSupportSet = DiagnosticSupportSet()
