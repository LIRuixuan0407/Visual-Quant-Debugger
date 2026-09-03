from __future__ import annotations

from app.diagnostics.models import (
    CostStressPoint,
    DiagnosisSourceRun,
    ExecutionDelayPoint,
    FailureFingerprint,
    FailureFingerprintDimension,
    FailureFingerprintKey,
    FailureSeverity,
    LookbackSensitivityPoint,
    RegimeDiagnostics,
    StatisticalDiagnostics,
    TrainTestSplit,
)


def _dimension(
    key: FailureFingerprintKey,
    title: str,
    severity: FailureSeverity,
    evidence: tuple[str, ...],
    calculation_details: tuple[str, ...],
) -> FailureFingerprintDimension:
    return FailureFingerprintDimension(
        key=key,
        title=title,
        severity=severity,
        evidence=evidence,
        calculation_details=calculation_details,
    )


def _oos_dimension(split: TrainTestSplit) -> FailureFingerprintDimension:
    if split.train.status != "OK" or split.test.status != "OK" or split.train.sharpe <= 0.25:
        return _dimension(
            "OOS_DEGRADATION",
            "Out-of-sample degradation",
            "NOT_AVAILABLE",
            (f"Train Sharpe {split.train.sharpe:.2f}; test Sharpe {split.test.sharpe:.2f}.",),
            (
                "A positive, defined train Sharpe above 0.25 is required before retention "
                "is graded.",
            ),
        )
    retention = split.test.sharpe / split.train.sharpe
    severity: FailureSeverity
    if split.test.sharpe <= 0.0 or retention < 0.40:
        severity = "HIGH"
    elif retention < 0.75:
        severity = "MEDIUM"
    else:
        severity = "LOW"
    return _dimension(
        "OOS_DEGRADATION",
        "Out-of-sample degradation",
        severity,
        (
            f"Train Sharpe {split.train.sharpe:.2f}; test Sharpe {split.test.sharpe:.2f}.",
            f"Test/train Sharpe retention {retention:.1%}.",
        ),
        (
            "High: test Sharpe is non-positive or retains under 40% of train Sharpe; "
            "medium: under 75%.",
        ),
    )


def _parameter_dimension(
    points: tuple[LookbackSensitivityPoint, ...],
) -> FailureFingerprintDimension:
    baseline = next((point for point in points if point.is_current), None)
    candidates = [point for point in points if not point.is_current and point.test.status == "OK"]
    if (
        baseline is None
        or baseline.test.status != "OK"
        or baseline.test.sharpe <= 0.25
        or len(candidates) < 2
    ):
        return _dimension(
            "PARAMETER_INSTABILITY",
            "Parameter instability",
            "NOT_AVAILABLE",
            (f"Evaluable neighboring parameter values: {len(candidates)}.",),
            (
                "A positive current test Sharpe and at least two evaluable alternatives are "
                "required.",
            ),
        )
    threshold = 0.8 * baseline.test.sharpe
    retained = sum(point.test.sharpe >= threshold for point in candidates)
    share = retained / len(candidates)
    severity: FailureSeverity = "HIGH" if share < 0.30 else "MEDIUM" if share < 0.60 else "LOW"
    return _dimension(
        "PARAMETER_INSTABILITY",
        "Parameter instability",
        severity,
        (
            f"Current test Sharpe {baseline.test.sharpe:.2f}.",
            f"{retained} of {len(candidates)} alternatives retain at least 80% of the "
            "current test Sharpe.",
        ),
        ("High: under 30% retention; medium: under 60%; low otherwise.",),
    )


def _cost_dimension(
    source: DiagnosisSourceRun,
    points: tuple[CostStressPoint, ...],
) -> FailureFingerprintDimension:
    if len(points) < 2:
        return _dimension(
            "COST_SENSITIVITY",
            "Transaction-cost sensitivity",
            "NOT_AVAILABLE",
            ("Fewer than two cost-stress reruns are available.",),
            ("Cost sensitivity is not inferred without rerun evidence.",),
        )
    current = sum(
        (
            source.fee_bps,
            source.slippage_bps,
            source.spread_bps / 2.0,
            source.market_impact_bps,
        )
    )
    baseline = min(points, key=lambda point: abs(point.total_friction_bps - current))
    stressed = max(points, key=lambda point: point.total_friction_bps)
    if (
        stressed.total_friction_bps <= baseline.total_friction_bps
        or baseline.metrics.total_return <= 0.0
    ):
        return _dimension(
            "COST_SENSITIVITY",
            "Transaction-cost sensitivity",
            "NOT_AVAILABLE",
            (
                f"Baseline return {baseline.metrics.total_return:.2%} at "
                f"{baseline.total_friction_bps:.0f} bps.",
                f"Highest tested friction {stressed.total_friction_bps:.0f} bps.",
            ),
            (
                "A positive baseline return and a strictly higher stress point are required "
                "for retention grading.",
            ),
        )
    retention = stressed.metrics.total_return / baseline.metrics.total_return
    severity: FailureSeverity = (
        "HIGH"
        if stressed.metrics.total_return <= 0.0 or retention < 0.50
        else "MEDIUM"
        if retention < 0.80
        else "LOW"
    )
    return _dimension(
        "COST_SENSITIVITY",
        "Transaction-cost sensitivity",
        severity,
        (
            f"Return changes from {baseline.metrics.total_return:.2%} at "
            f"{baseline.total_friction_bps:.0f} bps to {stressed.metrics.total_return:.2%} "
            f"at {stressed.total_friction_bps:.0f} bps.",
            f"Return retention {retention:.1%}.",
        ),
        ("High: stressed return is non-positive or retains under 50%; medium: under 80%.",),
    )


def _delay_dimension(points: tuple[ExecutionDelayPoint, ...]) -> FailureFingerprintDimension:
    baseline = next((point for point in points if point.additional_delay_bars == 0), None)
    stressed = max(points, key=lambda point: point.additional_delay_bars, default=None)
    if (
        baseline is None
        or stressed is None
        or stressed.additional_delay_bars == 0
        or baseline.metrics.total_return <= 0.0
    ):
        return _dimension(
            "EXECUTION_DELAY_SENSITIVITY",
            "Execution-delay sensitivity",
            "NOT_AVAILABLE",
            ("A positive baseline and delayed rerun are required.",),
            ("Execution-delay risk is not inferred without rerun evidence.",),
        )
    retention = stressed.metrics.total_return / baseline.metrics.total_return
    severity: FailureSeverity = (
        "HIGH"
        if stressed.metrics.total_return <= 0.0 or retention < 0.50
        else "MEDIUM"
        if retention < 0.80
        else "LOW"
    )
    return _dimension(
        "EXECUTION_DELAY_SENSITIVITY",
        "Execution-delay sensitivity",
        severity,
        (
            f"Return changes from {baseline.metrics.total_return:.2%} at baseline execution "
            f"to {stressed.metrics.total_return:.2%} with "
            f"+{stressed.additional_delay_bars} bars.",
            f"Delayed return retention {retention:.1%}; unfilled signals "
            f"{stressed.unfilled_signal_count}.",
        ),
        ("High: delayed return is non-positive or retains under 50%; medium: under 80%.",),
    )


def _regime_dimension(regime: RegimeDiagnostics | None) -> FailureFingerprintDimension:
    if regime is None or regime.status != "OK":
        return _dimension(
            "REGIME_DEPENDENCE",
            "Market-regime dependence",
            "NOT_AVAILABLE",
            ((regime.summary if regime is not None else "Regime diagnostics are unavailable."),),
            ("At least two evaluable market-regime buckets are required for dependence grading.",),
        )
    severity: FailureSeverity = (
        "HIGH"
        if regime.verdict == "REGIME_DEPENDENT"
        else "MEDIUM"
        if regime.verdict == "MIXED_REGIME_SENSITIVITY"
        else "LOW"
        if regime.verdict == "LIMITED_REGIME_SENSITIVITY"
        else "NOT_AVAILABLE"
    )
    return _dimension(
        "REGIME_DEPENDENCE",
        "Market-regime dependence",
        severity,
        (regime.summary,),
        (
            "Severity is derived from dispersion in evaluable regime Sharpe values, not from "
            "an AI score.",
        ),
    )


def _mean_reversion_dimension(
    strategy_id: str,
    statistical: StatisticalDiagnostics | None,
) -> FailureFingerprintDimension:
    if strategy_id != "pairs-trading":
        return _dimension(
            "MEAN_REVERSION_EVIDENCE",
            "Mean-reversion evidence",
            "NOT_AVAILABLE",
            ("This failure mode is only graded for the built-in pairs strategy.",),
            ("VQD does not infer a mean-reversion requirement for unrelated strategies.",),
        )
    pair = None if statistical is None else statistical.pair_mean_reversion
    if pair is None or pair.status != "OK" or pair.phi is None:
        return _dimension(
            "MEAN_REVERSION_EVIDENCE",
            "Mean-reversion evidence",
            "HIGH",
            ("The pairs strategy does not have enough valid AR(1) evidence for grading.",),
            (
                "Missing evidence is treated as a high diagnostic risk for a strategy whose "
                "premise is mean reversion.",
            ),
        )
    absolute_phi = abs(pair.phi)
    severity: FailureSeverity = (
        "HIGH" if absolute_phi >= 1.0 else "MEDIUM" if absolute_phi >= 0.90 else "LOW"
    )
    spread_acf = (
        "N/A"
        if pair.spread_lag_1_autocorrelation is None
        else f"{pair.spread_lag_1_autocorrelation:.3f}"
    )
    half_life = (
        f"Estimated half-life {pair.half_life_bars:.2f} bars."
        if pair.half_life_bars is not None
        else "Half-life is unavailable for this AR(1) estimate."
    )
    return _dimension(
        "MEAN_REVERSION_EVIDENCE",
        "Mean-reversion evidence",
        severity,
        (
            f"AR(1) phi {pair.phi:.3f}; lag-1 spread ACF {spread_acf}.",
            half_life,
        ),
        (
            "High: |phi| >= 1; medium: |phi| >= 0.90; low otherwise. This is evidence, "
            "not a stationarity proof.",
        ),
    )


def build_failure_fingerprint(
    *,
    source: DiagnosisSourceRun,
    train_test: TrainTestSplit,
    parameter_sensitivity: tuple[LookbackSensitivityPoint, ...],
    cost_stress: tuple[CostStressPoint, ...],
    execution_delay: tuple[ExecutionDelayPoint, ...],
    regime_diagnostics: RegimeDiagnostics | None,
    statistical_diagnostics: StatisticalDiagnostics | None,
) -> FailureFingerprint:
    dimensions = (
        _oos_dimension(train_test),
        _parameter_dimension(parameter_sensitivity),
        _cost_dimension(source, cost_stress),
        _delay_dimension(execution_delay),
        _regime_dimension(regime_diagnostics),
        _mean_reversion_dimension(source.strategy_id, statistical_diagnostics),
    )
    high_count = sum(item.severity == "HIGH" for item in dimensions)
    medium_count = sum(item.severity == "MEDIUM" for item in dimensions)
    available_count = sum(item.severity != "NOT_AVAILABLE" for item in dimensions)
    if available_count == 0:
        summary = "No failure modes have enough evidence for grading on this run."
    else:
        summary = (
            f"{high_count} high-severity and {medium_count} medium-severity failure modes "
            f"across {available_count} evidence-backed dimensions."
        )
    return FailureFingerprint(
        dimensions=dimensions,
        high_severity_count=high_count,
        medium_severity_count=medium_count,
        available_dimension_count=available_count,
        summary=summary,
        calculation_details=(
            "Failure Fingerprint uses deterministic thresholds over recorded diagnostics and "
            "rerun evidence.",
            "It is a triage summary, not a forecast, recommendation, or composite AI score.",
        ),
    )
