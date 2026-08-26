from __future__ import annotations

import importlib
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal

from ..loading import load_source_module, strategy_manifest
from ..models import (
    AdapterDataRequirements,
    AdapterEquityPoint,
    AdapterExecutionRecord,
    AdapterFeaturePoint,
    AdapterInspection,
    AdapterOrderRecord,
    AdapterPositionPoint,
    AdapterRunRequest,
    AdapterRunResult,
    AdapterSignalPoint,
    AdapterTradeRecord,
    TraceCapabilitySet,
    derive_trace_fidelity,
)
from ..validation import validate_dataset, validate_parameters


@dataclass(frozen=True, slots=True)
class VectorbtRunSpec:
    portfolio: object
    signals: dict[str, object]
    features: dict[str, object]
    feature_dependencies: dict[str, tuple[str, ...]]
    initial_equity: float | None = None


class VectorbtContext:
    """VQD-owned data context passed to a vectorbt adapter entrypoint."""

    def __init__(self, request: AdapterRunRequest) -> None:
        self._request = request
        pandas = importlib.import_module("pandas")
        self.index = pandas.DatetimeIndex(
            [point.timestamp for point in request.dataset.points], name="timestamp"
        )
        self.symbols = request.dataset.symbols

    @property
    def parameters(self) -> dict[str, int | float]:
        return dict(self._request.parameters)

    def field(self, field: str, symbol: str | None = None) -> Any:
        pandas = importlib.import_module("pandas")
        if field not in self._request.dataset.fields:
            raise ValueError(f"VQD dataset does not provide field '{field}'")
        if symbol is not None:
            if symbol not in self.symbols:
                raise ValueError(f"VQD dataset does not provide symbol '{symbol}'")
            return pandas.Series(
                [point.values[symbol][field] for point in self._request.dataset.points],
                index=self.index,
                name=symbol,
            )
        if len(self.symbols) == 1:
            return self.field(field, self.symbols[0])
        return pandas.DataFrame(
            {
                item: [point.values[item][field] for point in self._request.dataset.points]
                for item in self.symbols
            },
            index=self.index,
        )

    def close(self, symbol: str | None = None) -> Any:
        return self.field("close", symbol)

    def result(
        self,
        *,
        portfolio: object,
        signals: dict[str, object] | None = None,
        features: dict[str, object] | None = None,
        feature_dependencies: dict[str, tuple[str, ...]] | None = None,
        initial_equity: float | None = None,
    ) -> VectorbtRunSpec:
        return VectorbtRunSpec(
            portfolio=portfolio,
            signals=signals or {},
            features=features or {},
            feature_dependencies=feature_dependencies or {},
            initial_equity=initial_equity,
        )


def _datetime(value: Any, index: Any) -> datetime:
    if isinstance(value, int):
        value = index[value]
    converted = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if not isinstance(converted, datetime):
        raise ValueError(f"vectorbt record timestamp is not aligned to the VQD index: {value!r}")
    if converted.tzinfo is None or converted.utcoffset() is None:
        raise ValueError("vectorbt returned a naive timestamp; VQD requires UTC alignment")
    return converted.astimezone(UTC)


def _number(value: Any) -> float | None:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


class VectorbtAdapter:
    adapter_id = "vectorbt"
    adapter_version = "1"
    framework_name = "vectorbt"
    distribution_name = "vectorbt"

    @staticmethod
    def _framework() -> tuple[Any, str]:
        try:
            framework_version = version("vectorbt")
        except PackageNotFoundError as exc:
            raise PackageNotFoundError(
                "vectorbt is not installed in the VQD runtime. "
                "Install the optional 'framework-vectorbt' extra."
            ) from exc
        return importlib.import_module("vectorbt"), framework_version

    def inspect(self, source_path: str, entrypoint: str) -> AdapterInspection:
        _, framework_version = self._framework()
        module, path = load_source_module(source_path)
        function = getattr(module, entrypoint, None)
        if not callable(function):
            raise ValueError(f"'{entrypoint}' must be a callable vectorbt VQD entrypoint in {path}")
        manifest = strategy_manifest(
            module,
            path,
            requirements=AdapterDataRequirements(required_fields=("close",)),
        )
        return AdapterInspection(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            framework_name=self.framework_name,
            framework_version=framework_version,
            installed=True,
            available=True,
            manifest=manifest,
            entrypoint=entrypoint,
        )

    @staticmethod
    def _aligned_columns(
        value: object, context: VectorbtContext, label: str
    ) -> list[tuple[str, Any]]:
        pandas = importlib.import_module("pandas")
        numpy = importlib.import_module("numpy")
        if isinstance(value, pandas.Series):
            if not value.index.equals(context.index):
                raise ValueError(f"{label} index does not exactly match the VQD dataset index")
            return [("", value.to_numpy())]
        if isinstance(value, pandas.DataFrame):
            if not value.index.equals(context.index):
                raise ValueError(f"{label} index does not exactly match the VQD dataset index")
            columns = tuple(map(str, value.columns))
            if columns != context.symbols:
                raise ValueError(
                    f"{label} columns must exactly match VQD symbols {context.symbols}; "
                    f"received {columns}"
                )
            return [(str(column), value[column].to_numpy()) for column in value.columns]
        array = numpy.asarray(value)
        if array.ndim == 1 and len(array) == len(context.index):
            return [("", array)]
        if array.ndim == 2 and array.shape == (len(context.index), len(context.symbols)):
            return [(symbol, array[:, index]) for index, symbol in enumerate(context.symbols)]
        raise ValueError(
            f"{label} must be a Series/DataFrame or aligned array with one explicit configuration"
        )

    @staticmethod
    def _symbol(value: Any, symbols: tuple[str, ...]) -> str:
        rendered = str(value)
        if rendered in symbols:
            return rendered
        if len(symbols) == 1:
            return symbols[0]
        raise ValueError(f"vectorbt record column '{rendered}' is not a VQD dataset symbol")

    def execute(self, request: AdapterRunRequest) -> AdapterRunResult:
        started = time.perf_counter()
        vectorbt, framework_version = self._framework()
        if request.adapter_id != self.adapter_id:
            raise ValueError("Adapter request identity does not match vectorbt")
        validate_dataset(request.dataset, request.manifest)
        parameters = validate_parameters(request.parameters, request.manifest)
        normalized_request = request.model_copy(update={"parameters": parameters})
        module, _ = load_source_module(request.source_path)
        function = getattr(module, request.entrypoint, None)
        if not callable(function):
            raise ValueError(f"'{request.entrypoint}' is not a callable vectorbt entrypoint")
        context = VectorbtContext(normalized_request)
        raw_spec = function(context, **parameters)
        if not isinstance(raw_spec, VectorbtRunSpec):
            raise ValueError("vectorbt entrypoint must return ctx.result(...) / VectorbtRunSpec")
        if not isinstance(raw_spec.portfolio, vectorbt.Portfolio):
            raise ValueError("VectorbtRunSpec.portfolio must be a vectorbt.Portfolio")
        framework_seconds = time.perf_counter() - started
        normalization_started = time.perf_counter()
        pandas = importlib.import_module("pandas")
        numpy = importlib.import_module("numpy")

        raw_equity = raw_spec.portfolio.value()
        if isinstance(raw_equity, pandas.DataFrame):
            equity_series = raw_equity.sum(axis=1)
        elif isinstance(raw_equity, pandas.Series):
            equity_series = raw_equity
        else:
            values = numpy.asarray(raw_equity)
            if values.ndim != 1:
                raise ValueError("vectorbt portfolio value must resolve to one equity timeline")
            equity_series = pandas.Series(values, index=context.index)
        if not equity_series.index.equals(context.index):
            raise ValueError("vectorbt portfolio equity index does not match the VQD dataset")
        equity = tuple(
            AdapterEquityPoint(timestamp=timestamp, equity=float(value))
            for timestamp, value in zip(
                (point.timestamp for point in request.dataset.points),
                equity_series.to_numpy(),
                strict=True,
            )
        )
        if raw_spec.initial_equity is not None:
            initial_equity = raw_spec.initial_equity
        else:
            init_cash = numpy.asarray(raw_spec.portfolio.init_cash)
            initial_equity = float(init_cash.sum())

        features: list[AdapterFeaturePoint] = []
        for name, value in raw_spec.features.items():
            for symbol, values in self._aligned_columns(value, context, f"feature '{name}'"):
                feature_name = name if not symbol else f"{name}[{symbol}]"
                inputs = raw_spec.feature_dependencies.get(name, ())
                for timestamp, raw_value in zip(
                    (point.timestamp for point in request.dataset.points), values, strict=True
                ):
                    features.append(
                        AdapterFeaturePoint(
                            timestamp=timestamp,
                            name=feature_name,
                            value=_number(raw_value),
                            formula="Provided explicitly by vectorbt adapter entrypoint",
                            inputs=inputs,
                        )
                    )

        signals: list[AdapterSignalPoint] = []
        for name, value in raw_spec.signals.items():
            for symbol, values in self._aligned_columns(value, context, f"signal '{name}'"):
                for timestamp, raw_value in zip(
                    (point.timestamp for point in request.dataset.points), values, strict=True
                ):
                    signals.append(
                        AdapterSignalPoint(
                            timestamp=timestamp,
                            name=name,
                            active=bool(raw_value),
                            symbol=symbol or None,
                        )
                    )

        order_rows = raw_spec.portfolio.orders.records_readable.to_dict("records")
        orders: list[AdapterOrderRecord] = []
        executions: list[AdapterExecutionRecord] = []
        for index, row in enumerate(order_rows, start=1):
            timestamp = _datetime(row.get("Timestamp"), context.index)
            symbol = self._symbol(row.get("Column"), request.dataset.symbols)
            side: Literal["BUY", "SELL"] = (
                "BUY" if str(row.get("Side", "")).lower() == "buy" else "SELL"
            )
            quantity = abs(float(row.get("Size", 0.0)))
            price = float(row.get("Price", 0.0))
            fee = float(row.get("Fees", 0.0))
            order_id = f"vbt-order-{index:06d}"
            orders.append(
                AdapterOrderRecord(
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    submitted_at=timestamp,
                    expected_execution_at=timestamp,
                    price=price,
                    status="VECTORBT_FILLED_ORDER",
                )
            )
            executions.append(
                AdapterExecutionRecord(
                    execution_id=f"vbt-execution-{index:06d}",
                    source_order_id=order_id,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    executed_at=timestamp,
                    fee=fee,
                    meaning="vectorbt portfolio order record",
                )
            )

        quantities = {symbol: 0.0 for symbol in request.dataset.symbols}
        executions_by_time: dict[datetime, list[AdapterExecutionRecord]] = {}
        for execution in executions:
            executions_by_time.setdefault(execution.executed_at, []).append(execution)
        positions: list[AdapterPositionPoint] = []
        for point in request.dataset.points:
            for execution in executions_by_time.get(point.timestamp, []):
                signed = execution.quantity if execution.side == "BUY" else -execution.quantity
                quantities[execution.symbol] += signed
            positions.append(
                AdapterPositionPoint(
                    timestamp=point.timestamp,
                    quantities=dict(quantities),
                    market_values={
                        symbol: quantities[symbol] * point.values[symbol]["close"]
                        for symbol in request.dataset.symbols
                    },
                )
            )

        trade_rows = raw_spec.portfolio.trades.records_readable.to_dict("records")
        trades: list[AdapterTradeRecord] = []
        for index, row in enumerate(trade_rows, start=1):
            opened_at = _datetime(row.get("Entry Timestamp", row.get("Entry Index")), context.index)
            raw_exit = row.get("Exit Timestamp", row.get("Exit Index"))
            status_value = str(row.get("Status", "Closed"))
            closed = status_value.lower() == "closed"
            closed_at = _datetime(raw_exit, context.index) if closed else None
            direction = str(row.get("Direction", "Long")).upper()
            trades.append(
                AdapterTradeRecord(
                    trade_id=f"vbt-trade-{index:06d}",
                    symbol=self._symbol(row.get("Column"), request.dataset.symbols),
                    direction=direction,
                    status="CLOSED" if closed else "OPEN",
                    opened_at=opened_at,
                    closed_at=closed_at,
                    entry_price=float(row.get("Avg Entry Price", row.get("Entry Price", 0.0))),
                    exit_price=(
                        _number(row.get("Avg Exit Price", row.get("Exit Price")))
                        if closed
                        else None
                    ),
                    quantity=abs(float(row.get("Size", 0.0))),
                    pnl=_number(row.get("PnL")),
                    fees=None,
                )
            )

        capabilities = TraceCapabilitySet(
            market_timeline="AVAILABLE",
            feature_values="AVAILABLE" if features else "UNAVAILABLE",
            feature_lineage=(
                "PARTIAL" if features and raw_spec.feature_dependencies else "UNAVAILABLE"
            ),
            decision_events="AVAILABLE" if signals else "UNAVAILABLE",
            decision_conditions="UNAVAILABLE",
            data_dependencies="UNAVAILABLE",
            orders="AVAILABLE",
            executions="AVAILABLE",
            positions="AVAILABLE",
            trades="AVAILABLE",
            equity="AVAILABLE",
            pnl="AVAILABLE",
            point_in_time_proven="UNAVAILABLE",
            gross_pnl="PARTIAL",
            fees="AVAILABLE",
            slippage="UNAVAILABLE",
            trade_attribution="AVAILABLE",
            drawdowns="AVAILABLE",
        )
        return AdapterRunResult(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            framework_name=self.framework_name,
            framework_version=framework_version,
            execution_owner=self.framework_name,
            strategy_id=request.manifest.strategy_id,
            strategy_name=request.manifest.name,
            parameters=parameters,
            dataset_revision=request.dataset.revision,
            execution_semantics={
                "engine": "vectorbt Portfolio",
                **request.manifest.execution_config,
            },
            initial_equity=initial_equity,
            market_timeline=request.dataset.points,
            features=tuple(features),
            signals=tuple(signals),
            orders=tuple(orders),
            executions=tuple(executions),
            positions=tuple(positions),
            trades=tuple(trades),
            equity=equity,
            framework_metrics={
                "final_equity": equity[-1].equity,
                "order_count": len(orders),
                "trade_count": len(trades),
            },
            capabilities=capabilities,
            fidelity=derive_trace_fidelity(capabilities),
            warnings=(
                "Point-in-time provenance is not available for vectorized arrays; supplied "
                "signals and features are recorded without inferring their computation.",
                "Portfolio order records are the accounting authority; VQD does not rerun "
                "orders through its native execution engine.",
            ),
            determinism=("SEEDED" if request.manifest.random_seed is not None else "UNVERIFIED"),
            random_seed=request.manifest.random_seed,
            adapter_runtime_seconds=framework_seconds,
            normalization_seconds=time.perf_counter() - normalization_started,
        )
