from __future__ import annotations

import math
import statistics
from collections.abc import Mapping

from app.factors.runtime import compute_runtime_factor, compute_runtime_mixed_factors
from app.sdk.context import StrategyContext
from app.sdk.models import FeatureRef


def _ranked(values: Mapping[str, float]) -> dict[str, int]:
    return {
        symbol: rank
        for rank, symbol in enumerate(
            sorted(values, key=lambda item: (-values[item], item)), start=1
        )
    }


def compute_runtime_portfolio_scores(
    context: StrategyContext,
    *,
    factor_specs: tuple[Mapping[str, object], ...],
    combination: str,
    require_all_factors: bool = True,
) -> dict[str, FeatureRef]:
    component_features: list[tuple[float, str, dict[str, FeatureRef]]] = []
    for spec in factor_specs:
        factor_id = str(spec["factor_id"])
        direction = str(spec.get("direction", "HIGH"))
        raw_weight = spec.get("weight", 1.0)
        if not isinstance(raw_weight, int | float):
            raise ValueError("Portfolio factor weight must be numeric")
        parameters = spec.get("parameters", {})
        if not isinstance(parameters, Mapping):
            parameters = {}
        fundamental_dataset_id = spec.get("fundamental_dataset_id")
        if factor_id == "mixed":
            components = spec.get("components", ())
            if not isinstance(components, tuple):
                components = tuple(components) if isinstance(components, list) else ()
            features = compute_runtime_mixed_factors(
                context,
                components=components,
                research_id=str(spec["research_id"]),
                fundamental_dataset_id=(
                    str(fundamental_dataset_id) if fundamental_dataset_id is not None else None
                ),
            )
        else:
            lookback = int(parameters.get("lookback", spec.get("lookback", 20)))
            features = {
                symbol: compute_runtime_factor(
                    context,
                    factor_id=factor_id,
                    symbol=symbol,
                    lookback=lookback,
                    fundamental_dataset_id=(
                        str(fundamental_dataset_id) if fundamental_dataset_id is not None else None
                    ),
                    max_age_days=int(parameters.get("max_age_days", 550)),
                    parameters=parameters,
                )
                for symbol in context.symbols
            }
        signed: dict[str, FeatureRef] = features
        component_features.append((float(raw_weight), direction, signed))

    transformed: list[tuple[float, dict[str, float]]] = []
    for raw_weight, direction, features in component_features:
        available = {
            symbol: float(feature.value) * (1.0 if direction == "HIGH" else -1.0)
            for symbol, feature in features.items()
            if feature.value is not None
        }
        values = list(available.values())
        minimum = min(values) if values else 0.0
        maximum = max(values) if values else 0.0
        mean = statistics.fmean(values) if values else 0.0
        deviation = statistics.pstdev(values) if len(values) > 1 else 0.0
        ranks = _ranked(available)
        normalized: dict[str, float] = {}
        for symbol, value in available.items():
            if combination == "RANK_AVERAGE":
                normalized[symbol] = (
                    1.0 if len(available) == 1 else 1 - (ranks[symbol] - 1) / (len(available) - 1)
                )
            elif combination == "Z_SCORE_COMPOSITE":
                normalized[symbol] = 0.0 if deviation < 1e-12 else (value - mean) / deviation
            else:
                normalized[symbol] = (
                    0.5
                    if math.isclose(maximum, minimum)
                    else (value - minimum) / (maximum - minimum)
                )
        weight = raw_weight if combination == "USER_DEFINED_WEIGHT" else 1 / len(component_features)
        transformed.append((weight, normalized))

    result: dict[str, FeatureRef] = {}
    for symbol in context.symbols:
        inputs = tuple(features[symbol] for _, _, features in component_features)
        available_components = [
            (weight, values[symbol]) for weight, values in transformed if symbol in values
        ]
        all_available = len(available_components) == len(transformed)
        available_weight = sum(weight for weight, _ in available_components)
        if (require_all_factors and not all_available) or available_weight <= 0:
            composite_value: float | None = None
        else:
            composite_value = (
                sum(weight * normalized for weight, normalized in available_components)
                / available_weight
            )
        result[symbol] = context.feature(
            name=f"portfolio-composite:{symbol}",
            value=composite_value,
            inputs=inputs,
            formula=f"backend_portfolio_composite[{combination}]",
            parameters={
                "combination": combination,
                "symbol": symbol,
                "require_all_factors": require_all_factors,
                "available_factors": len(available_components),
                "total_factors": len(transformed),
            },
            window_end=context.current_time,
        )
    return result


def runtime_liquidity(context: StrategyContext, *, symbol: str, lookback: int = 20) -> FeatureRef:
    closes = context.history(symbol=symbol, field="close", bars=lookback)
    volumes = context.history(symbol=symbol, field="volume", bars=lookback)
    value = None
    if closes and len(closes) == len(volumes):
        value = statistics.fmean(
            close * volume for close, volume in zip(closes, volumes, strict=True)
        )
    return context.feature(
        name=f"liquidity-filter:{symbol}",
        value=value,
        inputs=(closes, volumes),
        formula="mean(close * volume, 20 bars)",
        parameters={"lookback": lookback, "symbol": symbol},
        window_start=closes.timestamps[0] if closes.timestamps else None,
        window_end=context.current_time,
    )
