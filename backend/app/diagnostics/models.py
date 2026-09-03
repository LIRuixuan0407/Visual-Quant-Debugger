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


def _finite_optional(value: float | None) -> float | None:
    return None if value is None else _finite(value)


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
    spread_bps: float = 0.0
    market_impact_bps: float = 0.0
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
    spread_bps: float = 0.0
    market_impact_bps: float = 0.0
    sensitivity_parameter: str | None = "lookback"


class DiagnosticSupportSet(DiagnosisModel):
    train_test: Literal["AVAILABLE", "NOT_SUPPORTED"] = "AVAILABLE"
    parameter_sensitivity: Literal["AVAILABLE", "NOT_SUPPORTED"] = "AVAILABLE"
    cost_stress: Literal["AVAILABLE", "NOT_SUPPORTED"] = "AVAILABLE"
    execution_delay: Literal["AVAILABLE", "NOT_SUPPORTED"] = "AVAILABLE"


class AutocorrelationPoint(DiagnosisModel):
    lag: int = Field(ge=1, le=10)
    status: Literal["OK", "INSUFFICIENT_DATA"]
    value: float | None = None

    _finite_value = field_validator("value")(_finite_optional)


class ReturnDiagnostics(DiagnosisModel):
    status: Literal["OK", "INSUFFICIENT_DATA"]
    observation_count: int = Field(ge=0)
    return_acf: tuple[AutocorrelationPoint, ...]
    squared_return_acf: tuple[AutocorrelationPoint, ...]
    lag_1_return_autocorrelation: float | None = None
    lag_1_squared_return_autocorrelation: float | None = None
    note: str | None = None

    _finite_values = field_validator(
        "lag_1_return_autocorrelation", "lag_1_squared_return_autocorrelation"
    )(_finite_optional)


class PairMeanReversionEvidence(DiagnosisModel):
    status: Literal["OK", "INSUFFICIENT_DATA"]
    observation_count: int = Field(ge=0)
    consecutive_pair_count: int = Field(ge=0)
    hedge_ratio_observation_count: int = Field(ge=0)
    phi: float | None = None
    spread_lag_1_autocorrelation: float | None = None
    half_life_bars: float | None = None
    hedge_ratio_mean: float | None = None
    hedge_ratio_std: float | None = None
    note: str | None = None

    _finite_values = field_validator(
        "phi",
        "spread_lag_1_autocorrelation",
        "half_life_bars",
        "hedge_ratio_mean",
        "hedge_ratio_std",
    )(_finite_optional)


class StatisticalDiagnostics(DiagnosisModel):
    returns: ReturnDiagnostics
    pair_mean_reversion: PairMeanReversionEvidence | None = None


class VolatilityRegimeThresholds(DiagnosisModel):
    low_upper_bound: float = 0.15
    high_lower_bound: float = 0.30

    _finite_values = field_validator("low_upper_bound", "high_lower_bound")(_finite)


class VolatilityPoint(DiagnosisModel):
    timestamp: datetime
    market_return: float | None = None
    rolling_historical_vol: float | None = None
    ewma_vol: float | None = None
    regime: Literal["LOW", "NORMAL", "HIGH"] | None = None

    _finite_values = field_validator("market_return", "rolling_historical_vol", "ewma_vol")(
        _finite_optional
    )


class VolatilityDrawdownOverlap(DiagnosisModel):
    episode_id: str
    rank_by_depth: int = Field(ge=1)
    start_time: datetime
    trough_time: datetime
    end_time: datetime
    max_drawdown: float
    start_regime: Literal["LOW", "NORMAL", "HIGH"] | None = None
    ewma_rising_at_start: bool | None = None
    regime_changed_at_start: bool | None = None

    _finite_drawdown = field_validator("max_drawdown")(_finite)


class VolatilityDiagnostics(DiagnosisModel):
    status: Literal["OK", "INSUFFICIENT_DATA", "UNSUPPORTED"]
    dataset_frequency: str
    rolling_window: int = Field(ge=2)
    ewma_decay: float = Field(gt=0, lt=1)
    annualization_factor: int | None = Field(default=None, gt=0)
    market_return_method: str
    thresholds: VolatilityRegimeThresholds
    points: tuple[VolatilityPoint, ...]
    current_regime: Literal["LOW", "NORMAL", "HIGH"] | None = None
    current_historical_vol: float | None = None
    current_ewma_vol: float | None = None
    drawdown_overlap: tuple[VolatilityDrawdownOverlap, ...]
    evaluable_drawdown_count: int = Field(ge=0)
    rising_volatility_start_count: int = Field(ge=0)
    regime_change_start_count: int = Field(ge=0)
    verdict: Literal[
        "RISING_VOLATILITY_OVERLAP",
        "MIXED_VOLATILITY_OVERLAP",
        "LIMITED_VOLATILITY_OVERLAP",
        "NO_DRAWDOWNS",
        "INSUFFICIENT_DATA",
        "UNSUPPORTED",
    ]
    summary: str
    calculation_details: tuple[str, ...]

    _finite_values = field_validator("current_historical_vol", "current_ewma_vol")(_finite_optional)


class WhatIfInputs(DiagnosisModel):
    fee_bps: float = Field(ge=0, le=10_000)
    slippage_bps: float = Field(ge=0, le=10_000)
    spread_bps: float = Field(default=0.0, ge=0, le=10_000)
    market_impact_bps: float = Field(default=0.0, ge=0, le=10_000)
    additional_execution_delay_bars: Literal[0, 1, 2] = 0
    strategy_parameters: dict[str, int | float] = Field(default_factory=dict)

    _finite_values = field_validator("fee_bps", "slippage_bps", "spread_bps", "market_impact_bps")(
        _finite
    )

    @field_validator("strategy_parameters")
    @classmethod
    def finite_strategy_parameters(cls, values: dict[str, int | float]) -> dict[str, int | float]:
        for key, value in values.items():
            if not key or isinstance(value, bool):
                raise ValueError("What-if strategy parameters must be named finite numbers")
            _finite(value)
        return values


class WhatIfMetrics(DiagnosisModel):
    total_return: float
    sharpe: float
    max_drawdown: float
    turnover: float
    trade_count: int = Field(ge=0)
    net_pnl: float

    _finite_values = field_validator(
        "total_return", "sharpe", "max_drawdown", "turnover", "net_pnl"
    )(_finite)


class WhatIfMetricDeltas(DiagnosisModel):
    total_return: float
    sharpe: float
    max_drawdown: float
    turnover: float
    trade_count: int
    net_pnl: float

    _finite_values = field_validator(
        "total_return", "sharpe", "max_drawdown", "turnover", "net_pnl"
    )(_finite)


class WhatIfParameterControl(DiagnosisModel):
    key: str
    label: str
    value_type: Literal["integer", "number"]
    current_value: int | float
    minimum: int | float
    maximum: int | float | None = None
    step: int | float
    unit: str

    _finite_values = field_validator("current_value", "minimum", "maximum", "step")(
        _finite_optional
    )


class WhatIfSupport(DiagnosisModel):
    status: Literal["AVAILABLE", "NOT_SUPPORTED"]
    baseline_inputs: WhatIfInputs | None = None
    baseline_metrics: WhatIfMetrics | None = None
    parameter: WhatIfParameterControl | None = None
    calculation_details: tuple[str, ...] = ()


class WhatIfScenario(DiagnosisModel):
    baseline_inputs: WhatIfInputs
    inputs: WhatIfInputs
    baseline_metrics: WhatIfMetrics
    stressed_metrics: WhatIfMetrics
    deltas: WhatIfMetricDeltas
    unfilled_signal_count: int = Field(ge=0)
    verdict: Literal["LOWER_NET_PNL", "HIGHER_NET_PNL", "UNCHANGED_NET_PNL"]
    evidence: tuple[str, ...]
    calculation_details: tuple[str, ...]


VolatilityRegime = Literal["LOW", "NORMAL", "HIGH"]
TrendRegime = Literal["UPTREND", "DOWNTREND", "SIDEWAYS"]
FailureSeverity = Literal["LOW", "MEDIUM", "HIGH", "NOT_AVAILABLE"]
FailureFingerprintKey = Literal[
    "OOS_DEGRADATION",
    "PARAMETER_INSTABILITY",
    "COST_SENSITIVITY",
    "EXECUTION_DELAY_SENSITIVITY",
    "REGIME_DEPENDENCE",
    "MEAN_REVERSION_EVIDENCE",
]


class RegimePerformance(DiagnosisModel):
    volatility_regime: VolatilityRegime
    trend_regime: TrendRegime
    observation_count: int = Field(ge=0)
    status: Literal["OK", "INSUFFICIENT_DATA"]
    total_return: float
    sharpe: float
    max_drawdown: float
    hit_rate: float = Field(ge=0, le=1)
    trade_count: int = Field(ge=0)
    turnover: float

    _finite_values = field_validator(
        "total_return", "sharpe", "max_drawdown", "hit_rate", "turnover"
    )(_finite)


class RegimeDiagnostics(DiagnosisModel):
    status: Literal["OK", "INSUFFICIENT_DATA", "UNSUPPORTED"]
    trend_window: int = Field(ge=2)
    trend_threshold: float = Field(gt=0)
    performance: tuple[RegimePerformance, ...]
    verdict: Literal[
        "REGIME_DEPENDENT",
        "MIXED_REGIME_SENSITIVITY",
        "LIMITED_REGIME_SENSITIVITY",
        "LIMITED_EVIDENCE",
        "UNSUPPORTED",
    ]
    summary: str
    calculation_details: tuple[str, ...]

    _finite_threshold = field_validator("trend_threshold")(_finite)


class FailureFingerprintDimension(DiagnosisModel):
    key: FailureFingerprintKey
    title: str
    severity: FailureSeverity
    evidence: tuple[str, ...]
    calculation_details: tuple[str, ...]


class FailureFingerprint(DiagnosisModel):
    dimensions: tuple[FailureFingerprintDimension, ...]
    high_severity_count: int = Field(ge=0)
    medium_severity_count: int = Field(ge=0)
    available_dimension_count: int = Field(ge=0)
    summary: str
    calculation_details: tuple[str, ...]


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
    statistical_diagnostics: StatisticalDiagnostics | None = None
    volatility_diagnostics: VolatilityDiagnostics | None = None
    what_if: WhatIfSupport | None = None
    regime_diagnostics: RegimeDiagnostics | None = None
    failure_fingerprint: FailureFingerprint | None = None
