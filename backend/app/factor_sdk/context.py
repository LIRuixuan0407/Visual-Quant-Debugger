from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime
from types import MappingProxyType

from app.sdk.models import ParameterValue

from .models import FactorPoint, FactorResult, FactorSeries

CurrentReader = Callable[[str, str], FactorPoint]
HistoryReader = Callable[[str, str, int], FactorSeries]
FundamentalReader = Callable[[str, str, str | None, int, int], FactorSeries]
FactorReader = Callable[[str, str, Mapping[str, ParameterValue]], FactorResult]


class FactorContext:
    """Point-in-time-only data surface exposed to trusted local factors."""

    def __init__(
        self,
        *,
        used_at: datetime,
        parameters: Mapping[str, ParameterValue],
        current_reader: CurrentReader,
        history_reader: HistoryReader,
        fundamental_reader: FundamentalReader,
        factor_reader: FactorReader,
    ) -> None:
        if used_at.tzinfo is None or used_at.utcoffset() is None:
            raise ValueError("FactorContext used_at must be timezone-aware")
        self.used_at = used_at
        self.parameters = MappingProxyType(dict(parameters))
        self.__current_reader = current_reader
        self.__history_reader = history_reader
        self.__fundamental_reader = fundamental_reader
        self.__factor_reader = factor_reader

    def current(self, symbol: str, field: str = "close") -> FactorPoint:
        return self.__current_reader(symbol.upper(), field)

    def history(self, symbol: str, field: str = "close", bars: int = 1) -> FactorSeries:
        if bars < 1:
            raise ValueError("Factor history bars must be positive")
        return self.__history_reader(symbol.upper(), field, bars)

    def fundamental(
        self,
        symbol: str,
        field: str,
        *,
        period_type: str | None = None,
        count: int = 1,
        max_age_days: int = 550,
    ) -> FactorSeries:
        if count < 1 or max_age_days < 1:
            raise ValueError("Fundamental count and max_age_days must be positive")
        return self.__fundamental_reader(symbol.upper(), field, period_type, count, max_age_days)

    def factor(
        self,
        factor_id: str,
        symbol: str,
        parameters: Mapping[str, ParameterValue] | None = None,
    ) -> FactorResult:
        return self.__factor_reader(factor_id, symbol.upper(), parameters or {})

    def result(
        self,
        value: float | None,
        *,
        inputs: tuple[FactorPoint | FactorSeries | FactorResult, ...],
        formula: str,
    ) -> FactorResult:
        if value is not None and not math.isfinite(value):
            raise ValueError("Factor result must be finite or None")
        dependencies = tuple(
            dependency
            for item in inputs
            for dependency in (
                (item.dependency,)
                if isinstance(item, FactorPoint)
                else tuple(point.dependency for point in item.points)
                if isinstance(item, FactorSeries)
                else item.dependencies
            )
        )
        nested_tokens = any(
            isinstance(item, FactorResult) and item.token is not None for item in inputs
        )
        if value is not None and not dependencies and not nested_tokens:
            raise ValueError(
                "A computed factor result must declare at least one point-in-time input"
            )
        if any(item.available_at > self.used_at for item in dependencies):
            raise ValueError("Factor attempted to use data that was not yet available")
        window_start = min(
            (item.source_timestamp for item in dependencies),
            default=None,
        )
        return FactorResult(
            value=value,
            formula=formula,
            inputs=inputs,
            parameters=self.parameters,
            window_start=window_start,
            window_end=self.used_at,
            available_at=max(
                (item.available_at for item in dependencies),
                default=self.used_at,
            ),
        )
