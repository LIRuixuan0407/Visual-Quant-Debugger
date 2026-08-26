from dataclasses import dataclass

from app.diagnostics import calculate_metrics
from app.models import BacktestResult, MarketBar
from app.sdk.runtime import StrategyRuntime, StrategyRuntimeError, legacy_timeline
from app.strategies import PairsTradingParameters, PairsTradingStrategy
from app.trace import TraceBuildConfiguration, build_trace


@dataclass(frozen=True, slots=True)
class BacktestParameters:
    strategy: PairsTradingParameters = PairsTradingParameters()
    initial_cash: float = 100_000.0
    gross_target: float = 20_000.0
    fee_bps: float = 5.0
    slippage_bps: float = 5.0
    additional_execution_delay_bars: int = 0

    def __post_init__(self) -> None:
        if self.initial_cash <= 0 or self.gross_target <= 0:
            raise ValueError("initial_cash and gross_target must be positive")
        if self.fee_bps < 0 or self.slippage_bps < 0:
            raise ValueError("fee_bps and slippage_bps must be non-negative")
        if self.additional_execution_delay_bars < 0:
            raise ValueError("additional_execution_delay_bars must be non-negative")


def run_backtest(
    bars: tuple[MarketBar, ...], parameters: BacktestParameters | None = None
) -> BacktestResult:
    config = parameters or BacktestParameters()
    if len(bars) < 3:
        raise ValueError("At least three market bars are required")

    strategy = PairsTradingStrategy(gross_target=config.gross_target)
    runtime = StrategyRuntime(
        strategy=strategy,
        parameters={
            "lookback": config.strategy.lookback,
            "entry_z": config.strategy.entry_z,
            "exit_z": config.strategy.exit_z,
        },
        initial_cash=config.initial_cash,
        fee_bps=config.fee_bps,
        slippage_bps=config.slippage_bps,
        additional_execution_delay_bars=config.additional_execution_delay_bars,
    )
    runtime_result = runtime.run(tuple(bar.as_frame() for bar in bars))
    if runtime_result.failure is not None:
        raise StrategyRuntimeError(runtime_result.failure)
    rows = legacy_timeline(runtime_result.rows)
    metrics = calculate_metrics(rows, config.initial_cash)
    trace = build_trace(
        bars,
        rows,
        metrics,
        TraceBuildConfiguration(
            dataset_name="in-memory-bars",
            strategy_id="pairs-trading",
            strategy_name="Pairs Trading",
            parameters={
                "lookback": config.strategy.lookback,
                "entry_z": config.strategy.entry_z,
                "exit_z": config.strategy.exit_z,
                "initial_cash": config.initial_cash,
                "gross_target": config.gross_target,
                "fee_bps": config.fee_bps,
                "slippage_bps": config.slippage_bps,
            },
            lookback=config.strategy.lookback,
            initial_cash=config.initial_cash,
            execution_model=(
                "signal at close(t); execute at close(t+1)"
                if config.additional_execution_delay_bars == 0
                else (
                    "signal at close(t); execute at close(t+"
                    f"{1 + config.additional_execution_delay_bars})"
                )
            ),
        ),
    )
    return BacktestResult(
        timeline=rows,
        metrics=metrics,
        trace=trace,
        unfilled_signal_count=runtime_result.unfilled_signal_count,
    )
