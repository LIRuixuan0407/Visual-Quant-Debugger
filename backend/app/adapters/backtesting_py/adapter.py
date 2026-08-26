from __future__ import annotations

import importlib
import math
import time
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
    AdapterTradeRecord,
    TraceCapabilitySet,
    derive_trace_fidelity,
)
from ..validation import validate_dataset, validate_parameters


def _datetime(value: Any) -> datetime:
    converted = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if not isinstance(converted, datetime):
        raise ValueError(f"Framework timestamp is not a datetime: {value!r}")
    if converted.tzinfo is None or converted.utcoffset() is None:
        raise ValueError("backtesting.py returned a naive timestamp; VQD requires UTC provenance")
    return converted.astimezone(UTC)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _row_value(row: Any, *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


class BacktestingPyAdapter:
    adapter_id = "backtesting.py"
    adapter_version = "1"
    framework_name = "backtesting.py"
    distribution_name = "backtesting"

    @staticmethod
    def _framework() -> tuple[Any, Any, str]:
        try:
            framework_version = version("backtesting")
        except PackageNotFoundError as exc:
            raise PackageNotFoundError(
                "backtesting.py is not installed in the VQD runtime. "
                "Install the optional 'framework-backtesting' extra."
            ) from exc
        module = importlib.import_module("backtesting")
        return module.Backtest, module.Strategy, framework_version

    def inspect(self, source_path: str, entrypoint: str) -> AdapterInspection:
        _, strategy_base, framework_version = self._framework()
        module, path = load_source_module(source_path)
        strategy_class = getattr(module, entrypoint, None)
        if not isinstance(strategy_class, type) or not issubclass(strategy_class, strategy_base):
            raise ValueError(f"'{entrypoint}' must be a backtesting.Strategy subclass in {path}")
        manifest = strategy_manifest(
            module,
            path,
            requirements=AdapterDataRequirements(
                required_fields=("open", "high", "low", "close"),
                symbol_count=1,
            ),
        )
        required = set(manifest.data_requirements.required_fields)
        if not {"open", "high", "low", "close"}.issubset(required):
            raise ValueError("backtesting.py adapter manifest must require OHLC fields")
        if manifest.data_requirements.symbol_count != 1:
            raise ValueError("backtesting.py adapter supports exactly one symbol per run")
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
    def _frame(request: AdapterRunRequest) -> Any:
        pandas = importlib.import_module("pandas")
        symbol = request.dataset.symbols[0]
        rows = []
        timestamps = []
        for point in request.dataset.points:
            values = point.values[symbol]
            rows.append(
                {
                    "Open": values["open"],
                    "High": values["high"],
                    "Low": values["low"],
                    "Close": values["close"],
                    **({"Volume": values["volume"]} if "volume" in values else {}),
                }
            )
            timestamps.append(point.timestamp)
        return pandas.DataFrame(rows, index=pandas.DatetimeIndex(timestamps, name="timestamp"))

    def execute(self, request: AdapterRunRequest) -> AdapterRunResult:
        started = time.perf_counter()
        backtest_class, strategy_base, framework_version = self._framework()
        if request.adapter_id != self.adapter_id:
            raise ValueError("Adapter request identity does not match backtesting.py")
        validate_dataset(request.dataset, request.manifest)
        parameters = validate_parameters(request.parameters, request.manifest)
        module, _ = load_source_module(request.source_path)
        user_strategy = getattr(module, request.entrypoint, None)
        if not isinstance(user_strategy, type) or not issubclass(user_strategy, strategy_base):
            raise ValueError(f"'{request.entrypoint}' is not a backtesting.Strategy subclass")

        frame = self._frame(request)
        symbol = request.dataset.symbols[0]
        timestamps = tuple(point.timestamp for point in request.dataset.points)
        timestamp_indexes = {timestamp: index for index, timestamp in enumerate(timestamps)}
        captured_features: list[AdapterFeaturePoint] = []
        captured_positions: dict[datetime, float] = {}
        captured_orders: list[AdapterOrderRecord] = []
        seen_orders: set[int] = set()
        config = {
            "cash": 100_000.0,
            "spread": 0.0,
            "commission": 0.0,
            "margin": 1.0,
            "trade_on_close": False,
            "hedging": False,
            "exclusive_orders": False,
            "finalize_trades": True,
            **request.manifest.execution_config,
        }
        supported_config = {
            key: config[key]
            for key in (
                "cash",
                "spread",
                "commission",
                "margin",
                "trade_on_close",
                "hedging",
                "exclusive_orders",
                "finalize_trades",
            )
        }

        def capture(instance: Any) -> None:
            timestamp = _datetime(instance.data.index[-1])
            captured_positions[timestamp] = float(instance.position.size)
            # backtesting.py exposes Strategy.I publicly but keeps its registered indicator
            # collection in _indicators.  Access is intentionally isolated here and tested
            # against the supported optional dependency version.
            for indicator_index, indicator in enumerate(getattr(instance, "_indicators", ())):
                raw_name = getattr(indicator, "name", f"indicator_{indicator_index + 1}")
                name = (
                    "/".join(map(str, raw_name)) if isinstance(raw_name, tuple) else str(raw_name)
                )
                value = _number(indicator[-1])
                if value is not None:
                    captured_features.append(
                        AdapterFeaturePoint(
                            timestamp=timestamp,
                            name=name,
                            value=value,
                            formula="Declared through backtesting.Strategy.I; formula not recorded",
                        )
                    )
            current_index = timestamp_indexes[timestamp]
            expected_index = (
                current_index
                if bool(supported_config["trade_on_close"])
                else min(current_index + 1, len(timestamps) - 1)
            )
            for order in instance.orders:
                identity = id(order)
                if identity in seen_orders:
                    continue
                seen_orders.add(identity)
                size = float(order.size)
                captured_orders.append(
                    AdapterOrderRecord(
                        order_id=f"bt-order-{len(captured_orders) + 1:06d}",
                        symbol=symbol,
                        side="BUY" if size > 0 else "SELL",
                        quantity=abs(size),
                        submitted_at=timestamp,
                        expected_execution_at=timestamps[expected_index],
                        price=_number(getattr(order, "limit", None)),
                        status="FRAMEWORK_PENDING_ORDER",
                    )
                )

        strategy_type: Any = user_strategy
        user_init = strategy_type.init
        user_next = strategy_type.next

        def instrumented_init(instance: Any) -> None:
            user_init(instance)

        def instrumented_next(instance: Any) -> None:
            user_next(instance)
            capture(instance)

        instrumented_strategy = type(
            f"VQDInstrumented{user_strategy.__name__}",
            (user_strategy,),
            {"init": instrumented_init, "next": instrumented_next, "__module__": __name__},
        )
        backtest = backtest_class(frame, instrumented_strategy, **supported_config)
        stats = backtest.run(**parameters)
        framework_seconds = time.perf_counter() - started
        normalization_started = time.perf_counter()

        # These two documented result tables are returned by Backtest.run under stable keys.
        # Their underscore-prefixed names are centralized here and version-covered by tests.
        equity_curve = stats["_equity_curve"]
        trade_table = stats["_trades"]
        equity_by_time = {
            _datetime(index): float(row["Equity"]) for index, row in equity_curve.iterrows()
        }
        equity = tuple(
            AdapterEquityPoint(timestamp=timestamp, equity=equity_by_time[timestamp])
            for timestamp in timestamps
        )
        position_size = 0.0
        positions: list[AdapterPositionPoint] = []
        close_by_time = {
            point.timestamp: point.values[symbol]["close"] for point in request.dataset.points
        }
        for timestamp in timestamps:
            position_size = captured_positions.get(timestamp, position_size)
            positions.append(
                AdapterPositionPoint(
                    timestamp=timestamp,
                    quantities={symbol: position_size},
                    market_values={symbol: position_size * close_by_time[timestamp]},
                )
            )

        trades: list[AdapterTradeRecord] = []
        executions: list[AdapterExecutionRecord] = []
        for _, row in trade_table.iterrows():
            trade_number = len(trades) + 1
            opened_at = _datetime(_row_value(row, "EntryTime"))
            raw_closed = _row_value(row, "ExitTime")
            pandas = importlib.import_module("pandas")
            closed_at = None if pandas.isna(raw_closed) else _datetime(raw_closed)
            raw_size = float(_row_value(row, "Size") or 0.0)
            direction = "LONG" if raw_size > 0 else "SHORT"
            entry_price = float(_row_value(row, "EntryPrice", "Avg Entry Price") or 0.0)
            exit_value = _row_value(row, "ExitPrice", "Avg Exit Price")
            exit_price = None if pandas.isna(exit_value) else _number(exit_value)
            commission = _number(_row_value(row, "Commission"))
            trade_id = f"bt-trade-{trade_number:06d}"
            trades.append(
                AdapterTradeRecord(
                    trade_id=trade_id,
                    symbol=symbol,
                    direction=direction,
                    status="OPEN" if closed_at is None else "CLOSED",
                    opened_at=opened_at,
                    closed_at=closed_at,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=abs(raw_size),
                    pnl=_number(_row_value(row, "PnL")),
                    fees=commission,
                )
            )
            entry_side: Literal["BUY", "SELL"] = "BUY" if raw_size > 0 else "SELL"
            executions.append(
                AdapterExecutionRecord(
                    execution_id=f"bt-execution-{trade_number:06d}-entry",
                    source_order_id=f"{trade_id}-entry",
                    symbol=symbol,
                    side=entry_side,
                    quantity=abs(raw_size),
                    price=entry_price,
                    executed_at=opened_at,
                    fee=0.0,
                    meaning="backtesting.py framework trade entry",
                )
            )
            if closed_at is not None and exit_price is not None:
                executions.append(
                    AdapterExecutionRecord(
                        execution_id=f"bt-execution-{trade_number:06d}-exit",
                        source_order_id=f"{trade_id}-exit",
                        symbol=symbol,
                        side="SELL" if raw_size > 0 else "BUY",
                        quantity=abs(raw_size),
                        price=exit_price,
                        executed_at=closed_at,
                        fee=commission or 0.0,
                        meaning="backtesting.py framework trade exit",
                    )
                )

        framework_metrics: dict[str, str | int | float | bool | None] = {}
        for source, target in (
            ("Return [%]", "return_percent"),
            ("Sharpe Ratio", "sharpe_ratio"),
            ("Max. Drawdown [%]", "max_drawdown_percent"),
            ("# Trades", "trade_count"),
            ("Equity Final [$]", "final_equity"),
        ):
            value = _number(stats.get(source))
            if value is not None:
                framework_metrics[target] = value
        capabilities = TraceCapabilitySet(
            market_timeline="AVAILABLE",
            feature_values="AVAILABLE" if captured_features else "UNAVAILABLE",
            feature_lineage="UNAVAILABLE",
            decision_events="PARTIAL" if captured_orders else "UNAVAILABLE",
            decision_conditions="UNAVAILABLE",
            data_dependencies="UNAVAILABLE",
            orders="PARTIAL" if captured_orders else "UNAVAILABLE",
            executions="PARTIAL" if executions else "UNAVAILABLE",
            positions="AVAILABLE",
            trades="AVAILABLE",
            equity="AVAILABLE",
            pnl="AVAILABLE",
            point_in_time_proven="UNAVAILABLE",
            gross_pnl="PARTIAL",
            fees="PARTIAL" if any(item.fees is not None for item in trades) else "UNAVAILABLE",
            slippage="UNAVAILABLE",
            trade_attribution="AVAILABLE",
            drawdowns="AVAILABLE",
        )
        warnings = (
            "Point-in-time provenance is not proven: backtesting.py Strategy.init can access "
            "the full dataset.",
            "Indicator values use the framework's centralized _indicators collection; full "
            "feature lineage and decision conditions are not available.",
            "Trade and equity normalization uses Backtest.run result tables _trades and "
            "_equity_curve; combined commission is attributed at trade exit.",
        )
        initial_equity = _number(supported_config["cash"])
        if initial_equity is None:
            raise ValueError("backtesting.py cash configuration must be numeric")
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
            execution_semantics=supported_config,
            initial_equity=initial_equity,
            market_timeline=request.dataset.points,
            features=tuple(captured_features),
            orders=tuple(captured_orders),
            executions=tuple(executions),
            positions=tuple(positions),
            trades=tuple(trades),
            equity=equity,
            framework_metrics=framework_metrics,
            capabilities=capabilities,
            fidelity=derive_trace_fidelity(capabilities),
            warnings=warnings,
            determinism="SEEDED" if request.manifest.random_seed is not None else "UNVERIFIED",
            random_seed=request.manifest.random_seed,
            adapter_runtime_seconds=framework_seconds,
            normalization_seconds=time.perf_counter() - normalization_started,
        )
