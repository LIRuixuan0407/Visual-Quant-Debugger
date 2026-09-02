import math
from typing import Literal

import numpy as np

from app.autopsy.engine import detect_drawdown_episodes
from app.autopsy.models import EquityPoint
from app.diagnostics.models import (
    VolatilityDiagnostics,
    VolatilityDrawdownOverlap,
    VolatilityPoint,
    VolatilityRegimeThresholds,
)
from app.trace.models import BacktestTrace, TimelineEvent

DEFAULT_ROLLING_WINDOW = 21
DEFAULT_EWMA_DECAY = 0.94
DEFAULT_ANNUALIZATION_FACTOR = 252
DEFAULT_LOW_VOL_THRESHOLD = 0.15
DEFAULT_HIGH_VOL_THRESHOLD = 0.30


def classify_volatility_regime(
    volatility: float,
    *,
    low_upper_bound: float = DEFAULT_LOW_VOL_THRESHOLD,
    high_lower_bound: float = DEFAULT_HIGH_VOL_THRESHOLD,
) -> Literal["LOW", "NORMAL", "HIGH"]:
    if volatility < low_upper_bound:
        return "LOW"
    if volatility < high_lower_bound:
        return "NORMAL"
    return "HIGH"


def rolling_historical_volatility(
    returns: tuple[float | None, ...],
    *,
    window: int = DEFAULT_ROLLING_WINDOW,
    annualization_factor: int = DEFAULT_ANNUALIZATION_FACTOR,
) -> tuple[float | None, ...]:
    if window < 2:
        raise ValueError("Historical volatility window must be at least two")
    output: list[float | None] = []
    for index in range(len(returns)):
        start = index - window + 1
        values = returns[start : index + 1] if start >= 0 else ()
        if len(values) != window or any(value is None for value in values):
            output.append(None)
            continue
        array = np.asarray(values, dtype=np.float64)
        volatility = float(np.std(array, ddof=1) * math.sqrt(annualization_factor))
        output.append(volatility if math.isfinite(volatility) else None)
    return tuple(output)


def ewma_volatility(
    returns: tuple[float | None, ...],
    *,
    decay: float = DEFAULT_EWMA_DECAY,
    annualization_factor: int = DEFAULT_ANNUALIZATION_FACTOR,
) -> tuple[float | None, ...]:
    if not 0.0 < decay < 1.0:
        raise ValueError("EWMA decay must be between zero and one")
    variance: float | None = None
    output: list[float | None] = []
    for value in returns:
        if value is None or not math.isfinite(value):
            output.append(None)
            continue
        variance = (
            value * value
            if variance is None
            else decay * variance + (1.0 - decay) * value * value
        )
        volatility = math.sqrt(variance * annualization_factor)
        output.append(volatility if math.isfinite(volatility) else None)
    return tuple(output)


def _close_values(event: TimelineEvent) -> dict[str, float]:
    return {
        item.symbol: item.value
        for item in event.market_snapshot.values
        if item.field.lower() == "close" and math.isfinite(item.value) and item.value > 0.0
    }


def _market_returns(trace: BacktestTrace) -> tuple[float | None, ...]:
    output: list[float | None] = [None]
    for previous_event, current_event in zip(trace.timeline, trace.timeline[1:], strict=False):
        previous = _close_values(previous_event)
        current = _close_values(current_event)
        if not previous or previous.keys() != current.keys():
            output.append(None)
            continue
        returns = tuple(current[symbol] / previous[symbol] - 1.0 for symbol in sorted(current))
        value = float(np.mean(np.asarray(returns, dtype=np.float64)))
        output.append(value if math.isfinite(value) else None)
    return tuple(output)


def _verdict(
    *,
    status: Literal["OK", "INSUFFICIENT_DATA"],
    drawdown_count: int,
    evaluable_count: int,
    rising_count: int,
) -> Literal[
    "RISING_VOLATILITY_OVERLAP",
    "MIXED_VOLATILITY_OVERLAP",
    "LIMITED_VOLATILITY_OVERLAP",
    "NO_DRAWDOWNS",
    "INSUFFICIENT_DATA",
]:
    if status != "OK" or evaluable_count == 0:
        return "NO_DRAWDOWNS" if drawdown_count == 0 and status == "OK" else "INSUFFICIENT_DATA"
    if rising_count * 2 > evaluable_count:
        return "RISING_VOLATILITY_OVERLAP"
    if rising_count == 0:
        return "LIMITED_VOLATILITY_OVERLAP"
    return "MIXED_VOLATILITY_OVERLAP"


def build_volatility_diagnostics(
    trace: BacktestTrace,
    *,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
    ewma_decay: float = DEFAULT_EWMA_DECAY,
    annualization_factor: int = DEFAULT_ANNUALIZATION_FACTOR,
) -> VolatilityDiagnostics:
    market_returns = _market_returns(trace) if trace.timeline else ()
    historical = rolling_historical_volatility(
        market_returns, window=rolling_window, annualization_factor=annualization_factor
    )
    ewma = ewma_volatility(
        market_returns, decay=ewma_decay, annualization_factor=annualization_factor
    )
    regimes = tuple(
        None if value is None else classify_volatility_regime(value) for value in ewma
    )
    points = tuple(
        VolatilityPoint(
            timestamp=event.timestamp,
            market_return=market_returns[index],
            rolling_historical_vol=historical[index],
            ewma_vol=ewma[index],
            regime=regimes[index],
        )
        for index, event in enumerate(trace.timeline)
    )
    equity_points = tuple(
        EquityPoint(
            event_id=event.event_id,
            timestamp=event.timestamp,
            equity=event.pnl_snapshot.equity,
        )
        for event in trace.timeline
    )
    important = sorted(
        detect_drawdown_episodes(equity_points), key=lambda item: item.rank_by_depth
    )[:4]
    point_index = {point.timestamp: index for index, point in enumerate(points)}
    overlaps: list[VolatilityDrawdownOverlap] = []
    for episode in important:
        index = point_index[episode.drawdown_start_time]
        previous = points[index - 1] if index > 0 else None
        current = points[index]
        rising = (
            None
            if previous is None or previous.ewma_vol is None or current.ewma_vol is None
            else current.ewma_vol > previous.ewma_vol
        )
        changed = (
            None
            if previous is None or previous.regime is None or current.regime is None
            else current.regime != previous.regime
        )
        overlaps.append(
            VolatilityDrawdownOverlap(
                episode_id=episode.episode_id,
                rank_by_depth=episode.rank_by_depth,
                start_time=episode.drawdown_start_time,
                trough_time=episode.trough_time,
                end_time=episode.recovery_time or trace.timeline[-1].timestamp,
                max_drawdown=episode.max_drawdown,
                start_regime=current.regime,
                ewma_rising_at_start=rising,
                regime_changed_at_start=changed,
            )
        )

    evaluable = sum(item.ewma_rising_at_start is not None for item in overlaps)
    rising_count = sum(item.ewma_rising_at_start is True for item in overlaps)
    regime_change_count = sum(item.regime_changed_at_start is True for item in overlaps)
    status: Literal["OK", "INSUFFICIENT_DATA"] = (
        "OK" if any(value is not None for value in historical) else "INSUFFICIENT_DATA"
    )
    if overlaps and evaluable:
        summary = (
            f"{rising_count} of the {evaluable} evaluable largest drawdowns began while "
            "EWMA volatility was rising."
        )
    elif not overlaps:
        summary = "No strategy drawdown episodes are available for volatility overlap analysis."
    else:
        summary = "Drawdown overlap is unavailable until volatility warm-up is complete."
    return VolatilityDiagnostics(
        status=status,
        rolling_window=rolling_window,
        ewma_decay=ewma_decay,
        annualization_factor=annualization_factor,
        market_return_method=(
            "At each bar, compute each recorded symbol's simple close-to-close return, then "
            "take their equal-weight mean."
        ),
        thresholds=VolatilityRegimeThresholds(),
        points=points,
        current_regime=next((value for value in reversed(regimes) if value is not None), None),
        current_historical_vol=next(
            (value for value in reversed(historical) if value is not None), None
        ),
        current_ewma_vol=next((value for value in reversed(ewma) if value is not None), None),
        drawdown_overlap=tuple(overlaps),
        evaluable_drawdown_count=evaluable,
        rising_volatility_start_count=rising_count,
        regime_change_start_count=regime_change_count,
        verdict=_verdict(
            status=status,
            drawdown_count=len(overlaps),
            evaluable_count=evaluable,
            rising_count=rising_count,
        ),
        summary=summary,
        calculation_details=(
            f"Historical volatility uses the sample standard deviation of {rolling_window} "
            f"equal-weight market returns, annualized by sqrt({annualization_factor}).",
            f"EWMA variance uses lambda={ewma_decay:.2f} and zero-mean returns: variance[t] = "
            "lambda * variance[t-1] + (1-lambda) * return[t]^2.",
            "Regimes use annualized EWMA volatility: Low below 15%, Normal from 15% to "
            "below 30%, High at or above 30%.",
            "Drawdown overlays come from the recorded strategy equity curve; overlap is "
            "descriptive and does not establish causality.",
        ),
    )
