from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Literal

from app.fundamentals import FundamentalObservation, FundamentalRepository
from app.models import DecisionCondition, MarketFrame
from app.sdk.models import (
    FeatureRecord,
    FeatureRef,
    MarketSeries,
    MarketValueRef,
    ParameterValue,
    TargetPortfolioIntent,
)
from app.trace.models import DataDependency, TraceScalar

FeatureInput = FeatureRef | MarketValueRef | MarketSeries


class StrategyContext:
    """The point-in-time-safe API exposed to a native strategy."""

    def __init__(
        self,
        *,
        frames: tuple[MarketFrame, ...],
        parameters: Mapping[str, ParameterValue],
        current_positions: Mapping[str, float],
        previous_target_signature: tuple[tuple[str, float], ...],
        next_feature_id: Callable[[], str],
        next_dependency_id: Callable[[], str],
        fundamental_repository: FundamentalRepository | None = None,
        active_symbols: tuple[str, ...] | None = None,
    ) -> None:
        if not frames:
            raise ValueError("A strategy context requires a current frame")
        self.__frames = frames
        self.parameters = MappingProxyType(dict(parameters))
        self.current_positions = MappingProxyType(dict(current_positions))
        self.current_time = frames[-1].knowledge_time
        self.__active_symbols = None if active_symbols is None else frozenset(active_symbols)
        visible_symbols = (
            tuple(frames[-1].values)
            if active_symbols is None
            else tuple(symbol for symbol in active_symbols if symbol in frames[-1].values)
        )
        self.market = MappingProxyType(
            {
                symbol: MappingProxyType(dict(frames[-1].values[symbol]))
                for symbol in visible_symbols
            }
        )
        self.__previous_target_signature = previous_target_signature
        self.__next_feature_id = next_feature_id
        self.__next_dependency_id = next_dependency_id
        self.__fundamental_repository = fundamental_repository
        self.__dependency_by_key: dict[tuple[datetime, str, str], MarketValueRef] = {}
        self.__external_dependency_by_key: dict[tuple[str, datetime, str, str], MarketValueRef] = {}
        self.__dependencies: list[DataDependency] = []
        self.__features: list[FeatureRecord] = []
        for symbol in visible_symbols:
            for field in frames[-1].values[symbol]:
                self.__market_ref(frames[-1], symbol, field)

    @property
    def features(self) -> tuple[FeatureRecord, ...]:
        return tuple(self.__features)

    @property
    def symbols(self) -> tuple[str, ...]:
        """Symbols in the strategy's configured market order."""
        return tuple(self.market)

    @property
    def data_dependencies(self) -> tuple[DataDependency, ...]:
        return tuple(self.__dependencies)

    def __market_ref(self, frame: MarketFrame, symbol: str, field: str) -> MarketValueRef:
        key = (frame.timestamp, symbol, field)
        existing = self.__dependency_by_key.get(key)
        if existing is not None:
            return existing
        value = frame.value(symbol, field)
        dependency_id = self.__next_dependency_id()
        ref = MarketValueRef(
            symbol=symbol,
            field=field,
            value=value,
            timestamp=frame.timestamp,
            dependency_id=dependency_id,
        )
        self.__dependency_by_key[key] = ref
        self.__dependencies.append(
            DataDependency(
                dependency_id=dependency_id,
                source="market_data",
                field=field,
                symbol=symbol,
                value=value,
                source_timestamp=frame.timestamp,
                available_at=frame.knowledge_time,
                used_at=self.current_time,
            )
        )
        return ref

    def __require_active_symbol(self, symbol: str) -> None:
        if self.__active_symbols is not None and symbol not in self.__active_symbols:
            raise ValueError(
                f"Symbol '{symbol}' is not active in the historical universe at "
                f"{self.current_time.isoformat()}"
            )

    def current(self, symbol: str, field: str = "close") -> MarketValueRef:
        self.__require_active_symbol(symbol)
        return self.__market_ref(self.__frames[-1], symbol, field)

    def external_value(
        self,
        *,
        source: str,
        symbol: str,
        field: str,
        value: float,
        source_timestamp: datetime,
        available_at: datetime,
    ) -> MarketValueRef:
        """Register a non-market observation that was genuinely available by current_time."""
        if source_timestamp.tzinfo is None or available_at.tzinfo is None:
            raise ValueError("External dependency timestamps must be timezone-aware")
        if available_at > self.current_time:
            raise ValueError("External dependency is not available at the current strategy time")
        key = (source, source_timestamp, symbol, field)
        existing = self.__external_dependency_by_key.get(key)
        if existing is not None:
            return existing
        dependency_id = self.__next_dependency_id()
        ref = MarketValueRef(
            symbol=symbol,
            field=field,
            value=value,
            timestamp=source_timestamp,
            dependency_id=dependency_id,
        )
        self.__external_dependency_by_key[key] = ref
        self.__dependencies.append(
            DataDependency(
                dependency_id=dependency_id,
                source=source,
                field=field,
                symbol=symbol,
                value=value,
                source_timestamp=source_timestamp,
                available_at=available_at,
                used_at=self.current_time,
            )
        )
        return ref

    def fundamental_observations(
        self,
        *,
        dataset_id: str,
        symbol: str,
        field: str,
        period_type: str | None = None,
        count: int = 1,
    ) -> tuple[FundamentalObservation, ...]:
        self.__require_active_symbol(symbol)
        if self.__fundamental_repository is None:
            return ()
        dataset = self.__fundamental_repository.get(dataset_id)
        if dataset is None:
            return ()
        return self.__fundamental_repository.latest_available(
            dataset,
            symbol=symbol,
            field=field,
            used_at=self.current_time,
            period_type=period_type,
            count=count,
        )

    def history(self, *, symbol: str, field: str = "close", bars: int) -> MarketSeries:
        self.__require_active_symbol(symbol)
        if bars < 1:
            raise ValueError("history bars must be positive")
        available = [
            frame
            for frame in self.__frames
            if symbol in frame.values and field in frame.values[symbol]
        ]
        selected = available[-bars:]
        return MarketSeries(tuple(self.__market_ref(frame, symbol, field) for frame in selected))

    def feature(
        self,
        *,
        name: str,
        value: float | None,
        inputs: Iterable[FeatureInput] = (),
        formula: str,
        parameters: Mapping[str, TraceScalar] | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> FeatureRef:
        feature_inputs: list[str] = []
        dependencies: list[str] = []
        for item in inputs:
            if isinstance(item, FeatureRef):
                feature_inputs.append(item.feature_id)
            elif isinstance(item, MarketValueRef):
                dependencies.append(item.dependency_id)
            else:
                dependencies.extend(point.dependency_id for point in item.dependencies)
        record = FeatureRecord(
            feature_id=self.__next_feature_id(),
            name=name,
            value=value,
            formula=formula,
            inputs=tuple(dict.fromkeys(feature_inputs)),
            parameters=MappingProxyType(dict(parameters or {})),
            window_start=window_start,
            window_end=window_end,
            available_at=self.current_time,
            data_dependencies=tuple(dict.fromkeys(dependencies)),
        )
        self.__features.append(record)
        return FeatureRef(record)

    def condition(
        self,
        *,
        left: str,
        left_value: float | None,
        operator: str,
        right: str | None,
        right_value: float | None,
        result: bool,
        description: str,
    ) -> DecisionCondition:
        return DecisionCondition(
            left_operand=left,
            left_value=left_value,
            operator=operator,
            right_operand=right,
            right_value=right_value,
            result=result,
            description=description,
        )

    @staticmethod
    def __dependency_ids(dependencies: Iterable[FeatureRef]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.feature_id for item in dependencies))

    @staticmethod
    def __state_target(values: Mapping[str, float]) -> Literal[-1, 0, 1]:
        first = next((value for value in values.values() if abs(value) >= 1e-12), 0.0)
        return 1 if first > 0 else -1 if first < 0 else 0

    def target_positions(
        self,
        positions: Mapping[str, float],
        *,
        reason: str,
        dependencies: Iterable[FeatureRef] = (),
        conditions: Iterable[DecisionCondition] = (),
        signal: str = "TARGET_POSITIONS",
        previous_state: str = "CURRENT",
        next_state: str = "TARGET",
        transition: bool | None = None,
        target_state: Literal[-1, 0, 1] | None = None,
    ) -> TargetPortfolioIntent:
        signature = tuple(sorted((symbol, float(value)) for symbol, value in positions.items()))
        changed = (
            signature != self.__previous_target_signature if transition is None else transition
        )
        return TargetPortfolioIntent(
            target_positions=positions,
            target_weights={},
            gross_notional=None,
            reason=reason,
            signal=signal,
            conditions=tuple(conditions),
            dependencies=self.__dependency_ids(dependencies),
            previous_state=previous_state,
            next_state=next_state,
            target_state=target_state
            if target_state is not None
            else self.__state_target(positions),
            transition=changed,
        )

    def target_weights(
        self,
        weights: Mapping[str, float],
        *,
        gross_notional: float,
        reason: str,
        dependencies: Iterable[FeatureRef] = (),
        conditions: Iterable[DecisionCondition] = (),
        signal: str = "TARGET_WEIGHTS",
        previous_state: str = "CURRENT",
        next_state: str = "TARGET",
        transition: bool | None = None,
        target_state: Literal[-1, 0, 1] | None = None,
    ) -> TargetPortfolioIntent:
        signature = tuple(sorted((symbol, float(value)) for symbol, value in weights.items()))
        changed = (
            signature != self.__previous_target_signature if transition is None else transition
        )
        return TargetPortfolioIntent(
            target_positions={},
            target_weights=weights,
            gross_notional=gross_notional,
            reason=reason,
            signal=signal,
            conditions=tuple(conditions),
            dependencies=self.__dependency_ids(dependencies),
            previous_state=previous_state,
            next_state=next_state,
            target_state=target_state if target_state is not None else self.__state_target(weights),
            transition=changed,
        )
