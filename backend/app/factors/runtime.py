from __future__ import annotations

import math
import statistics
from collections.abc import Mapping

from app.factor_sdk import FactorContext, FactorPoint, FactorResult, FactorSeries
from app.fundamentals import FundamentalObservation
from app.sdk.context import StrategyContext
from app.sdk.models import FeatureRef, MarketValueRef
from app.trace.models import DataDependency

from .catalog import parameter_values
from .registry import factor_registry


def _fundamental_inputs(
    context: StrategyContext,
    *,
    fundamental_dataset_id: str,
    factor_id: str,
    symbol: str,
    max_age_days: int,
) -> tuple[FundamentalObservation, ...]:
    definition = factor_registry.definition(factor_id)
    result: list[FundamentalObservation] = []
    annual_fields = {"net_income", "revenue", "operating_income", "free_cash_flow"}
    for field in definition.required_fundamental_fields:
        count = 2 if factor_id in {"revenue-growth", "earnings-growth"} else 1
        selected = context.fundamental_observations(
            dataset_id=fundamental_dataset_id,
            symbol=symbol,
            field=field,
            period_type="ANNUAL" if field in annual_fields else None,
            count=count,
        )
        if (
            len(selected) < count
            or (context.current_time.date() - selected[0].available_at.date()).days > max_age_days
        ):
            return ()
        result.extend(selected)
    return tuple(result)


def _fundamental_result(
    factor_id: str,
    inputs: tuple[FundamentalObservation, ...],
    close: float,
) -> float | None:
    selected: dict[str, list[FundamentalObservation]] = {}
    for item in inputs:
        selected.setdefault(item.field, []).append(item)
    try:
        latest = {field: values[0].value for field, values in selected.items()}
        market_cap = close * latest.get("shares_outstanding", 1.0)
        if factor_id == "earnings-yield":
            return latest["net_income"] / market_cap
        if factor_id == "book-to-price":
            return latest["equity"] / market_cap
        if factor_id == "free-cash-flow-yield":
            return latest["free_cash_flow"] / market_cap
        if factor_id == "roe":
            return latest["net_income"] / latest["equity"]
        if factor_id == "roa":
            return latest["net_income"] / latest["assets"]
        if factor_id == "operating-margin":
            return latest["operating_income"] / latest["revenue"]
        if factor_id == "revenue-growth":
            return selected["revenue"][0].value / selected["revenue"][1].value - 1
        if factor_id == "earnings-growth":
            return selected["net_income"][0].value / selected["net_income"][1].value - 1
        if factor_id == "debt-to-equity":
            return latest["debt"] / latest["equity"]
    except (KeyError, ZeroDivisionError):
        return None
    return None


def compute_runtime_factor(
    context: StrategyContext,
    *,
    factor_id: str,
    symbol: str,
    lookback: int,
    fundamental_dataset_id: str | None = None,
    max_age_days: int = 550,
    parameters: Mapping[str, int | float] | None = None,
) -> FeatureRef:
    definition = factor_registry.definition(factor_id)
    supplied = dict(parameters or {})
    if "lookback" in {item.key for item in definition.parameters}:
        supplied.setdefault("lookback", lookback)
    if "max_age_days" in {item.key for item in definition.parameters}:
        supplied.setdefault("max_age_days", max_age_days)
    configured = parameter_values(definition, supplied)
    if definition.origin == "CUSTOM":
        return _custom_runtime_factor(
            context,
            factor_id=factor_id,
            symbol=symbol,
            parameters=configured,
            fundamental_dataset_id=fundamental_dataset_id,
            stack=(),
        )
    if definition.data_source == "FUNDAMENTAL":
        fundamental_observations = (
            _fundamental_inputs(
                context,
                fundamental_dataset_id=fundamental_dataset_id,
                factor_id=factor_id,
                symbol=symbol,
                max_age_days=max_age_days,
            )
            if fundamental_dataset_id is not None
            else ()
        )
        dependencies = tuple(
            context.external_value(
                source=item.source,
                symbol=item.symbol,
                field=item.field,
                value=item.value,
                source_timestamp=item.report_date,
                available_at=item.available_at,
            )
            for item in fundamental_observations
        )
        close = context.current(symbol, "close")
        fundamental_value = (
            _fundamental_result(factor_id, fundamental_observations, close.value)
            if fundamental_observations
            else None
        )
        return context.feature(
            name=f"{factor_id}:{symbol}",
            value=fundamental_value,
            inputs=(*dependencies, close),
            formula=definition.formula,
            parameters={
                "max_age_days": max_age_days,
                "symbol": symbol,
                "fundamental_dataset_id": fundamental_dataset_id or "",
            },
            window_start=min((item.report_date for item in fundamental_observations), default=None),
            window_end=context.current_time,
        )
    closes = context.history(symbol=symbol, field="close", bars=lookback + 1)
    value: float | None = None
    market_inputs: list[object] = [closes]
    if len(closes) >= lookback + 1:
        if factor_id == "momentum":
            value = closes[-1] / closes[0] - 1
        elif factor_id == "reversal":
            value = -(closes[-1] / closes[0] - 1)
        elif factor_id == "volatility":
            returns = [
                current / previous - 1
                for previous, current in zip(closes, closes[1:], strict=False)
            ]
            value = statistics.pstdev(returns)
        elif factor_id == "moving-average-distance":
            value = closes[-1] / statistics.fmean(closes[1:]) - 1
        elif factor_id in {"volume-change", "liquidity-proxy"}:
            volumes = context.history(symbol=symbol, field="volume", bars=lookback + 1)
            market_inputs.append(volumes)
            if len(volumes) >= lookback + 1:
                if factor_id == "volume-change":
                    baseline = statistics.fmean(volumes[:-1])
                    value = volumes[-1] / baseline - 1 if baseline else 0.0
                else:
                    value = math.log(
                        max(
                            statistics.fmean(
                                close * volume
                                for close, volume in zip(closes[1:], volumes[1:], strict=True)
                            ),
                            1e-12,
                        )
                    )
        elif factor_id in {"price-range", "breakout"}:
            highs = context.history(symbol=symbol, field="high", bars=lookback + 1)
            market_inputs.append(highs)
            if factor_id == "breakout" and len(highs) >= lookback + 1:
                value = closes[-1] / max(highs[:-1]) - 1
            elif factor_id == "price-range":
                lows = context.history(symbol=symbol, field="low", bars=lookback + 1)
                market_inputs.append(lows)
                if len(highs) >= lookback + 1 and len(lows) >= lookback + 1:
                    value = (max(highs[1:]) - min(lows[1:])) / closes[-1]
    return context.feature(
        name=f"{factor_id}:{symbol}",
        value=value,
        inputs=market_inputs,  # type: ignore[arg-type]
        formula=definition.formula,
        parameters={"lookback": lookback, "symbol": symbol},
        window_start=closes.timestamps[0] if closes.timestamps else None,
        window_end=context.current_time,
    )


def _custom_runtime_factor(
    context: StrategyContext,
    *,
    factor_id: str,
    symbol: str,
    parameters: dict[str, int | float],
    fundamental_dataset_id: str | None,
    stack: tuple[str, ...],
) -> FeatureRef:
    if factor_id in stack:
        raise ValueError(f"Recursive factor lineage detected: {' → '.join((*stack, factor_id))}")
    definition = factor_registry.definition(factor_id)
    factor, _ = factor_registry.instantiate(factor_id, parameters)

    def dependency_for(dependency_id: str) -> DataDependency:
        return next(
            item for item in context.data_dependencies if item.dependency_id == dependency_id
        )

    def point(ref: MarketValueRef) -> FactorPoint:
        dependency = dependency_for(ref.dependency_id)
        return FactorPoint(
            value=ref.value,
            source_timestamp=dependency.source_timestamp,
            available_at=dependency.available_at,
            used_at=dependency.used_at,
            dependency=dependency,
            token=ref,
        )

    def current_reader(request_symbol: str, field: str) -> FactorPoint:
        if request_symbol != symbol:
            raise ValueError("A factor may only read the symbol currently being evaluated")
        if field not in definition.required_fields:
            raise ValueError(f"Market field '{field}' is not declared in required_fields")
        return point(context.current(symbol, field))

    def history_reader(request_symbol: str, field: str, bars: int) -> FactorSeries:
        if request_symbol != symbol:
            raise ValueError("A factor may only read the symbol currently being evaluated")
        if field not in definition.required_fields:
            raise ValueError(f"Market field '{field}' is not declared in required_fields")
        history = context.history(symbol=symbol, field=field, bars=bars)
        return FactorSeries(tuple(point(item) for item in history.points))

    def fundamental_reader(
        request_symbol: str,
        field: str,
        period_type: str | None,
        count: int,
        requested_max_age_days: int,
    ) -> FactorSeries:
        if request_symbol != symbol:
            raise ValueError("A factor may only read the symbol currently being evaluated")
        if field not in definition.required_fundamental_fields:
            raise ValueError(
                f"Fundamental field '{field}' is not declared in required_fundamental_fields"
            )
        if fundamental_dataset_id is None:
            return FactorSeries(())
        selected = context.fundamental_observations(
            dataset_id=fundamental_dataset_id,
            symbol=symbol,
            field=field,
            period_type=period_type,
            count=count,
        )
        if (
            len(selected) < count
            or (context.current_time.date() - selected[0].available_at.date()).days
            > requested_max_age_days
        ):
            return FactorSeries(())
        points: list[FactorPoint] = []
        for item in reversed(selected):
            ref = context.external_value(
                source=item.source,
                symbol=item.symbol,
                field=item.field,
                value=item.value,
                source_timestamp=item.report_date,
                available_at=item.available_at,
            )
            points.append(point(ref))
        return FactorSeries(tuple(points))

    def factor_reader(
        child_id: str,
        request_symbol: str,
        child_parameters: Mapping[str, int | float],
    ) -> FactorResult:
        if request_symbol != symbol:
            raise ValueError("A factor may only compose the symbol currently being evaluated")
        child_definition = factor_registry.definition(child_id)
        configured = parameter_values(child_definition, dict(child_parameters))
        feature = (
            _custom_runtime_factor(
                context,
                factor_id=child_id,
                symbol=symbol,
                parameters=configured,
                fundamental_dataset_id=fundamental_dataset_id,
                stack=(*stack, factor_id),
            )
            if child_definition.origin == "CUSTOM"
            else compute_runtime_factor(
                context,
                factor_id=child_id,
                symbol=symbol,
                lookback=int(configured.get("lookback", child_definition.lookback)),
                fundamental_dataset_id=fundamental_dataset_id,
                max_age_days=int(configured.get("max_age_days", 550)),
                parameters=configured,
            )
        )
        dependencies = tuple(
            item
            for item in context.data_dependencies
            if item.dependency_id in feature.record.data_dependencies
        )
        points = tuple(
            FactorPoint(
                value=float(item.value) if item.value is not None else 0.0,
                source_timestamp=item.source_timestamp,
                available_at=item.available_at,
                used_at=item.used_at,
                dependency=item,
            )
            for item in dependencies
        )
        return FactorResult(
            value=feature.value,
            formula=child_definition.formula,
            inputs=(FactorSeries(points),),
            parameters=configured,
            window_start=feature.record.window_start,
            window_end=context.current_time,
            available_at=feature.record.available_at,
            token=feature,
        )

    sdk_context = FactorContext(
        used_at=context.current_time,
        parameters=parameters,
        current_reader=current_reader,
        history_reader=history_reader,
        fundamental_reader=fundamental_reader,
        factor_reader=factor_reader,
    )
    result = factor.compute(sdk_context, symbol)
    if not isinstance(result, FactorResult):
        raise TypeError("VQDFactor.compute() must return context.result(...)")
    return context.feature(
        name=f"{factor_id}:{symbol}",
        value=result.value,
        inputs=result.tokens,
        formula=result.formula,
        parameters={**parameters, "symbol": symbol},
        window_start=result.window_start,
        window_end=context.current_time,
    )


def compute_runtime_mixed_factors(
    context: StrategyContext,
    *,
    components: tuple[Mapping[str, object], ...],
    research_id: str,
    fundamental_dataset_id: str | None,
) -> dict[str, FeatureRef]:
    component_features: list[tuple[float, dict[str, FeatureRef]]] = []
    for component in components:
        factor_id = str(component["factor_id"])
        raw_weight = component["weight"]
        if not isinstance(raw_weight, (int, float)):
            raise ValueError("Mixed factor weight must be numeric")
        parameters = component.get("parameters", {})
        lookback = (
            int(parameters.get("lookback", factor_registry.definition(factor_id).lookback))
            if isinstance(parameters, Mapping)
            else factor_registry.definition(factor_id).lookback
        )
        features = {
            symbol: compute_runtime_factor(
                context,
                factor_id=factor_id,
                symbol=symbol,
                lookback=lookback,
                fundamental_dataset_id=fundamental_dataset_id,
                max_age_days=(
                    int(parameters.get("max_age_days", 550))
                    if isinstance(parameters, Mapping)
                    else 550
                ),
            )
            for symbol in context.symbols
        }
        component_features.append((float(raw_weight), features))
    normalized: list[tuple[float, dict[str, float]]] = []
    for weight, features in component_features:
        available = {
            symbol: float(feature.value)
            for symbol, feature in features.items()
            if feature.value is not None
        }
        if len(available) < 2:
            normalized.append((weight, {}))
            continue
        mean = statistics.fmean(available.values())
        deviation = statistics.pstdev(available.values())
        normalized.append(
            (
                weight,
                {
                    symbol: 0.0 if deviation < 1e-12 else (value - mean) / deviation
                    for symbol, value in available.items()
                },
            )
        )
    result: dict[str, FeatureRef] = {}
    for symbol in context.symbols:
        inputs = tuple(features[symbol] for _, features in component_features)
        value = (
            None
            if any(symbol not in values for _, values in normalized)
            else sum(weight * values[symbol] for weight, values in normalized)
        )
        result[symbol] = context.feature(
            name=f"mixed:{symbol}",
            value=value,
            inputs=inputs,
            formula="sum(weight_i * cross_sectional_zscore(component_i(t)))",
            parameters={"research_id": research_id, "symbol": symbol},
            window_end=context.current_time,
        )
    return result


def runtime_volatility(context: StrategyContext, *, symbol: str, lookback: int) -> FeatureRef:
    closes = context.history(symbol=symbol, field="close", bars=lookback + 1)
    value = None
    if len(closes) >= lookback + 1:
        returns = [
            current / previous - 1 for previous, current in zip(closes, closes[1:], strict=False)
        ]
        value = statistics.pstdev(returns)
    return context.feature(
        name=f"volatility-filter:{symbol}",
        value=value,
        inputs=(closes,),
        formula="std(daily_return[t-lookback+1:t])",
        parameters={"lookback": lookback, "symbol": symbol},
        window_start=closes.timestamps[0] if closes.timestamps else None,
        window_end=context.current_time,
    )
