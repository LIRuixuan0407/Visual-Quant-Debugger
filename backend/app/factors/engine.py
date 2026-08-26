from __future__ import annotations

import math
import secrets
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from app.datasets import DatasetRegistry
from app.factor_sdk.context import FactorContext
from app.factor_sdk.models import FactorPoint, FactorResult, FactorSeries
from app.fundamentals import (
    FundamentalDataset,
    FundamentalFieldSnapshot,
    FundamentalObservation,
    FundamentalRepository,
)
from app.models import MarketFrame
from app.trace.models import DataDependency
from app.universes import HistoricalUniverse, UniverseRepository

from .catalog import parameter_values
from .models import (
    CreateFactorResearch,
    FactorComponent,
    FactorDefinition,
    FactorInspection,
    FactorObservation,
    FactorResearchRecord,
    FactorTimelinePoint,
    HistoricalMarketView,
    HistoricalSecurityRow,
    HistoricalTrendPoint,
    HorizonEvaluation,
    PeriodEvaluation,
    ResearchPeriod,
    ResearchStage,
)
from .registry import FactorRegistry, factor_registry


@dataclass(frozen=True, slots=True)
class _ComputedObservation:
    symbol: str
    index: int
    timestamp: datetime
    value: float
    window_start: datetime
    available_at: datetime
    dependencies: tuple[DataDependency, ...]
    fundamental_inputs: tuple[FundamentalFieldSnapshot, ...]
    universe_size: int
    future_returns: dict[int, float | None]
    future_return_timestamps: dict[int, datetime | None]


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(set(left)) < 2 or len(set(right)) < 2:
        return None
    value = float(np.corrcoef(np.asarray(left), np.asarray(right))[0, 1])
    return value if math.isfinite(value) else None


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[cursor]]:
            end += 1
        rank = (cursor + end - 1) / 2 + 1
        for index in ordered[cursor:end]:
            result[index] = rank
        cursor = end
    return result


def _safe_mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _stability(values: list[float]) -> float | None:
    if not values:
        return None
    deviation = statistics.pstdev(values)
    return None if deviation < 1e-12 else statistics.fmean(values) / deviation


def _is_monotonic(values: tuple[float | None, ...]) -> bool:
    populated = [value for value in values if value is not None]
    if len(populated) < 3:
        return False
    return all(left <= right for left, right in zip(populated, populated[1:], strict=False))


def _required_future(item: _ComputedObservation, horizon: int) -> float:
    value = item.future_returns[horizon]
    if value is None:
        raise ValueError("A missing forward return entered factor evaluation")
    return value


def _factor_value(
    factor_id: str, frames: tuple[MarketFrame, ...], index: int, symbol: str, lookback: int
) -> tuple[float, int] | None:
    if index < lookback:
        return None
    start = index - lookback
    window = frames[start : index + 1]
    try:
        closes = [frame.value(symbol, "close") for frame in window]
        if factor_id == "momentum":
            return closes[-1] / closes[0] - 1, start
        if factor_id == "reversal":
            return -(closes[-1] / closes[0] - 1), start
        if factor_id == "volatility":
            returns = [
                current_close / previous_close - 1
                for previous_close, current_close in zip(closes, closes[1:], strict=False)
            ]
            return statistics.pstdev(returns), start
        if factor_id == "volume-change":
            volumes = [frame.value(symbol, "volume") for frame in window]
            baseline = statistics.fmean(volumes[:-1])
            return (volumes[-1] / baseline - 1 if baseline else 0.0), start
        if factor_id == "liquidity-proxy":
            dollar_volume = [
                frame.value(symbol, "close") * frame.value(symbol, "volume") for frame in window[1:]
            ]
            average = statistics.fmean(dollar_volume)
            return math.log(max(average, 1e-12)), start + 1
        if factor_id == "price-range":
            highs = [frame.value(symbol, "high") for frame in window[1:]]
            lows = [frame.value(symbol, "low") for frame in window[1:]]
            return (max(highs) - min(lows)) / closes[-1], start + 1
        if factor_id == "moving-average-distance":
            average = statistics.fmean(closes[1:])
            return closes[-1] / average - 1, start + 1
        if factor_id == "breakout":
            prior_high = max(frame.value(symbol, "high") for frame in window[:-1])
            return closes[-1] / prior_high - 1, start
    except (KeyError, ValueError, statistics.StatisticsError, ZeroDivisionError):
        return None
    raise KeyError(f"Factor '{factor_id}' was not found")


def _dependencies(
    *,
    frames: tuple[MarketFrame, ...],
    symbol: str,
    start_index: int,
    end_index: int,
    fields: tuple[str, ...],
) -> tuple[DataDependency, ...]:
    used_at = frames[end_index].knowledge_time
    result: list[DataDependency] = []
    for index in range(start_index, end_index + 1):
        frame = frames[index]
        for field in fields:
            if field not in frame.values[symbol]:
                continue
            result.append(
                DataDependency(
                    dependency_id=f"factor-dep-{symbol}-{index}-{field}",
                    source="market_data",
                    field=field,
                    symbol=symbol,
                    value=frame.value(symbol, field),
                    source_timestamp=frame.timestamp,
                    available_at=frame.knowledge_time,
                    used_at=used_at,
                )
            )
    return tuple(result)


class FactorResearchEngine:
    def __init__(
        self,
        datasets: DatasetRegistry,
        fundamentals: FundamentalRepository | None = None,
        universes: UniverseRepository | None = None,
        factors: FactorRegistry | None = None,
    ) -> None:
        self.datasets = datasets
        self.fundamentals = fundamentals or FundamentalRepository(datasets.workspace_root)
        self.universes = universes or UniverseRepository(datasets.workspace_root)
        self.factors = factors or factor_registry

    def _inputs(
        self, request: CreateFactorResearch
    ) -> tuple[
        tuple[MarketFrame, ...],
        tuple[str, ...],
        dict[str, int | float],
        HistoricalUniverse,
        FundamentalDataset | None,
    ]:
        dataset = self.datasets.get(request.dataset_id)
        if dataset is None:
            raise KeyError(f"Dataset '{request.dataset_id}' was not found")
        if dataset.source_type != "PROVIDER":
            raise ValueError("Factor research requires a real provider-backed dataset")
        definition = self.factors.definition(request.factor_id)
        component_definitions = tuple(
            self.factors.definition(item.factor_id) for item in request.components
        )
        required_market = set(definition.required_fields)
        for item in component_definitions:
            required_market.update(item.required_fields)
        missing = sorted(required_market - set(dataset.fields))
        if missing:
            raise ValueError(f"Dataset is missing factor fields: {', '.join(missing)}")
        universe_record = (
            self.universes.get(request.universe_id)
            if request.universe_id is not None
            else self.universes.static_for_dataset(dataset)
        )
        if universe_record is None:
            raise KeyError(f"Universe '{request.universe_id}' was not found")
        if universe_record.dataset_id not in {None, dataset.dataset_id}:
            raise ValueError("The selected universe does not belong to this market dataset")
        universe = tuple(dict.fromkeys(item.upper() for item in request.universe))
        universe = universe or universe_record.symbols_at(dataset.end_time)
        unknown = sorted(set(universe) - set(dataset.symbols))
        if unknown:
            raise ValueError(f"Universe symbols are not in the dataset: {', '.join(unknown)}")
        if len(universe) < 5:
            raise ValueError("Cross-sectional factor research requires at least five symbols")
        needs_fundamentals = definition.data_source in {"FUNDAMENTAL", "MIXED"} or any(
            item.data_source in {"FUNDAMENTAL", "MIXED"} for item in component_definitions
        )
        fundamental_dataset = (
            self.fundamentals.get(request.fundamental_dataset_id)
            if request.fundamental_dataset_id is not None
            else None
        )
        if needs_fundamentals and fundamental_dataset is None:
            raise ValueError("This factor requires a saved point-in-time fundamental dataset")
        if fundamental_dataset is not None:
            missing_symbols = sorted(set(universe) - set(fundamental_dataset.symbols))
            if missing_symbols:
                raise ValueError(
                    "Fundamental data is missing universe symbols: " + ", ".join(missing_symbols)
                )
        return (
            self.datasets.load_frames(request.dataset_id, universe),
            universe,
            parameter_values(definition, request.parameters),
            universe_record,
            fundamental_dataset,
        )

    @staticmethod
    def _future_returns(
        frames: tuple[MarketFrame, ...], index: int, symbol: str
    ) -> dict[int, float | None]:
        return {
            horizon: (
                None
                if index + horizon >= len(frames)
                else frames[index + horizon].value(symbol, "close")
                / frames[index].value(symbol, "close")
                - 1
            )
            for horizon in (1, 5, 20)
        }

    @staticmethod
    def _future_return_timestamps(
        frames: tuple[MarketFrame, ...], index: int
    ) -> dict[int, datetime | None]:
        return {
            horizon: (None if index + horizon >= len(frames) else frames[index + horizon].timestamp)
            for horizon in (1, 5, 20)
        }

    @staticmethod
    def _fundamental_dependency(item: FundamentalObservation, used_at: datetime) -> DataDependency:
        return DataDependency(
            dependency_id=f"fundamental-dep-{item.observation_id}-{used_at.date().isoformat()}",
            source=item.source,
            field=item.field,
            symbol=item.symbol,
            value=item.value,
            source_timestamp=item.report_date,
            available_at=item.available_at,
            used_at=used_at,
        )

    @staticmethod
    def _fundamental_snapshot_row(
        item: FundamentalObservation, used_at: datetime
    ) -> FundamentalFieldSnapshot:
        age_days = max(0, (used_at.date() - item.available_at.date()).days)
        return FundamentalFieldSnapshot(
            field=item.field,
            status="RESTATED" if item.is_restatement else "AVAILABLE",
            value=item.value,
            unit=item.unit,
            fiscal_period=item.fiscal_period,
            report_date=item.report_date,
            filed_at=item.filed_at,
            available_at=item.available_at,
            used_at=used_at,
            age_days=age_days,
            form=item.form,
            accession=item.accession,
            is_restatement=item.is_restatement,
        )

    def _fundamental_value(
        self,
        definition: FactorDefinition,
        dataset: FundamentalDataset,
        *,
        symbol: str,
        frame: MarketFrame,
        max_age_days: int,
    ) -> tuple[float, tuple[FundamentalObservation, ...]] | None:
        def take(
            field: str, *, annual: bool = False, count: int = 1
        ) -> tuple[FundamentalObservation, ...]:
            selected = self.fundamentals.latest_available(
                dataset,
                symbol=symbol,
                field=field,
                used_at=frame.knowledge_time,
                period_type="ANNUAL" if annual else None,
                count=count,
            )
            # Freshness describes the newest report used at this decision time.  A
            # growth factor still needs the previous annual comparison period, which
            # is expected to be roughly one year older and must not be rejected as
            # stale solely for being the comparison base.
            if (
                len(selected) < count
                or (frame.knowledge_time.date() - selected[0].available_at.date()).days
                > max_age_days
            ):
                return ()
            return selected

        factor_id = definition.factor_id
        annual_fields = {"net_income", "revenue", "operating_income", "free_cash_flow"}
        selected: dict[str, tuple[FundamentalObservation, ...]] = {}
        for field in definition.required_fundamental_fields:
            count = 2 if factor_id in {"revenue-growth", "earnings-growth"} else 1
            values = take(field, annual=field in annual_fields, count=count)
            if not values:
                return None
            selected[field] = values
        observations = tuple(item for values in selected.values() for item in values)
        try:
            latest = {field: values[0].value for field, values in selected.items()}
            market_cap = frame.value(symbol, "close") * latest.get("shares_outstanding", 1.0)
            if factor_id == "earnings-yield":
                value = latest["net_income"] / market_cap
            elif factor_id == "book-to-price":
                value = latest["equity"] / market_cap
            elif factor_id == "free-cash-flow-yield":
                value = latest["free_cash_flow"] / market_cap
            elif factor_id == "roe":
                value = latest["net_income"] / latest["equity"]
            elif factor_id == "roa":
                value = latest["net_income"] / latest["assets"]
            elif factor_id == "operating-margin":
                value = latest["operating_income"] / latest["revenue"]
            elif factor_id == "revenue-growth":
                current, previous = selected["revenue"]
                value = current.value / previous.value - 1
            elif factor_id == "earnings-growth":
                current, previous = selected["net_income"]
                value = current.value / previous.value - 1
            elif factor_id == "debt-to-equity":
                value = latest["debt"] / latest["equity"]
            else:
                raise KeyError(f"Fundamental factor '{factor_id}' was not found")
        except ZeroDivisionError:
            return None
        return (value, observations) if math.isfinite(value) else None

    def _compute_single(
        self,
        *,
        frames: tuple[MarketFrame, ...],
        universe: tuple[str, ...],
        universe_record: HistoricalUniverse,
        definition: FactorDefinition,
        parameters: dict[str, int | float],
        fundamental_dataset: FundamentalDataset | None,
    ) -> tuple[_ComputedObservation, ...]:
        observations: list[_ComputedObservation] = []
        lookback = int(parameters.get("lookback", definition.lookback))
        max_age_days = int(parameters.get("max_age_days", 550))
        for index, frame in enumerate(frames):
            active_universe = tuple(
                symbol
                for symbol in universe
                if symbol in universe_record.symbols_at(frame.knowledge_time)
            )
            for symbol in active_universe:
                fundamental_inputs: tuple[FundamentalFieldSnapshot, ...] = ()
                if definition.origin == "CUSTOM":
                    result = self._custom_result(
                        frames=frames,
                        index=index,
                        symbol=symbol,
                        definition=definition,
                        parameters=parameters,
                        fundamental_dataset=fundamental_dataset,
                        stack=(),
                    )
                    if result.value is None:
                        continue
                    value = result.value
                    dependencies = result.dependencies
                    if not dependencies:
                        raise ValueError(
                            f"Custom factor '{definition.factor_id}' produced an untraceable value"
                        )
                    fundamental_inputs = result.fundamental_inputs
                    window_start = result.window_start or frame.timestamp
                elif definition.data_source == "MARKET":
                    computed = _factor_value(definition.factor_id, frames, index, symbol, lookback)
                    if computed is None:
                        continue
                    value, start_index = computed
                    dependencies = _dependencies(
                        frames=frames,
                        symbol=symbol,
                        start_index=start_index,
                        end_index=index,
                        fields=definition.required_fields,
                    )
                    window_start = frames[start_index].timestamp
                else:
                    if fundamental_dataset is None:
                        continue
                    computed_fundamental = self._fundamental_value(
                        definition,
                        fundamental_dataset,
                        symbol=symbol,
                        frame=frame,
                        max_age_days=max_age_days,
                    )
                    if computed_fundamental is None:
                        continue
                    value, inputs = computed_fundamental
                    fundamental_inputs = tuple(
                        self._fundamental_snapshot_row(item, frame.knowledge_time)
                        for item in inputs
                    )
                    dependencies = tuple(
                        self._fundamental_dependency(item, frame.knowledge_time) for item in inputs
                    )
                    if "close" in definition.required_fields:
                        dependencies = (
                            *dependencies,
                            *_dependencies(
                                frames=frames,
                                symbol=symbol,
                                start_index=index,
                                end_index=index,
                                fields=("close",),
                            ),
                        )
                    window_start = min(item.report_date for item in inputs)
                observations.append(
                    _ComputedObservation(
                        symbol=symbol,
                        index=index,
                        timestamp=frame.timestamp,
                        value=value,
                        window_start=window_start,
                        available_at=max(item.available_at for item in dependencies),
                        dependencies=dependencies,
                        fundamental_inputs=fundamental_inputs,
                        universe_size=len(active_universe),
                        future_returns=self._future_returns(frames, index, symbol),
                        future_return_timestamps=self._future_return_timestamps(frames, index),
                    )
                )
        return tuple(observations)

    def _custom_result(
        self,
        *,
        frames: tuple[MarketFrame, ...],
        index: int,
        symbol: str,
        definition: FactorDefinition,
        parameters: dict[str, int | float],
        fundamental_dataset: FundamentalDataset | None,
        stack: tuple[str, ...],
    ) -> FactorResult:
        if definition.factor_id in stack:
            lineage = " → ".join((*stack, definition.factor_id))
            raise ValueError(f"Recursive factor lineage detected: {lineage}")
        factor, _ = self.factors.instantiate(definition.factor_id, parameters)
        frame = frames[index]
        used_at = frame.knowledge_time

        def point_for(frame_index: int, field: str) -> FactorPoint:
            selected = frames[frame_index]
            dependency = _dependencies(
                frames=frames,
                symbol=symbol,
                start_index=frame_index,
                end_index=index,
                fields=(field,),
            )[0]
            return FactorPoint(
                value=selected.value(symbol, field),
                source_timestamp=selected.timestamp,
                available_at=selected.knowledge_time,
                used_at=used_at,
                dependency=dependency,
            )

        def current_reader(request_symbol: str, field: str) -> FactorPoint:
            if request_symbol != symbol:
                raise ValueError("A factor may only read the symbol currently being evaluated")
            if field not in definition.required_fields:
                raise ValueError(f"Market field '{field}' is not declared in required_fields")
            return point_for(index, field)

        def history_reader(request_symbol: str, field: str, bars: int) -> FactorSeries:
            if request_symbol != symbol:
                raise ValueError("A factor may only read the symbol currently being evaluated")
            if field not in definition.required_fields:
                raise ValueError(f"Market field '{field}' is not declared in required_fields")
            start = max(0, index - bars + 1)
            return FactorSeries(
                tuple(
                    point_for(item_index, field)
                    for item_index in range(start, index + 1)
                    if symbol in frames[item_index].values
                    and field in frames[item_index].values[symbol]
                )
            )

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
            if fundamental_dataset is None:
                return FactorSeries(())
            selected = self.fundamentals.latest_available(
                fundamental_dataset,
                symbol=symbol,
                field=field,
                used_at=used_at,
                period_type=period_type,
                count=count,
            )
            if (
                len(selected) < count
                or (used_at.date() - selected[0].available_at.date()).days > requested_max_age_days
            ):
                return FactorSeries(())
            return FactorSeries(
                tuple(
                    FactorPoint(
                        value=item.value,
                        source_timestamp=item.report_date,
                        available_at=item.available_at,
                        used_at=used_at,
                        dependency=self._fundamental_dependency(item, used_at),
                        fundamental_input=self._fundamental_snapshot_row(item, used_at),
                    )
                    for item in reversed(selected)
                )
            )

        def factor_reader(
            child_id: str,
            request_symbol: str,
            supplied: Mapping[str, int | float],
        ) -> FactorResult:
            if request_symbol != symbol:
                raise ValueError("A factor may only compose the symbol currently being evaluated")
            child_definition = self.factors.definition(child_id)
            child_parameters = parameter_values(
                child_definition,
                dict(supplied),
            )
            if child_definition.origin == "CUSTOM":
                return self._custom_result(
                    frames=frames,
                    index=index,
                    symbol=symbol,
                    definition=child_definition,
                    parameters=child_parameters,
                    fundamental_dataset=fundamental_dataset,
                    stack=(*stack, definition.factor_id),
                )
            return self._built_in_result(
                frames=frames,
                index=index,
                symbol=symbol,
                definition=child_definition,
                parameters=child_parameters,
                fundamental_dataset=fundamental_dataset,
            )

        context = FactorContext(
            used_at=used_at,
            parameters=parameters,
            current_reader=current_reader,
            history_reader=history_reader,
            fundamental_reader=fundamental_reader,
            factor_reader=factor_reader,
        )
        result = factor.compute(context, symbol)
        if not isinstance(result, FactorResult):
            raise TypeError("VQDFactor.compute() must return context.result(...)")
        return result

    def _built_in_result(
        self,
        *,
        frames: tuple[MarketFrame, ...],
        index: int,
        symbol: str,
        definition: FactorDefinition,
        parameters: dict[str, int | float],
        fundamental_dataset: FundamentalDataset | None,
    ) -> FactorResult:
        used_at = frames[index].knowledge_time
        lookback = int(parameters.get("lookback", definition.lookback))
        dependencies: tuple[DataDependency, ...] = ()
        fundamental_inputs: tuple[FundamentalFieldSnapshot, ...] = ()
        value: float | None = None
        if definition.data_source == "MARKET":
            computed = _factor_value(definition.factor_id, frames, index, symbol, lookback)
            if computed is not None:
                value, start_index = computed
                dependencies = _dependencies(
                    frames=frames,
                    symbol=symbol,
                    start_index=start_index,
                    end_index=index,
                    fields=definition.required_fields,
                )
        elif fundamental_dataset is not None:
            computed_fundamental = self._fundamental_value(
                definition,
                fundamental_dataset,
                symbol=symbol,
                frame=frames[index],
                max_age_days=int(parameters.get("max_age_days", 550)),
            )
            if computed_fundamental is not None:
                value, inputs = computed_fundamental
                fundamental_inputs = tuple(
                    self._fundamental_snapshot_row(item, used_at) for item in inputs
                )
                dependencies = tuple(self._fundamental_dependency(item, used_at) for item in inputs)
                if "close" in definition.required_fields:
                    dependencies = (
                        *dependencies,
                        *_dependencies(
                            frames=frames,
                            symbol=symbol,
                            start_index=index,
                            end_index=index,
                            fields=("close",),
                        ),
                    )
        points = tuple(
            FactorPoint(
                value=float(item.value) if item.value is not None else 0.0,
                source_timestamp=item.source_timestamp,
                available_at=item.available_at,
                used_at=item.used_at,
                dependency=item,
                fundamental_input=next(
                    (
                        snapshot
                        for snapshot in fundamental_inputs
                        if snapshot.field == item.field
                        and snapshot.available_at == item.available_at
                    ),
                    None,
                ),
            )
            for item in dependencies
        )
        return FactorResult(
            value=value,
            formula=definition.formula,
            inputs=(FactorSeries(points),),
            parameters=parameters,
            window_start=min((item.source_timestamp for item in dependencies), default=None),
            window_end=used_at,
            available_at=max((item.available_at for item in dependencies), default=used_at),
        )

    def _compute(
        self,
        *,
        frames: tuple[MarketFrame, ...],
        universe: tuple[str, ...],
        universe_record: HistoricalUniverse,
        definition: FactorDefinition,
        parameters: dict[str, int | float],
        fundamental_dataset: FundamentalDataset | None,
        components: tuple[FactorComponent, ...],
    ) -> tuple[_ComputedObservation, ...]:
        if definition.factor_id != "mixed":
            return self._compute_single(
                frames=frames,
                universe=universe,
                universe_record=universe_record,
                definition=definition,
                parameters=parameters,
                fundamental_dataset=fundamental_dataset,
            )
        component_values: list[
            tuple[FactorComponent, dict[tuple[int, str], _ComputedObservation]]
        ] = []
        for component in components:
            component_definition = self.factors.definition(component.factor_id)
            computed = self._compute_single(
                frames=frames,
                universe=universe,
                universe_record=universe_record,
                definition=component_definition,
                parameters=parameter_values(component_definition, component.parameters),
                fundamental_dataset=fundamental_dataset,
            )
            component_values.append(
                (component, {(item.index, item.symbol): item for item in computed})
            )
        result: list[_ComputedObservation] = []
        for index, frame in enumerate(frames):
            active = tuple(
                symbol
                for symbol in universe
                if symbol in universe_record.symbols_at(frame.knowledge_time)
            )
            normalized: list[tuple[FactorComponent, dict[str, float]]] = []
            for component, values in component_values:
                available = {
                    symbol: values[(index, symbol)].value
                    for symbol in active
                    if (index, symbol) in values
                }
                if len(available) < 2:
                    normalized.append((component, {}))
                    continue
                mean = statistics.fmean(available.values())
                deviation = statistics.pstdev(available.values())
                normalized.append(
                    (
                        component,
                        {
                            symbol: 0.0 if deviation < 1e-12 else (value - mean) / deviation
                            for symbol, value in available.items()
                        },
                    )
                )
            for symbol in active:
                if any(symbol not in values for _, values in normalized):
                    continue
                parts = [values[(index, symbol)] for _, values in component_values]
                value = sum(component.weight * values[symbol] for component, values in normalized)
                dependency_by_id = {
                    dependency.dependency_id: dependency
                    for part in parts
                    for dependency in part.dependencies
                }
                dependencies = tuple(dependency_by_id.values())
                fundamental_inputs = tuple(
                    item for part in parts for item in part.fundamental_inputs
                )
                result.append(
                    _ComputedObservation(
                        symbol=symbol,
                        index=index,
                        timestamp=frame.timestamp,
                        value=value,
                        window_start=min(item.window_start for item in parts),
                        available_at=max(item.available_at for item in parts),
                        dependencies=dependencies,
                        fundamental_inputs=fundamental_inputs,
                        universe_size=len(active),
                        future_returns=self._future_returns(frames, index, symbol),
                        future_return_timestamps=self._future_return_timestamps(frames, index),
                    )
                )
        return tuple(result)

    @staticmethod
    def _evaluation(
        *,
        stage: ResearchStage,
        period: ResearchPeriod,
        observations: tuple[_ComputedObservation, ...],
        universe_size: int,
    ) -> PeriodEvaluation:
        period_items = tuple(
            item for item in observations if period.start <= item.timestamp <= period.end
        )
        horizons: list[HorizonEvaluation] = []
        for horizon in (1, 5, 20):
            by_time: dict[datetime, list[_ComputedObservation]] = {}
            for item in period_items:
                endpoint = item.future_return_timestamps[horizon]
                if (
                    item.future_returns[horizon] is not None
                    and endpoint is not None
                    and period.start <= endpoint <= period.end
                ):
                    by_time.setdefault(item.timestamp, []).append(item)
            daily_ic: list[float] = []
            daily_rank_ic: list[float] = []
            quantile_values: list[list[float]] = [[] for _ in range(5)]
            timeline: list[FactorTimelinePoint] = []
            top_sets: list[set[str]] = []
            observation_count = 0
            for timestamp, items in sorted(by_time.items()):
                if len(items) < 2:
                    continue
                ordered = sorted(items, key=lambda item: (item.value, item.symbol))
                factor_values = [item.value for item in ordered]
                forward_values = [_required_future(item, horizon) for item in ordered]
                ic = _pearson(factor_values, forward_values)
                rank_ic = _pearson(_ranks(factor_values), _ranks(forward_values))
                if ic is not None:
                    daily_ic.append(ic)
                if rank_ic is not None:
                    daily_rank_ic.append(rank_ic)
                daily_quantiles: list[float | None] = []
                top: set[str] = set()
                for quantile in range(5):
                    bucket = [
                        item
                        for rank, item in enumerate(ordered)
                        if min(4, rank * 5 // len(ordered)) == quantile
                    ]
                    returns = [_required_future(item, horizon) for item in bucket]
                    mean = _safe_mean(returns)
                    daily_quantiles.append(mean)
                    if mean is not None:
                        quantile_values[quantile].extend(returns)
                    if quantile == 4:
                        top = {item.symbol for item in bucket}
                top_sets.append(top)
                spread = (
                    None
                    if daily_quantiles[0] is None or daily_quantiles[4] is None
                    else daily_quantiles[4] - daily_quantiles[0]
                )
                timeline.append(
                    FactorTimelinePoint(
                        timestamp=timestamp,
                        ic=ic,
                        rank_ic=rank_ic,
                        quantile_returns=tuple(daily_quantiles),
                        long_short_spread=spread,
                    )
                )
                observation_count += len(items)
            quantiles = tuple(_safe_mean(values) for values in quantile_values)
            spreads = [
                item.long_short_spread for item in timeline if item.long_short_spread is not None
            ]
            turnover_values = [
                1 - len(previous & current) / max(len(previous), 1)
                for previous, current in zip(top_sets, top_sets[1:], strict=False)
            ]
            potential = sum(max(item.universe_size for item in items) for items in by_time.values())
            horizons.append(
                HorizonEvaluation(
                    horizon=horizon,
                    observation_count=observation_count,
                    cross_section_count=len(timeline),
                    ic=_safe_mean(daily_ic),
                    rank_ic=_safe_mean(daily_rank_ic),
                    ic_stability=_stability(daily_ic),
                    rank_ic_stability=_stability(daily_rank_ic),
                    quantile_returns=quantiles,
                    long_short_spread=_safe_mean(spreads),
                    turnover=_safe_mean(turnover_values),
                    coverage=observation_count / potential if potential else 0.0,
                    monotonic=_is_monotonic(quantiles),
                    timeline=tuple(timeline),
                )
            )
        return PeriodEvaluation(stage=stage, period=period, horizons=tuple(horizons))

    @staticmethod
    def _public_observation(
        item: _ComputedObservation,
        *,
        research_id: str,
        factor_id: str,
    ) -> FactorObservation:
        del research_id
        return FactorObservation(
            symbol=item.symbol,
            timestamp=item.timestamp,
            factor_id=factor_id,
            value=item.value,
            window_start=item.window_start,
            window_end=item.timestamp,
            available_at=item.available_at,
            future_returns=item.future_returns,
            future_return_timestamps=item.future_return_timestamps,
            dependencies=item.dependencies,
            fundamental_inputs=item.fundamental_inputs,
        )

    def create(self, request: CreateFactorResearch) -> FactorResearchRecord:
        frames, universe, parameters, universe_record, fundamental_dataset = self._inputs(request)
        definition = self.factors.definition(request.factor_id)
        lookback = int(parameters.get("lookback", definition.lookback))
        observations = self._compute(
            frames=frames,
            universe=universe,
            universe_record=universe_record,
            definition=definition,
            parameters=parameters,
            fundamental_dataset=fundamental_dataset,
            components=request.components,
        )
        research_id = f"factor-research-{secrets.token_hex(10)}"
        research_eval = self._evaluation(
            stage="RESEARCH",
            period=request.periods.research,
            observations=observations,
            universe_size=len(universe),
        )
        research_items = [
            item
            for item in observations
            if request.periods.research.start <= item.timestamp <= request.periods.research.end
        ]
        sample_indices = (
            []
            if not research_items
            else sorted({0, len(research_items) // 2, len(research_items) - 1})
        )
        samples = tuple(
            self._public_observation(
                research_items[index],
                research_id=research_id,
                factor_id=definition.factor_id,
            )
            for index in sample_indices
        )
        dataset = self.datasets.get(request.dataset_id)
        if dataset is None:
            raise KeyError(request.dataset_id)
        return FactorResearchRecord(
            research_id=research_id,
            name=request.name,
            created_at=datetime.now(UTC),
            dataset_id=dataset.dataset_id,
            dataset_name=dataset.name,
            dataset_revision=dataset.content_fingerprint,
            factor=definition.model_copy(update={"lookback": lookback}),
            parameters=parameters,
            components=request.components,
            universe=universe,
            universe_id=universe_record.universe_id,
            universe_mode=universe_record.mode,
            survivorship_bias_free=universe_record.survivorship_bias_free,
            survivorship_warning=universe_record.disclosure,
            periods=request.periods,
            evaluations=(research_eval,),
            factor_observation_count=len(observations),
            sample_observations=samples,
            fundamental_dataset_id=(
                None if fundamental_dataset is None else fundamental_dataset.fundamental_dataset_id
            ),
            fundamental_provider=(
                None if fundamental_dataset is None else fundamental_dataset.provider
            ),
            restatement_safe=(
                True if fundamental_dataset is None else fundamental_dataset.restatement_safe
            ),
            restatement_warning=(
                None
                if fundamental_dataset is None or fundamental_dataset.restatement_safe
                else fundamental_dataset.disclosure
            ),
        )

    def _record_inputs(
        self, record: FactorResearchRecord
    ) -> tuple[tuple[MarketFrame, ...], HistoricalUniverse, FundamentalDataset | None]:
        dataset = self.datasets.get(record.dataset_id)
        if dataset is None:
            raise KeyError(record.dataset_id)
        universe = (
            self.universes.get(record.universe_id)
            if record.universe_id is not None
            else self.universes.static_for_dataset(dataset)
        )
        if universe is None:
            raise KeyError(record.universe_id or "")
        fundamentals = (
            self.fundamentals.get(record.fundamental_dataset_id)
            if record.fundamental_dataset_id is not None
            else None
        )
        return self.datasets.load_frames(record.dataset_id, record.universe), universe, fundamentals

    def observations(self, record: FactorResearchRecord) -> tuple[FactorObservation, ...]:
        """Recompute one saved Factor study through the canonical Factor Engine.

        Portfolio and relationship research use this public boundary instead of duplicating
        Factor formulas or reading cached frontend values.
        """
        frames, universe_record, fundamental_dataset = self._record_inputs(record)
        observations = self._compute(
            frames=frames,
            universe=record.universe,
            universe_record=universe_record,
            definition=record.factor,
            parameters=record.parameters,
            fundamental_dataset=fundamental_dataset,
            components=record.components,
        )
        return tuple(
            self._public_observation(
                item,
                research_id=record.research_id,
                factor_id=record.factor.factor_id,
            )
            for item in observations
        )

    def evaluate_periods(
        self,
        record: FactorResearchRecord,
        periods: tuple[tuple[ResearchStage, ResearchPeriod], ...],
    ) -> tuple[PeriodEvaluation, ...]:
        """Evaluate many windows from one canonical factor computation.

        Forward-return endpoints are constrained by each period's end inside
        ``_evaluation`` so callers cannot accidentally reintroduce boundary leakage.
        """
        frames, universe_record, fundamental_dataset = self._record_inputs(record)
        observations = self._compute(
            frames=frames,
            universe=record.universe,
            universe_record=universe_record,
            definition=record.factor,
            parameters=record.parameters,
            fundamental_dataset=fundamental_dataset,
            components=record.components,
        )
        return tuple(
            self._evaluation(
                stage=stage,
                period=period,
                observations=observations,
                universe_size=len(record.universe),
            )
            for stage, period in periods
        )

    def reveal(self, record: FactorResearchRecord, stage: ResearchStage) -> FactorResearchRecord:
        allowed = {
            ("RESEARCH", "VALIDATION"),
            ("VALIDATION", "HOLDOUT"),
        }
        if (record.revealed_stage, stage) not in allowed:
            if record.revealed_stage == stage:
                return record
            raise ValueError(f"Cannot reveal {stage} after {record.revealed_stage}")
        frames, universe_record, fundamental_dataset = self._record_inputs(record)
        observations = self._compute(
            frames=frames,
            universe=record.universe,
            universe_record=universe_record,
            definition=record.factor,
            parameters=record.parameters,
            fundamental_dataset=fundamental_dataset,
            components=record.components,
        )
        period = record.periods.validation if stage == "VALIDATION" else record.periods.holdout
        evaluation = self._evaluation(
            stage=stage,
            period=period,
            observations=observations,
            universe_size=len(record.universe),
        )
        return record.model_copy(
            update={
                "revealed_stage": stage,
                "evaluations": (*record.evaluations, evaluation),
            }
        )

    def inspect(
        self, record: FactorResearchRecord, symbol: str, timestamp: datetime
    ) -> FactorInspection:
        normalized = symbol.upper()
        if normalized not in record.universe:
            raise ValueError(f"Symbol '{normalized}' is not in this research universe")
        frames, universe_record, fundamental_dataset = self._record_inputs(record)
        observations = self._compute(
            frames=frames,
            universe=record.universe,
            universe_record=universe_record,
            definition=record.factor,
            parameters=record.parameters,
            fundamental_dataset=fundamental_dataset,
            components=record.components,
        )
        candidates = [
            item
            for item in observations
            if item.symbol == normalized and item.timestamp <= timestamp
        ]
        if not candidates:
            raise ValueError("No available factor observation at or before that timestamp")
        selected = candidates[-1]
        return FactorInspection(
            research_id=record.research_id,
            observation=self._public_observation(
                selected,
                research_id=record.research_id,
                factor_id=record.factor.factor_id,
            ),
            formula=record.factor.formula,
            parameter_values=record.parameters,
            restatement_status=("SAFE" if record.restatement_safe else "NOT_RESTATEMENT_SAFE"),
        )

    def historical_market(
        self,
        dataset_id: str,
        requested_as_of: datetime,
        selected_symbol: str | None,
        fundamental_dataset_id: str | None = None,
        universe_id: str | None = None,
    ) -> HistoricalMarketView:
        dataset = self.datasets.get(dataset_id)
        if dataset is None:
            raise KeyError(f"Dataset '{dataset_id}' was not found")
        if dataset.source_type != "PROVIDER":
            raise ValueError("Historical Market requires a real provider-backed dataset")
        frames = self.datasets.load_frames(dataset_id, dataset.symbols)
        eligible = [frame for frame in frames if frame.timestamp <= requested_as_of]
        if not eligible:
            raise ValueError("No market cross-section is available at or before that time")
        frame = eligible[-1]
        index = frames.index(frame)
        universe_record = (
            self.universes.get(universe_id)
            if universe_id is not None
            else self.universes.static_for_dataset(dataset)
        )
        if universe_record is None:
            raise KeyError(f"Universe '{universe_id}' was not found")
        active_symbols = universe_record.symbols_at(frame.knowledge_time)
        rows: list[HistoricalSecurityRow] = []
        for symbol in active_symbols:
            close = frame.value(symbol, "close")
            previous_close = frames[index - 1].value(symbol, "close") if index else None
            start = max(0, index - 20)
            history = frames[start : index + 1]
            closes = [item.value(symbol, "close") for item in history]
            returns = [
                current / previous - 1
                for previous, current in zip(closes, closes[1:], strict=False)
            ]
            volumes = [
                item.value(symbol, "volume") for item in history if "volume" in item.values[symbol]
            ]
            fields = frame.values[symbol]
            rows.append(
                HistoricalSecurityRow(
                    symbol=symbol,
                    company=dataset.security_names.get(symbol, symbol),
                    close=close,
                    return_1d=None if previous_close is None else close / previous_close - 1,
                    volume=fields.get("volume"),
                    volatility_20d=statistics.pstdev(returns) if len(returns) > 1 else None,
                    high_low_range=(fields["high"] - fields["low"]) / close
                    if "high" in fields and "low" in fields
                    else None,
                    average_volume_20d=_safe_mean(volumes),
                )
            )
        symbol = (selected_symbol or active_symbols[0]).upper()
        if symbol not in active_symbols:
            raise ValueError(f"Symbol '{symbol}' is not in this dataset")
        trend = tuple(
            HistoricalTrendPoint(
                timestamp=item.timestamp,
                close=item.value(symbol, "close"),
                volume=item.values[symbol].get("volume"),
            )
            for item in frames[max(0, index - 119) : index + 1]
        )
        fundamental_dataset = (
            self.fundamentals.get(fundamental_dataset_id)
            if fundamental_dataset_id is not None
            else None
        )
        if fundamental_dataset_id is not None and fundamental_dataset is None:
            raise KeyError(f"Fundamental dataset '{fundamental_dataset_id}' was not found")
        fundamental_snapshot = (
            None
            if fundamental_dataset is None
            else self.fundamentals.snapshot(
                fundamental_dataset,
                symbol=symbol,
                used_at=frame.knowledge_time,
            )
        )
        return HistoricalMarketView(
            dataset_id=dataset.dataset_id,
            dataset_revision=dataset.content_fingerprint,
            source=(
                f"{dataset.provenance.provider}:{dataset.provenance.feed}"
                if dataset.provenance
                else dataset.source_type
            ),
            requested_as_of=requested_as_of,
            as_of=frame.timestamp,
            universe_id=universe_record.universe_id,
            universe_source=universe_record.source,
            universe_mode=universe_record.mode,
            survivorship_bias_free=universe_record.survivorship_bias_free,
            universe_disclosure=universe_record.disclosure,
            cross_section=tuple(rows),
            selected_symbol=symbol,
            trend=trend,
            fundamentals=fundamental_snapshot,
        )
