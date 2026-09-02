from typing import Literal, cast

import numpy as np

from app.datasets import dataset_registry
from app.diagnostics.fingerprint import build_failure_fingerprint
from app.diagnostics.metrics import (
    daily_returns,
    max_drawdown,
    sharpe,
)
from app.diagnostics.models import (
    CostStressPoint,
    DiagnosisObservation,
    DiagnosisReport,
    DiagnosisSourceRun,
    DiagnosticMetrics,
    DiagnosticSupportSet,
    ExecutionDelayPoint,
    LookbackSensitivityPoint,
    TrainTestSplit,
    WhatIfInputs,
    WhatIfMetricDeltas,
    WhatIfMetrics,
    WhatIfParameterControl,
    WhatIfScenario,
    WhatIfSupport,
)
from app.diagnostics.regime import build_regime_diagnostics
from app.diagnostics.statistical import build_statistical_diagnostics
from app.diagnostics.volatility import build_volatility_diagnostics
from app.runs import BacktestRunRecord, OpenRunResult, execute_open_run
from app.sdk.loader import load_strategy
from app.sdk.registry import strategy_registry
from app.strategies.definition import get_strategy_definition
from app.trace.models import BacktestTrace


def _dataset_frequency(dataset_id: str) -> str | None:
    definition = dataset_registry.get(dataset_id)
    return None if definition is None else definition.frequency


def _lookback_candidates(train_bar_count: int, current: int) -> tuple[int, ...]:
    maximum = max(2, (train_bar_count + 1) // 2)
    values = {round(2 + index * (maximum - 2) / 6) for index in range(7)}
    if 2 <= current <= maximum:
        values.add(current)
    return tuple(sorted(values))


def _cost_split(
    total_bps: float, original_fee: float, original_slippage: float
) -> tuple[float, float]:
    original_total = original_fee + original_slippage
    fee_ratio = original_fee / original_total if original_total > 0 else 0.5
    fee = total_bps * fee_ratio
    return fee, total_bps - fee


def _observations(
    train: DiagnosticMetrics,
    test: DiagnosticMetrics,
    costs: tuple[CostStressPoint, ...],
    delays: tuple[ExecutionDelayPoint, ...],
) -> tuple[DiagnosisObservation, ...]:
    observations: list[DiagnosisObservation] = []
    if test.status == "NO_TRADES":
        observations.append(
            DiagnosisObservation(
                observation_id="observation-test-no-trades",
                title="The test window contains no new trade entries",
                detail=(
                    "This sample cannot establish out-of-sample trading behavior for the "
                    "active parameters."
                ),
                evidence=f"Test trade count: {test.trade_count}; test bars: {test.bar_count}.",
            )
        )
    elif train.status == "OK" and test.status == "OK" and test.sharpe < train.sharpe:
        observations.append(
            DiagnosisObservation(
                observation_id="observation-test-sharpe-lower",
                title="Test Sharpe is lower than train Sharpe",
                detail=(
                    "The difference is descriptive for this single chronological split and "
                    "is not an overfitting claim."
                ),
                evidence=f"Train {train.sharpe:.3f}; test {test.sharpe:.3f}.",
            )
        )
    if costs and costs[-1].metrics.total_return < costs[0].metrics.total_return:
        observations.append(
            DiagnosisObservation(
                observation_id="observation-cost-drag",
                title="Higher modeled friction reduces the recorded return",
                detail=(
                    "Each stress point is a full engine rerun with the same bars and strategy "
                    "parameters."
                ),
                evidence=(
                    f"0 bps return {costs[0].metrics.total_return:.4%}; "
                    f"20 bps return {costs[-1].metrics.total_return:.4%}."
                ),
            )
        )
    baseline = delays[0]
    delayed = delays[-1]
    observations.append(
        DiagnosisObservation(
            observation_id="observation-delay",
            title="Execution timing changes are measured, not inferred",
            detail=(
                "Delay scenarios rerun order generation, fills, costs, positions, and "
                "mark-to-market accounting."
            ),
            evidence=(
                f"t+1 return {baseline.metrics.total_return:.4%}; "
                f"t+3 return {delayed.metrics.total_return:.4%}; "
                f"t+3 unfilled signals {delayed.unfilled_signal_count}."
            ),
        )
    )
    return tuple(observations)


def _trace_window_metrics(
    trace: BacktestTrace, initial_cash: float, start_index: int, end_index: int
) -> DiagnosticMetrics:
    events = trace.timeline[start_index:end_index]
    window_initial = (
        initial_cash if start_index == 0 else trace.timeline[start_index - 1].pnl_snapshot.equity
    )
    equity = tuple(event.pnl_snapshot.equity for event in events)
    returns = daily_returns(equity, window_initial)
    trade_count = sum(
        events[0].timestamp <= trade.opened_at <= events[-1].timestamp for trade in trace.trades
    )
    traded_notional = sum(
        execution.traded_notional for event in events for execution in event.execution_events
    )
    average_equity = float(np.mean(np.asarray(equity, dtype=np.float64)))
    status: Literal["OK", "INSUFFICIENT_DATA", "NO_TRADES", "UNDEFINED_SHARPE"]
    note = None
    if len(events) < 2:
        status = "INSUFFICIENT_DATA"
        note = "Fewer than two bars are available in this window."
    elif trade_count == 0:
        status = "NO_TRADES"
        note = "No trade entry was executed in this window."
    elif float(np.std(returns, ddof=1)) == 0:
        status = "UNDEFINED_SHARPE"
        note = "Return variance is zero; Sharpe is reported as 0."
    else:
        status = "OK"
    return DiagnosticMetrics(
        status=status,
        total_return=events[-1].pnl_snapshot.equity / window_initial - 1.0,
        sharpe=sharpe(returns),
        max_drawdown=max_drawdown(equity, window_initial),
        turnover=traded_notional / average_equity if average_equity > 0 else 0.0,
        trade_count=trade_count,
        final_equity=events[-1].pnl_snapshot.equity,
        bar_count=len(events),
        note=note,
    )


def _native_rerun(
    record: BacktestRunRecord,
    *,
    parameter_updates: dict[str, int | float] | None = None,
    additional_delay: int = 0,
) -> OpenRunResult:
    parameters = {**(record.parameter_values or {}), **(parameter_updates or {})}
    loaded = (
        None
        if record.strategy_source_path is None
        else load_strategy(record.strategy_source_path, record.strategy_class_name)
    )
    return execute_open_run(
        strategy_id=record.strategy_id,
        dataset_id=record.dataset_id,
        parameters=parameters,
        research_cutoff=record.research_cutoff,
        additional_execution_delay_bars=additional_delay,
        strategy_registry=strategy_registry,
        dataset_registry=dataset_registry,
        loaded_strategy=loaded,
    )


def _native_trace(result: OpenRunResult) -> BacktestTrace:
    if result.trace is None:
        failure = result.failure
        detail = "before a trace was produced" if failure is None else failure.message
        raise ValueError(f"Diagnostic rerun failed {detail}")
    return result.trace


def _what_if_metrics(trace: BacktestTrace, initial_cash: float) -> WhatIfMetrics:
    metrics = _trace_window_metrics(trace, initial_cash, 0, len(trace.timeline))
    return WhatIfMetrics(
        total_return=metrics.total_return,
        sharpe=metrics.sharpe,
        max_drawdown=metrics.max_drawdown,
        turnover=metrics.turnover,
        trade_count=metrics.trade_count,
        net_pnl=metrics.final_equity - initial_cash,
    )


def _what_if_parameter(record: BacktestRunRecord) -> WhatIfParameterControl | None:
    definition = get_strategy_definition(record.strategy_id)
    if definition is None:
        return None
    key = definition.diagnostic_capabilities.parameter_sensitivity
    parameter = next((item for item in definition.parameters if item.key == key), None)
    if key is None or parameter is None:
        return None
    current = (record.parameter_values or {}).get(key, parameter.default_value)
    return WhatIfParameterControl(
        key=parameter.key,
        label=parameter.label,
        value_type=parameter.value_type,
        current_value=current,
        minimum=parameter.minimum,
        maximum=parameter.maximum,
        step=parameter.step,
        unit=parameter.unit,
    )


def _what_if_support(record: BacktestRunRecord) -> WhatIfSupport:
    initial_cash = float((record.parameter_values or {}).get("initial_cash", 100_000.0))
    parameter = _what_if_parameter(record)
    baseline_inputs = WhatIfInputs(
        fee_bps=float((record.parameter_values or {}).get("fee_bps", 5.0)),
        slippage_bps=float((record.parameter_values or {}).get("slippage_bps", 5.0)),
        additional_execution_delay_bars=0,
        strategy_parameters=({} if parameter is None else {parameter.key: parameter.current_value}),
    )
    return WhatIfSupport(
        status="AVAILABLE",
        baseline_inputs=baseline_inputs,
        baseline_metrics=_what_if_metrics(record.trace, initial_cash),
        parameter=parameter,
        calculation_details=(
            "Each scenario is a full deterministic rerun on the source run's recorded "
            "dataset and strategy revision.",
            "Fees and slippage are applied to each executed side; execution delay never "
            "forces an end-of-data fill.",
            "Baseline inputs remain the immutable assumptions recorded on the source run.",
        ),
    )


def run_what_if_scenario(record: BacktestRunRecord, inputs: WhatIfInputs) -> WhatIfScenario:
    support = _what_if_support(record)
    baseline_inputs = support.baseline_inputs
    baseline_metrics = support.baseline_metrics
    if baseline_inputs is None or baseline_metrics is None:
        raise ValueError("What-if is not available for this run")
    parameter = support.parameter
    allowed = set() if parameter is None else {parameter.key}
    unexpected = sorted(set(inputs.strategy_parameters) - allowed)
    if unexpected:
        raise ValueError(f"Unsupported What-if strategy parameters: {', '.join(unexpected)}")
    strategy_parameters = dict(baseline_inputs.strategy_parameters)
    strategy_parameters.update(inputs.strategy_parameters)
    if parameter is not None:
        value = strategy_parameters[parameter.key]
        if parameter.value_type == "integer" and int(value) != value:
            raise ValueError(f"What-if parameter '{parameter.key}' must be an integer")
        if value < parameter.minimum:
            raise ValueError(
                f"What-if parameter '{parameter.key}' must be at least {parameter.minimum}"
            )
        if parameter.maximum is not None and value > parameter.maximum:
            raise ValueError(
                f"What-if parameter '{parameter.key}' must be at most {parameter.maximum}"
            )
    effective_inputs = WhatIfInputs(
        fee_bps=inputs.fee_bps,
        slippage_bps=inputs.slippage_bps,
        additional_execution_delay_bars=inputs.additional_execution_delay_bars,
        strategy_parameters=strategy_parameters,
    )
    rerun_result = _native_rerun(
        record,
        parameter_updates={
            "fee_bps": effective_inputs.fee_bps,
            "slippage_bps": effective_inputs.slippage_bps,
            **effective_inputs.strategy_parameters,
        },
        additional_delay=effective_inputs.additional_execution_delay_bars,
    )
    stressed = _what_if_metrics(
        _native_trace(rerun_result),
        float((record.parameter_values or {}).get("initial_cash", 100_000.0)),
    )
    deltas = WhatIfMetricDeltas(
        total_return=stressed.total_return - baseline_metrics.total_return,
        sharpe=stressed.sharpe - baseline_metrics.sharpe,
        max_drawdown=stressed.max_drawdown - baseline_metrics.max_drawdown,
        turnover=stressed.turnover - baseline_metrics.turnover,
        trade_count=stressed.trade_count - baseline_metrics.trade_count,
        net_pnl=stressed.net_pnl - baseline_metrics.net_pnl,
    )
    verdict: Literal["LOWER_NET_PNL", "HIGHER_NET_PNL", "UNCHANGED_NET_PNL"]
    if deltas.net_pnl < 0:
        verdict = "LOWER_NET_PNL"
    elif deltas.net_pnl > 0:
        verdict = "HIGHER_NET_PNL"
    else:
        verdict = "UNCHANGED_NET_PNL"
    return WhatIfScenario(
        baseline_inputs=baseline_inputs,
        inputs=effective_inputs,
        baseline_metrics=baseline_metrics,
        stressed_metrics=stressed,
        deltas=deltas,
        unfilled_signal_count=rerun_result.unfilled_signal_count,
        verdict=verdict,
        evidence=(
            f"Sharpe changes from {baseline_metrics.sharpe:.3f} to {stressed.sharpe:.3f}.",
            f"Net P&L changes from {baseline_metrics.net_pnl:.2f} to {stressed.net_pnl:.2f}.",
            f"Max drawdown changes from {baseline_metrics.max_drawdown:.4%} to "
            f"{stressed.max_drawdown:.4%}.",
        ),
        calculation_details=support.calculation_details,
    )


def _diagnose_native_run(trace_id: str, record: BacktestRunRecord) -> DiagnosisReport:
    baseline = record.trace
    bar_count = len(baseline.timeline)
    split_index = int(bar_count * 0.7)
    if split_index < 2 or bar_count - split_index < 1:
        raise ValueError("A chronological diagnosis requires at least three bars")
    initial_cash = float((record.parameter_values or {}).get("initial_cash", 100_000.0))
    train = _trace_window_metrics(baseline, initial_cash, 0, split_index)
    test = _trace_window_metrics(baseline, initial_cash, split_index, bar_count)
    definition = get_strategy_definition(record.strategy_id)
    sensitivity_parameter = (
        None if definition is None else definition.diagnostic_capabilities.parameter_sensitivity
    )
    sensitivity: list[LookbackSensitivityPoint] = []
    current_value = int((record.parameter_values or {}).get(sensitivity_parameter or "", 0))
    if sensitivity_parameter is not None and current_value > 0:
        for candidate in _lookback_candidates(split_index, current_value):
            rerun = _native_trace(
                _native_rerun(record, parameter_updates={sensitivity_parameter: candidate})
            )
            sensitivity.append(
                LookbackSensitivityPoint(
                    lookback=candidate,
                    is_current=candidate == current_value,
                    train=_trace_window_metrics(rerun, initial_cash, 0, split_index),
                    test=_trace_window_metrics(rerun, initial_cash, split_index, bar_count),
                )
            )
    original_fee = float((record.parameter_values or {}).get("fee_bps", 5.0))
    original_slippage = float((record.parameter_values or {}).get("slippage_bps", 5.0))
    cost_points: list[CostStressPoint] = []
    for total_bps in (0.0, 5.0, 10.0, 15.0, 20.0):
        fee_bps, slippage_bps = _cost_split(total_bps, original_fee, original_slippage)
        rerun = _native_trace(
            _native_rerun(
                record,
                parameter_updates={"fee_bps": fee_bps, "slippage_bps": slippage_bps},
            )
        )
        cost_points.append(
            CostStressPoint(
                total_friction_bps=total_bps,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                metrics=_trace_window_metrics(rerun, initial_cash, 0, bar_count),
            )
        )
    delay_points: list[ExecutionDelayPoint] = []
    for delay in (0, 1, 2):
        result = _native_rerun(record, additional_delay=delay)
        rerun = _native_trace(result)
        delay_points.append(
            ExecutionDelayPoint(
                additional_delay_bars=delay,
                execution_offset_bars=cast(Literal[1, 2, 3], delay + 1),
                unfilled_signal_count=result.unfilled_signal_count,
                metrics=_trace_window_metrics(rerun, initial_cash, 0, bar_count),
            )
        )
    source = DiagnosisSourceRun(
        trace_id=trace_id,
        strategy_id=record.strategy_id,
        dataset_id=record.dataset_id,
        dataset_name=baseline.metadata.dataset_name,
        dataset_source=record.dataset_source,
        bar_count=bar_count,
        current_lookback=current_value,
        fee_bps=original_fee,
        slippage_bps=original_slippage,
        sensitivity_parameter=sensitivity_parameter,
    )
    split = TrainTestSplit(
        train_start=baseline.timeline[0].timestamp,
        train_end=baseline.timeline[split_index - 1].timestamp,
        test_start=baseline.timeline[split_index].timestamp,
        test_end=baseline.timeline[-1].timestamp,
        train_bar_count=split_index,
        test_bar_count=bar_count - split_index,
        feature_context_policy=(
            "Test decisions are produced by one chronological full-run pipeline, so each "
            "test bar may use only earlier bars, including train history."
        ),
        pnl_isolation_policy=(
            "Test metrics start from equity immediately before the first test bar; train "
            "P&L is not counted in test return."
        ),
        train=train,
        test=test,
    )
    costs = tuple(cost_points)
    delays = tuple(delay_points)
    sensitivity_points = tuple(sensitivity)
    statistical = build_statistical_diagnostics(baseline)
    volatility = build_volatility_diagnostics(
        baseline, dataset_frequency=_dataset_frequency(record.dataset_id)
    )
    regime = build_regime_diagnostics(baseline, volatility)
    fingerprint = build_failure_fingerprint(
        source=source,
        train_test=split,
        parameter_sensitivity=sensitivity_points,
        cost_stress=costs,
        execution_delay=delays,
        regime_diagnostics=regime,
        statistical_diagnostics=statistical,
    )
    return DiagnosisReport(
        source_run=source,
        train_test=split,
        lookback_sensitivity=sensitivity_points,
        cost_stress=costs,
        execution_delay=delays,
        observations=_observations(train, test, costs, delays),
        sensitivity_available=sensitivity_parameter is not None,
        statistical_diagnostics=statistical,
        volatility_diagnostics=volatility,
        what_if=_what_if_support(record),
        regime_diagnostics=regime,
        failure_fingerprint=fingerprint,
    )


def diagnose_run(trace_id: str, record: BacktestRunRecord) -> DiagnosisReport:
    return _diagnose_native_run(trace_id, record)


def diagnose_framework_trace(
    trace_id: str, run_id: str, trace: BacktestTrace, dataset_source: str
) -> DiagnosisReport:
    bar_count = len(trace.timeline)
    split_index = int(bar_count * 0.7)
    if split_index < 2 or bar_count - split_index < 1:
        raise ValueError("A chronological diagnosis requires at least three bars")
    first = trace.timeline[0]
    initial_equity = first.pnl_snapshot.equity - first.pnl_snapshot.period_net_pnl
    train = _trace_window_metrics(trace, initial_equity, 0, split_index)
    test = _trace_window_metrics(trace, initial_equity, split_index, bar_count)
    source = DiagnosisSourceRun(
        trace_id=trace_id,
        strategy_id=trace.strategy.strategy_id,
        dataset_id=trace.metadata.dataset_id,
        dataset_name=trace.metadata.dataset_name,
        dataset_source=dataset_source,
        bar_count=bar_count,
        current_lookback=0,
        fee_bps=0.0,
        slippage_bps=0.0,
        sensitivity_parameter=None,
    )
    split = TrainTestSplit(
        train_start=trace.timeline[0].timestamp,
        train_end=trace.timeline[split_index - 1].timestamp,
        test_start=trace.timeline[split_index].timestamp,
        test_end=trace.timeline[-1].timestamp,
        train_bar_count=split_index,
        test_bar_count=bar_count - split_index,
        feature_context_policy=(
            "Descriptive chronological split of the persisted framework equity timeline; "
            "point-in-time strategy provenance is not asserted."
        ),
        pnl_isolation_policy=(
            "Test metrics start from persisted equity immediately before the first test bar."
        ),
        train=train,
        test=test,
    )
    statistical = build_statistical_diagnostics(trace)
    volatility = build_volatility_diagnostics(
        trace, dataset_frequency=_dataset_frequency(trace.metadata.dataset_id)
    )
    regime = build_regime_diagnostics(trace, volatility)
    fingerprint = build_failure_fingerprint(
        source=source,
        train_test=split,
        parameter_sensitivity=(),
        cost_stress=(),
        execution_delay=(),
        regime_diagnostics=regime,
        statistical_diagnostics=statistical,
    )
    return DiagnosisReport(
        source_run=source,
        train_test=split,
        lookback_sensitivity=(),
        cost_stress=(),
        execution_delay=(),
        observations=(
            DiagnosisObservation(
                observation_id=f"framework-diagnosis-{run_id}",
                title="Framework diagnostics are capability-limited",
                detail=(
                    "Train/test uses persisted equity. Parameter sensitivity, cost stress, and "
                    "execution delay require adapter rerun support and are not inferred."
                ),
                evidence=(
                    f"Runtime: {trace.metadata.runtime.framework_name}; "
                    f"Trace Fidelity: {trace.metadata.runtime.trace_fidelity}."
                ),
            ),
        ),
        sensitivity_available=False,
        support=DiagnosticSupportSet(
            train_test="AVAILABLE",
            parameter_sensitivity="NOT_SUPPORTED",
            cost_stress="NOT_SUPPORTED",
            execution_delay="NOT_SUPPORTED",
        ),
        statistical_diagnostics=statistical,
        volatility_diagnostics=volatility,
        what_if=WhatIfSupport(
            status="NOT_SUPPORTED",
            calculation_details=(
                "What-if reruns require a native VQD strategy execution contract.",
            ),
        ),
        regime_diagnostics=regime,
        failure_fingerprint=fingerprint,
    )
