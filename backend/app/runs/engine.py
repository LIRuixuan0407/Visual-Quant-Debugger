from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.backtest import BacktestParameters, run_backtest
from app.corporate_actions.models import CorporateAction, PriceAdjustmentPolicy
from app.datasets import DatasetRegistry
from app.fundamentals import FundamentalRepository
from app.models import BacktestMetrics, BacktestResult, MarketBar, MarketFrame
from app.sdk.loader import LoadedStrategy
from app.sdk.models import ParameterValue, RuntimeFailure, RuntimeRow
from app.sdk.registry import StrategyRegistry
from app.sdk.runtime import StrategyRuntime
from app.sdk.tracing import (
    RuntimeTraceConfiguration,
    build_runtime_trace,
    calculate_runtime_metrics,
)
from app.strategies import PairsTradingParameters, PairsTradingStrategy
from app.trace.models import BacktestTrace, TraceScalar
from app.universes import HistoricalUniverse


@dataclass(frozen=True, slots=True)
class OpenRunResult:
    status: str
    trace: BacktestTrace | None
    metrics: BacktestMetrics | None
    rows: tuple[RuntimeRow, ...]
    failure: RuntimeFailure | None
    unfilled_signal_count: int
    strategy_version: str
    strategy_fingerprint: str
    dataset_fingerprint: str
    frames: tuple[MarketFrame, ...]
    legacy_result: BacktestResult | None = None


def _selected_symbols(
    frames: tuple[MarketFrame, ...], exact: tuple[str, ...], count: int | None
) -> tuple[str, ...]:
    available = tuple(sorted(set.intersection(*(set(frame.symbols) for frame in frames))))
    if exact:
        missing = sorted(set(exact) - set(available))
        if missing:
            raise ValueError(f"Dataset is missing required symbols: {', '.join(missing)}")
        return exact
    if count is not None:
        if len(available) < count:
            raise ValueError(
                f"Strategy requires {count} symbols; dataset provides {len(available)}"
            )
        return available[:count]
    return available


def _pair_bars(frames: tuple[MarketFrame, ...]) -> tuple[MarketBar, ...]:
    return tuple(
        MarketBar(
            timestamp=frame.timestamp,
            asset_a=frame.value("ASSET_A"),
            asset_b=frame.value("ASSET_B"),
        )
        for frame in frames
    )


def execute_open_run(
    *,
    strategy_id: str,
    dataset_id: str,
    parameters: dict[str, ParameterValue],
    research_cutoff: datetime | None = None,
    additional_execution_delay_bars: int = 0,
    strategy_registry: StrategyRegistry,
    dataset_registry: DatasetRegistry,
    loaded_strategy: LoadedStrategy | None = None,
    frames_override: tuple[MarketFrame, ...] | None = None,
    corporate_actions: tuple[CorporateAction, ...] = (),
    price_adjustment_policy: PriceAdjustmentPolicy = "RAW",
    historical_universe: HistoricalUniverse | None = None,
) -> OpenRunResult:
    loaded = loaded_strategy or strategy_registry.load(strategy_id)
    strategy = loaded.strategy_class()
    if strategy.metadata.strategy_id != strategy_id:
        raise ValueError(
            f"Loaded strategy id '{strategy.metadata.strategy_id}' does not match '{strategy_id}'"
        )
    definition = dataset_registry.get(dataset_id)
    if definition is None:
        raise KeyError(f"Dataset '{dataset_id}' was not found")
    requirements = strategy.metadata.data_requirements
    all_frames = frames_override or dataset_registry.load_frames(dataset_id)
    symbols = _selected_symbols(all_frames, requirements.symbols, requirements.symbol_count)
    frames = (
        tuple(
            MarketFrame(
                timestamp=frame.timestamp,
                values={symbol: frame.values[symbol] for symbol in symbols},
                available_at=frame.available_at,
            )
            for frame in all_frames
        )
        if frames_override is not None
        else dataset_registry.load_frames(dataset_id, symbols)
    )
    if research_cutoff is not None:
        if research_cutoff.tzinfo is None or research_cutoff.utcoffset() is None:
            raise ValueError("research_cutoff must be timezone-aware")
        frames = tuple(frame for frame in frames if frame.timestamp <= research_cutoff)
    if len(frames) < requirements.minimum_history:
        raise ValueError(
            f"Strategy requires at least {requirements.minimum_history} synchronized bars; "
            f"dataset segment provides {len(frames)}"
        )
    fee_bps = float(parameters.get("fee_bps", 5.0))
    slippage_bps = float(parameters.get("slippage_bps", 5.0))
    initial_cash = float(parameters.get("initial_cash", 100_000.0))
    strategy_parameters = {
        item.name: parameters.get(item.name, item.default)
        for item in strategy.parameter_definitions()
    }
    if strategy_id == PairsTradingStrategy.metadata.strategy_id:
        if corporate_actions:
            raise ValueError(
                "Corporate Actions require a native open strategy runtime; "
                "the legacy pairs runtime cannot record these events"
            )
        lookback = int(strategy_parameters["lookback"])
        entry_z = float(strategy_parameters["entry_z"])
        exit_z = float(strategy_parameters["exit_z"])
        if exit_z >= entry_z:
            raise ValueError("exit_z must be smaller than entry_z")
        if len(frames) < lookback * 2 - 1:
            raise ValueError(
                f"Strategy requires at least {lookback * 2 - 1} synchronized bars for "
                f"lookback {lookback}; dataset segment provides {len(frames)}"
            )
        backtest_parameters = BacktestParameters(
            strategy=PairsTradingParameters(lookback=lookback, entry_z=entry_z, exit_z=exit_z),
            initial_cash=initial_cash,
            gross_target=float(parameters.get("gross_target", 20_000.0)),
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            additional_execution_delay_bars=additional_execution_delay_bars,
        )
        result = run_backtest(_pair_bars(frames), backtest_parameters)
        legacy_trace = result.trace.model_copy(
            update={
                "metadata": result.trace.metadata.model_copy(
                    update={
                        "dataset_id": dataset_id,
                        "dataset_name": definition.name,
                    }
                )
            }
        )
        return OpenRunResult(
            status="COMPLETED",
            trace=legacy_trace,
            metrics=result.metrics,
            rows=(),
            failure=None,
            unfilled_signal_count=result.unfilled_signal_count,
            strategy_version=strategy.metadata.version,
            strategy_fingerprint=loaded.source_fingerprint,
            dataset_fingerprint=definition.content_fingerprint,
            frames=frames,
            legacy_result=result,
        )
    runtime = StrategyRuntime(
        strategy=strategy,
        parameters=strategy_parameters,
        initial_cash=initial_cash,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        additional_execution_delay_bars=additional_execution_delay_bars,
        fundamental_repository=FundamentalRepository(dataset_registry.workspace_root),
        corporate_actions=corporate_actions,
        price_adjustment_policy=price_adjustment_policy,
        historical_universe=historical_universe,
    )
    runtime_result = runtime.run(frames)
    trace_parameters: dict[str, TraceScalar] = {
        **strategy_parameters,
        "initial_cash": initial_cash,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
    }
    runtime_trace = (
        build_runtime_trace(
            runtime_result.rows,
            RuntimeTraceConfiguration(
                dataset_id=dataset_id,
                dataset_name=definition.name,
                strategy_id=strategy_id,
                strategy_name=strategy.metadata.name,
                parameters=trace_parameters,
                initial_cash=initial_cash,
                execution_model=(
                    "signal at close(t); execute at close(t+1)"
                    if additional_execution_delay_bars == 0
                    else "signal at close(t); execute at close(t+"
                    f"{1 + additional_execution_delay_bars})"
                ),
                corporate_action_events=tuple(runtime.corporate_action_events),
            ),
        )
        if runtime_result.rows
        else None
    )
    metrics = (
        calculate_runtime_metrics(runtime_result.rows, initial_cash)
        if runtime_result.rows
        else None
    )
    return OpenRunResult(
        status=runtime_result.status,
        trace=runtime_trace,
        metrics=metrics,
        rows=runtime_result.rows,
        failure=runtime_result.failure,
        unfilled_signal_count=runtime_result.unfilled_signal_count,
        strategy_version=strategy.metadata.version,
        strategy_fingerprint=loaded.source_fingerprint,
        dataset_fingerprint=definition.content_fingerprint,
        frames=frames,
    )
