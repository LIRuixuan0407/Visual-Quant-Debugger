from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.models import DecisionCondition, FeaturePoint, MarketBar, SignalDecision
from app.sdk import (
    DataRequirements,
    DiagnosticCapabilities,
    FeatureRef,
    StrategyContext,
    StrategyMetadata,
    TargetPortfolioIntent,
    VQDStrategy,
    parameter,
)


@dataclass(frozen=True, slots=True)
class PairsTradingParameters:
    lookback: int = 60
    entry_z: float = 2.0
    exit_z: float = 0.5

    def __post_init__(self) -> None:
        if self.lookback < 2:
            raise ValueError("lookback must be at least 2")
        if self.entry_z <= 0:
            raise ValueError("entry_z must be positive")
        if self.exit_z < 0 or self.exit_z >= self.entry_z:
            raise ValueError("exit_z must be non-negative and smaller than entry_z")


class PairsTradingStrategy(VQDStrategy):
    """The built-in strategy expressed entirely through the native incremental SDK."""

    metadata = StrategyMetadata(
        strategy_id="pairs-trading",
        name="Pairs Trading",
        version="0.1",
        description=(
            "Models the changing price relationship between two assets, then trades temporary "
            "departures from that relationship with explicit next-bar execution."
        ),
        data_requirements=DataRequirements(
            required_fields=("close",), symbol_count=2, minimum_history=3
        ),
        diagnostic_capabilities=DiagnosticCapabilities(parameter_sensitivity="lookback"),
    )
    lookback = parameter(
        default=60,
        minimum=2,
        step=1,
        label="Lookback",
        unit="bars",
        description="Historical observations used for regression and rolling statistics.",
    )
    entry_z = parameter(
        default=2.0,
        minimum=0.01,
        step=0.1,
        label="Entry Z",
        unit="σ",
        description="Absolute z-score threshold for opening a spread position.",
    )
    exit_z = parameter(
        default=0.5,
        minimum=0.0,
        step=0.1,
        label="Exit Z",
        unit="σ",
        description="Absolute z-score threshold for closing a spread position.",
    )

    def __init__(self, gross_target: float = 20_000.0) -> None:
        self.gross_target = gross_target
        self._spreads: list[float | None] = []
        self._spread_refs: list[FeatureRef] = []
        self._target: Literal[-1, 0, 1] = 0

    def initialize(self, context: StrategyContext) -> None:
        self._spreads.clear()
        self._spread_refs.clear()
        self._target = 0

    @staticmethod
    def _state(target: int) -> str:
        return "LONG_SPREAD" if target > 0 else "SHORT_SPREAD" if target < 0 else "FLAT"

    def on_bar(self, context: StrategyContext) -> TargetPortfolioIntent:
        if len(context.symbols) != 2:
            raise ValueError("Pairs Trading requires exactly two configured symbols")
        asset_a, asset_b = context.symbols
        a_history = context.history(symbol=asset_a, bars=self.lookback)
        b_history = context.history(symbol=asset_b, bars=self.lookback)
        current_a = context.current(asset_a)
        current_b = context.current(asset_b)
        hedge_ratio: float | None = None
        spread: float | None = None
        if len(a_history) == self.lookback and len(b_history) == self.lookback:
            x = np.asarray(tuple(b_history), dtype=np.float64)
            y = np.asarray(tuple(a_history), dtype=np.float64)
            denominator = float(np.dot(x, x))
            if denominator == 0:
                raise ValueError("Cannot regress against a zero-valued Asset B window")
            hedge_ratio = float(np.dot(x, y) / denominator)
            spread = current_a.value - hedge_ratio * current_b.value
        hedge = context.feature(
            name="hedge_ratio",
            value=hedge_ratio,
            inputs=(a_history, b_history),
            formula="dot(price_B, price_A) / dot(price_B, price_B)",
            parameters={"lookback": self.lookback},
            window_start=a_history.timestamps[0] if hedge_ratio is not None else None,
            window_end=context.current_time if hedge_ratio is not None else None,
        )
        spread_ref = context.feature(
            name="spread",
            value=spread,
            inputs=(hedge, current_a, current_b),
            formula="price_A - hedge_ratio * price_B",
            window_start=context.current_time if spread is not None else None,
            window_end=context.current_time if spread is not None else None,
        )
        self._spreads.append(spread)
        self._spread_refs.append(spread_ref)

        valid = len(self._spreads) >= self.lookback and all(
            value is not None for value in self._spreads[-self.lookback :]
        )
        rolling_mean: float | None = None
        rolling_std: float | None = None
        zscore: float | None = None
        window_start = None
        rolling_inputs: tuple[FeatureRef, ...] = ()
        if valid:
            spread_values = np.asarray(self._spreads[-self.lookback :], dtype=np.float64)
            rolling_mean = float(np.mean(spread_values))
            rolling_std = float(np.std(spread_values, ddof=0))
            zscore = (
                0.0 if rolling_std == 0 else float((spread_values[-1] - rolling_mean) / rolling_std)
            )
            rolling_inputs = tuple(self._spread_refs[-self.lookback :])
            window_start = rolling_inputs[0].record.available_at
        mean_ref = context.feature(
            name="rolling_mean",
            value=rolling_mean,
            inputs=rolling_inputs,
            formula="mean(spread_window)",
            parameters={"lookback": self.lookback},
            window_start=window_start,
            window_end=context.current_time if valid else None,
        )
        std_ref = context.feature(
            name="rolling_std",
            value=rolling_std,
            inputs=rolling_inputs,
            formula="population_std(spread_window, ddof=0)",
            parameters={"lookback": self.lookback, "ddof": 0},
            window_start=window_start,
            window_end=context.current_time if valid else None,
        )
        zscore_ref = context.feature(
            name="zscore",
            value=zscore,
            inputs=(spread_ref, mean_ref, std_ref),
            formula="(spread - rolling_mean) / rolling_std",
            window_start=window_start,
            window_end=context.current_time if valid else None,
        )

        previous_target = self._target
        next_target = previous_target
        action: str
        reason: str
        conditions: tuple[DecisionCondition, ...]
        if zscore is None:
            action = "WARMUP"
            reason = f"Need two complete {self.lookback}-bar windows"
            conditions = (
                context.condition(
                    left="zscore",
                    left_value=None,
                    operator="is_available",
                    right=None,
                    right_value=None,
                    result=False,
                    description="The strategy is still in its warm-up period",
                ),
            )
        elif previous_target == 0:
            short_result = zscore > self.entry_z
            long_result = zscore < -self.entry_z
            conditions = (
                context.condition(
                    left="zscore",
                    left_value=zscore,
                    operator=">",
                    right="entry_z",
                    right_value=self.entry_z,
                    result=short_result,
                    description="Enter short spread when z-score exceeds the entry threshold",
                ),
                context.condition(
                    left="zscore",
                    left_value=zscore,
                    operator="<",
                    right="-entry_z",
                    right_value=-self.entry_z,
                    result=long_result,
                    description=(
                        "Enter long spread when z-score is below the negative entry threshold"
                    ),
                ),
            )
            if short_result:
                next_target = -1
                action = "SHORT_SPREAD"
                reason = f"z-score {zscore:.4f} > entry threshold {self.entry_z:.4f}"
            elif long_result:
                next_target = 1
                action = "LONG_SPREAD"
                reason = f"z-score {zscore:.4f} < -entry threshold {-self.entry_z:.4f}"
            else:
                action = "HOLD"
                reason = "No position transition condition was met"
        else:
            exit_value = abs(zscore)
            exit_result = exit_value < self.exit_z
            conditions = (
                context.condition(
                    left="abs(zscore)",
                    left_value=exit_value,
                    operator="<",
                    right="exit_z",
                    right_value=self.exit_z,
                    result=exit_result,
                    description=(
                        "Close the spread when absolute z-score is below the exit threshold"
                    ),
                ),
            )
            if exit_result:
                next_target = 0
                action = "CLOSE"
                reason = f"|z-score| {exit_value:.4f} < exit threshold {self.exit_z:.4f}"
            else:
                action = "HOLD"
                reason = "No position transition condition was met"

        transition = next_target != previous_target
        self._target = next_target
        beta = hedge_ratio or 0.0
        weights = {
            asset_a: float(next_target),
            asset_b: -float(next_target) * beta,
        }
        return context.target_weights(
            weights,
            gross_notional=self.gross_target,
            reason=reason,
            dependencies=(zscore_ref,) if zscore is not None else (),
            conditions=conditions,
            signal=action,
            previous_state=self._state(previous_target),
            next_state=self._state(next_target),
            transition=transition,
            target_state=next_target,
        )


@dataclass(slots=True)
class PairsFeatureCalculator:
    lookback: int

    def __post_init__(self) -> None:
        if self.lookback < 2:
            raise ValueError("lookback must be at least 2")

    def calculate(self, bars: tuple[MarketBar, ...]) -> tuple[FeaturePoint, ...]:
        spreads: list[float | None] = [None] * len(bars)
        hedge_ratios: list[float | None] = [None] * len(bars)
        features: list[FeaturePoint] = []
        for index, bar in enumerate(bars):
            if index >= self.lookback - 1:
                window = bars[index - self.lookback + 1 : index + 1]
                x = np.asarray([item.asset_b for item in window], dtype=np.float64)
                y = np.asarray([item.asset_a for item in window], dtype=np.float64)
                denominator = float(np.dot(x, x))
                if denominator == 0:
                    raise ValueError("Cannot regress against a zero-valued Asset B window")
                hedge_ratio = float(np.dot(x, y) / denominator)
                hedge_ratios[index] = hedge_ratio
                spreads[index] = bar.asset_a - hedge_ratio * bar.asset_b

            spread_window_start = index - self.lookback + 1
            valid_window = spread_window_start >= 0 and all(
                value is not None for value in spreads[spread_window_start : index + 1]
            )
            if valid_window:
                spread_values = np.asarray(
                    spreads[spread_window_start : index + 1], dtype=np.float64
                )
                rolling_mean = float(np.mean(spread_values))
                rolling_std = float(np.std(spread_values, ddof=0))
                zscore = (
                    0.0
                    if rolling_std == 0
                    else float((spread_values[-1] - rolling_mean) / rolling_std)
                )
                window_start = bars[spread_window_start].timestamp
            else:
                rolling_mean = None
                rolling_std = None
                zscore = None
                window_start = None

            features.append(
                FeaturePoint(
                    hedge_ratio=hedge_ratios[index],
                    spread=spreads[index],
                    rolling_mean=rolling_mean,
                    rolling_std=rolling_std,
                    zscore=zscore,
                    window_start=window_start,
                    window_end=bar.timestamp if valid_window else None,
                    available_at=bar.timestamp,
                )
            )
        return tuple(features)


@dataclass(slots=True)
class PairsSignalEvaluator:
    parameters: PairsTradingParameters

    def evaluate(
        self, bars: tuple[MarketBar, ...], features: tuple[FeaturePoint, ...]
    ) -> tuple[SignalDecision, ...]:
        if len(bars) != len(features):
            raise ValueError("bars and features must have equal length")
        decisions: list[SignalDecision] = []
        target: Literal[-1, 0, 1] = 0
        signal_count = 0
        for bar, feature in zip(bars, features, strict=True):
            zscore = feature.zscore
            next_target: Literal[-1, 0, 1] = target
            action: Literal["LONG_SPREAD", "SHORT_SPREAD", "CLOSE", "HOLD", "WARMUP"]
            reason: str
            conditions: tuple[DecisionCondition, ...]
            if zscore is None:
                action = "WARMUP"
                reason = f"Need two complete {self.parameters.lookback}-bar windows"
                conditions = (
                    DecisionCondition(
                        left_operand="zscore",
                        left_value=None,
                        operator="is_available",
                        right_operand=None,
                        right_value=None,
                        result=False,
                        description="The strategy is still in its warm-up period",
                    ),
                )
            elif target == 0:
                short_result = zscore > self.parameters.entry_z
                long_result = zscore < -self.parameters.entry_z
                conditions = (
                    DecisionCondition(
                        left_operand="zscore",
                        left_value=zscore,
                        operator=">",
                        right_operand="entry_z",
                        right_value=self.parameters.entry_z,
                        result=short_result,
                        description="Enter short spread when z-score exceeds the entry threshold",
                    ),
                    DecisionCondition(
                        left_operand="zscore",
                        left_value=zscore,
                        operator="<",
                        right_operand="-entry_z",
                        right_value=-self.parameters.entry_z,
                        result=long_result,
                        description=(
                            "Enter long spread when z-score is below the negative entry threshold"
                        ),
                    ),
                )
                if short_result:
                    next_target = -1
                    action = "SHORT_SPREAD"
                    reason = f"z-score {zscore:.4f} > entry threshold {self.parameters.entry_z:.4f}"
                elif long_result:
                    next_target = 1
                    action = "LONG_SPREAD"
                    reason = (
                        f"z-score {zscore:.4f} < -entry threshold {-self.parameters.entry_z:.4f}"
                    )
                else:
                    action = "HOLD"
                    reason = "No position transition condition was met"
            else:
                exit_value = abs(zscore)
                exit_result = exit_value < self.parameters.exit_z
                conditions = (
                    DecisionCondition(
                        left_operand="abs(zscore)",
                        left_value=exit_value,
                        operator="<",
                        right_operand="exit_z",
                        right_value=self.parameters.exit_z,
                        result=exit_result,
                        description=(
                            "Close the spread when absolute z-score is below the exit threshold"
                        ),
                    ),
                )
                if exit_result:
                    next_target = 0
                    action = "CLOSE"
                    reason = (
                        f"|z-score| {exit_value:.4f} < exit threshold {self.parameters.exit_z:.4f}"
                    )
                else:
                    action = "HOLD"
                    reason = "No position transition condition was met"
            signal_id = None
            if next_target != target:
                signal_count += 1
                signal_id = f"signal-{signal_count:04d}"
            decisions.append(
                SignalDecision(
                    signal_id=signal_id,
                    action=action,
                    target_position=next_target,
                    reason=reason,
                    decided_at=bar.timestamp,
                    previous_target=target,
                    conditions=conditions,
                )
            )
            target = next_target
        return tuple(decisions)


def calculate_features(bars: tuple[MarketBar, ...], lookback: int) -> tuple[FeaturePoint, ...]:
    return PairsFeatureCalculator(lookback).calculate(bars)


def evaluate_signals(
    bars: tuple[MarketBar, ...],
    features: tuple[FeaturePoint, ...],
    parameters: PairsTradingParameters,
) -> tuple[SignalDecision, ...]:
    return PairsSignalEvaluator(parameters).evaluate(bars, features)
