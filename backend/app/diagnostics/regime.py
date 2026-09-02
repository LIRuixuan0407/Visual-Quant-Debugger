from __future__ import annotations

import math
from collections import defaultdict
from typing import Literal

import numpy as np

from app.diagnostics.metrics import max_drawdown, sharpe
from app.diagnostics.models import (
    RegimeDiagnostics,
    RegimePerformance,
    TrendRegime,
    VolatilityDiagnostics,
    VolatilityRegime,
)
from app.diagnostics.volatility import market_returns
from app.trace.models import BacktestTrace

DEFAULT_TREND_WINDOW = 21
DEFAULT_TREND_THRESHOLD = 0.02
MIN_REGIME_OBSERVATIONS = 5


def classify_trend_regime(
    rolling_return: float,
    *,
    threshold: float = DEFAULT_TREND_THRESHOLD,
) -> TrendRegime:
    if rolling_return > threshold:
        return "UPTREND"
    if rolling_return < -threshold:
        return "DOWNTREND"
    return "SIDEWAYS"


def rolling_trend_regimes(
    returns: tuple[float | None, ...],
    *,
    window: int = DEFAULT_TREND_WINDOW,
    threshold: float = DEFAULT_TREND_THRESHOLD,
) -> tuple[TrendRegime | None, ...]:
    if window < 2:
        raise ValueError("Trend window must be at least two observations")
    output: list[TrendRegime | None] = []
    for index in range(len(returns)):
        start = index - window + 1
        values = returns[start : index + 1] if start >= 0 else ()
        if len(values) != window or any(value is None for value in values):
            output.append(None)
            continue
        array = np.asarray(values, dtype=np.float64)
        rolling_return = float(np.prod(1.0 + array) - 1.0)
        output.append(
            classify_trend_regime(rolling_return, threshold=threshold)
            if math.isfinite(rolling_return)
            else None
        )
    return tuple(output)


def _strategy_returns(trace: BacktestTrace) -> tuple[float, ...]:
    if not trace.timeline:
        return ()
    first = trace.timeline[0]
    initial_equity = first.pnl_snapshot.equity - first.pnl_snapshot.period_net_pnl
    previous_equity = initial_equity
    output: list[float] = []
    for event in trace.timeline:
        current_equity = event.pnl_snapshot.equity
        if previous_equity <= 0.0:
            output.append(0.0)
        else:
            value = current_equity / previous_equity - 1.0
            output.append(value if math.isfinite(value) else 0.0)
        previous_equity = current_equity
    return tuple(output)


def _regime_metrics(
    trace: BacktestTrace,
    strategy_returns: tuple[float, ...],
    indexes: tuple[int, ...],
    *,
    volatility_regime: VolatilityRegime,
    trend_regime: TrendRegime,
) -> RegimePerformance:
    selected_returns = np.asarray([strategy_returns[index] for index in indexes], dtype=np.float64)
    cumulative = np.cumprod(1.0 + selected_returns)
    total_return = float(cumulative[-1] - 1.0) if cumulative.size else 0.0
    synthetic_equity = tuple(float(value) for value in cumulative)
    average_equity = float(
        np.mean(np.asarray([trace.timeline[index].pnl_snapshot.equity for index in indexes]))
    )
    traded_notional = sum(
        execution.traded_notional
        for index in indexes
        for execution in trace.timeline[index].execution_events
    )
    timestamps = {trace.timeline[index].timestamp for index in indexes}
    trade_count = sum(trade.opened_at in timestamps for trade in trace.trades)
    status: Literal["OK", "INSUFFICIENT_DATA"] = (
        "OK" if len(indexes) >= MIN_REGIME_OBSERVATIONS else "INSUFFICIENT_DATA"
    )
    return RegimePerformance(
        volatility_regime=volatility_regime,
        trend_regime=trend_regime,
        observation_count=len(indexes),
        status=status,
        total_return=total_return,
        sharpe=sharpe(selected_returns),
        max_drawdown=max_drawdown(synthetic_equity, 1.0),
        hit_rate=float(np.mean(selected_returns > 0.0)) if selected_returns.size else 0.0,
        trade_count=trade_count,
        turnover=traded_notional / average_equity if average_equity > 0.0 else 0.0,
    )


def build_regime_diagnostics(
    trace: BacktestTrace,
    volatility: VolatilityDiagnostics | None,
    *,
    trend_window: int = DEFAULT_TREND_WINDOW,
    trend_threshold: float = DEFAULT_TREND_THRESHOLD,
) -> RegimeDiagnostics:
    if volatility is None or volatility.status == "UNSUPPORTED":
        return RegimeDiagnostics(
            status="UNSUPPORTED",
            trend_window=trend_window,
            trend_threshold=trend_threshold,
            performance=(),
            verdict="UNSUPPORTED",
            summary="Regime diagnostics require supported volatility diagnostics.",
            calculation_details=(
                "VQD does not infer a regime matrix when annualized volatility is unsupported.",
            ),
        )
    if volatility.status != "OK" or len(volatility.points) != len(trace.timeline):
        return RegimeDiagnostics(
            status="INSUFFICIENT_DATA",
            trend_window=trend_window,
            trend_threshold=trend_threshold,
            performance=(),
            verdict="LIMITED_EVIDENCE",
            summary="There is not enough aligned market evidence to evaluate strategy regimes.",
            calculation_details=(
                "Volatility and strategy timeline observations must be aligned before regime "
                "attribution.",
            ),
        )

    market = market_returns(trace)
    trends = rolling_trend_regimes(
        market,
        window=trend_window,
        threshold=trend_threshold,
    )
    strategy = _strategy_returns(trace)
    grouped: dict[tuple[VolatilityRegime, TrendRegime], list[int]] = defaultdict(list)
    for index, point in enumerate(volatility.points):
        trend = trends[index]
        if point.regime is None or trend is None:
            continue
        grouped[(point.regime, trend)].append(index)

    performance = tuple(
        _regime_metrics(
            trace,
            strategy,
            tuple(indexes),
            volatility_regime=volatility_regime,
            trend_regime=trend_regime,
        )
        for (volatility_regime, trend_regime), indexes in sorted(grouped.items())
    )
    evaluable = [item for item in performance if item.status == "OK"]
    if len(evaluable) < 2:
        verdict: Literal[
            "REGIME_DEPENDENT",
            "MIXED_REGIME_SENSITIVITY",
            "LIMITED_REGIME_SENSITIVITY",
            "LIMITED_EVIDENCE",
            "UNSUPPORTED",
        ] = "LIMITED_EVIDENCE"
        summary = "Fewer than two regime buckets have enough observations for comparison."
    else:
        best = max(evaluable, key=lambda item: item.sharpe)
        worst = min(evaluable, key=lambda item: item.sharpe)
        spread = best.sharpe - worst.sharpe
        if best.sharpe > 0.0 > worst.sharpe and spread >= 1.5:
            verdict = "REGIME_DEPENDENT"
        elif spread >= 1.0:
            verdict = "MIXED_REGIME_SENSITIVITY"
        else:
            verdict = "LIMITED_REGIME_SENSITIVITY"
        summary = (
            f"Best regime Sharpe {best.sharpe:.2f} "
            f"({best.volatility_regime} / {best.trend_regime}); worst {worst.sharpe:.2f} "
            f"({worst.volatility_regime} / {worst.trend_regime})."
        )

    return RegimeDiagnostics(
        status="OK" if performance else "INSUFFICIENT_DATA",
        trend_window=trend_window,
        trend_threshold=trend_threshold,
        performance=performance,
        verdict=verdict,
        summary=summary,
        calculation_details=(
            f"Trend regime uses the compounded equal-weight market return over {trend_window} "
            "observations.",
            f"Uptrend is above +{trend_threshold:.1%}; downtrend is below "
            f"-{trend_threshold:.1%}; otherwise sideways.",
            "Each strategy bar is assigned to one volatility/trend bucket using only information "
            "available at that bar.",
            "Regime total return compounds only returns observed in that bucket; regime max "
            "drawdown is computed on that conditional return sequence.",
            f"A bucket needs at least {MIN_REGIME_OBSERVATIONS} observations to be treated as "
            "evaluable.",
            "Regime differences are descriptive evidence and do not establish causality or "
            "future performance.",
        ),
    )
