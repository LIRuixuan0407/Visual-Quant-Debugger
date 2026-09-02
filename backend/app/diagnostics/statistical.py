import math
from typing import Literal

import numpy as np

from app.diagnostics.models import (
    AutocorrelationPoint,
    PairMeanReversionEvidence,
    ReturnDiagnostics,
    StatisticalDiagnostics,
)
from app.trace.models import BacktestTrace

_ACF_LAGS = tuple(range(1, 11))
_MIN_PAIR_AR1_PAIRS = 20


def _finite_values(values: tuple[float | None, ...]) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    return array[np.isfinite(array)]


def _autocorrelation(values: np.ndarray, lag: int) -> float | None:
    if values.size <= lag:
        return None
    centered = values - float(np.mean(values))
    denominator = float(np.dot(centered, centered))
    if denominator == 0.0:
        return None
    value = float(np.dot(centered[:-lag], centered[lag:]) / denominator)
    return value if math.isfinite(value) else None


def _acf(values: np.ndarray) -> tuple[AutocorrelationPoint, ...]:
    points: list[AutocorrelationPoint] = []
    for lag in _ACF_LAGS:
        value = _autocorrelation(values, lag)
        points.append(
            AutocorrelationPoint(
                lag=lag,
                status="OK" if value is not None else "INSUFFICIENT_DATA",
                value=value,
            )
        )
    return tuple(points)


def calculate_return_diagnostics(equity: tuple[float, ...]) -> ReturnDiagnostics:
    returns: list[float] = []
    for previous, current in zip(equity, equity[1:], strict=False):
        if not math.isfinite(previous) or not math.isfinite(current) or previous == 0.0:
            continue
        value = current / previous - 1.0
        if math.isfinite(value):
            returns.append(value)

    values = np.asarray(returns, dtype=np.float64)
    squared_values = np.square(values)
    return_acf = _acf(values)
    squared_return_acf = _acf(squared_values)
    complete = all(point.status == "OK" for point in (*return_acf, *squared_return_acf))
    return ReturnDiagnostics(
        status="OK" if complete else "INSUFFICIENT_DATA",
        observation_count=int(values.size),
        return_acf=return_acf,
        squared_return_acf=squared_return_acf,
        lag_1_return_autocorrelation=return_acf[0].value,
        lag_1_squared_return_autocorrelation=squared_return_acf[0].value,
        note=(
            None
            if complete
            else (
                "At least eleven non-constant consecutive equity returns are required "
                "to estimate every requested ACF lag."
            )
        ),
    )


def calculate_pair_mean_reversion(
    spreads: tuple[float | None, ...], hedge_ratios: tuple[float | None, ...]
) -> PairMeanReversionEvidence:
    spread_values = _finite_values(spreads)
    hedge_values = _finite_values(hedge_ratios)
    adjacent = tuple(
        (float(previous), float(current))
        for previous, current in zip(spreads, spreads[1:], strict=False)
        if previous is not None
        and current is not None
        and math.isfinite(previous)
        and math.isfinite(current)
    )
    previous = np.asarray([pair[0] for pair in adjacent], dtype=np.float64)
    current = np.asarray([pair[1] for pair in adjacent], dtype=np.float64)
    phi: float | None = None
    spread_acf: float | None = None
    if previous.size >= _MIN_PAIR_AR1_PAIRS:
        centered_previous = previous - float(np.mean(previous))
        centered_current = current - float(np.mean(current))
        previous_sum_squares = float(np.dot(centered_previous, centered_previous))
        current_sum_squares = float(np.dot(centered_current, centered_current))
        if previous_sum_squares != 0.0:
            candidate = float(np.dot(centered_previous, centered_current) / previous_sum_squares)
            if math.isfinite(candidate):
                phi = candidate
        correlation_denominator = math.sqrt(previous_sum_squares * current_sum_squares)
        if correlation_denominator != 0.0:
            candidate = float(np.dot(centered_previous, centered_current) / correlation_denominator)
            if math.isfinite(candidate):
                spread_acf = candidate

    half_life: float | None = None
    if phi is not None and 0.0 < phi < 1.0:
        candidate = -math.log(2.0) / math.log(phi)
        if math.isfinite(candidate):
            half_life = candidate

    status: Literal["OK", "INSUFFICIENT_DATA"] = (
        "OK" if phi is not None and spread_acf is not None else "INSUFFICIENT_DATA"
    )
    return PairMeanReversionEvidence(
        status=status,
        observation_count=int(spread_values.size),
        consecutive_pair_count=int(previous.size),
        hedge_ratio_observation_count=int(hedge_values.size),
        phi=phi,
        spread_lag_1_autocorrelation=spread_acf,
        half_life_bars=half_life,
        hedge_ratio_mean=(None if hedge_values.size == 0 else float(np.mean(hedge_values))),
        hedge_ratio_std=(None if hedge_values.size == 0 else float(np.std(hedge_values, ddof=0))),
        note=(
            None
            if status == "OK"
            else (
                f"At least {_MIN_PAIR_AR1_PAIRS} time-adjacent, non-constant spread pairs "
                "are required for AR(1). Missing bars are never bridged."
            )
        ),
    )


def _feature_values(trace: BacktestTrace, name: str) -> tuple[float | None, ...]:
    values: list[float | None] = []
    for event in trace.timeline:
        snapshot = next((item for item in event.feature_snapshots if item.name == name), None)
        value = None if snapshot is None else snapshot.value
        values.append(value if value is not None and math.isfinite(value) else None)
    return tuple(values)


def build_statistical_diagnostics(trace: BacktestTrace) -> StatisticalDiagnostics:
    pair_evidence = None
    if trace.strategy.strategy_id == "pairs-trading":
        pair_evidence = calculate_pair_mean_reversion(
            _feature_values(trace, "spread"), _feature_values(trace, "hedge_ratio")
        )
    return StatisticalDiagnostics(
        returns=calculate_return_diagnostics(
            tuple(event.pnl_snapshot.equity for event in trace.timeline)
        ),
        pair_mean_reversion=pair_evidence,
    )
