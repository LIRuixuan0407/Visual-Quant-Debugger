"""Native VQD SDK example. Register explicitly; it is not loaded by default."""

from app.sdk import (
    DataRequirements,
    DiagnosticCapabilities,
    StrategyContext,
    StrategyMetadata,
    TargetPortfolioIntent,
    VQDStrategy,
    parameter,
)


class MovingAverageCross(VQDStrategy):
    metadata = StrategyMetadata(
        strategy_id="user.sma-cross",
        name="SMA Cross",
        version="1.0.0",
        description="Traces a fast/slow moving-average crossover into an AAPL target position.",
        data_requirements=DataRequirements(
            required_fields=("close",),
            symbols=("AAPL",),
            symbol_count=1,
            minimum_history=5,
        ),
        diagnostic_capabilities=DiagnosticCapabilities(parameter_sensitivity="slow_window"),
    )
    fast_window = parameter(
        default=3,
        minimum=1,
        maximum=100,
        step=1,
        unit="bars",
        description="Bars used by the responsive moving average.",
    )
    slow_window = parameter(
        default=5,
        minimum=2,
        maximum=250,
        step=1,
        unit="bars",
        description="Bars used by the slower moving average.",
    )
    quantity = parameter(
        default=100.0,
        minimum=1.0,
        maximum=1_000_000.0,
        step=1.0,
        unit="shares",
        description="Long target quantity while the fast average is above the slow average.",
    )

    def initialize(self, context: StrategyContext) -> None:
        self._target = 0.0

    def on_bar(self, context: StrategyContext) -> TargetPortfolioIntent:
        fast_history = context.history(symbol="AAPL", bars=self.fast_window)
        slow_history = context.history(symbol="AAPL", bars=self.slow_window)
        fast_value = (
            sum(fast_history) / self.fast_window if len(fast_history) == self.fast_window else None
        )
        slow_value = (
            sum(slow_history) / self.slow_window if len(slow_history) == self.slow_window else None
        )
        fast = context.feature(
            name="fast_ma",
            value=fast_value,
            inputs=(fast_history,),
            formula="SMA(AAPL.close, fast_window)",
            parameters={"fast_window": self.fast_window},
            window_start=fast_history.timestamps[0] if fast_value is not None else None,
            window_end=context.current_time if fast_value is not None else None,
        )
        slow = context.feature(
            name="slow_ma",
            value=slow_value,
            inputs=(slow_history,),
            formula="SMA(AAPL.close, slow_window)",
            parameters={"slow_window": self.slow_window},
            window_start=slow_history.timestamps[0] if slow_value is not None else None,
            window_end=context.current_time if slow_value is not None else None,
        )
        ready = fast_value is not None and slow_value is not None
        next_target = self.quantity if ready and fast_value > slow_value else 0.0
        condition = context.condition(
            left="fast_ma",
            left_value=fast_value,
            operator=">",
            right="slow_ma",
            right_value=slow_value,
            result=bool(ready and fast_value > slow_value),
            description="Hold AAPL only while the fast average is above the slow average",
        )
        previous_target = self._target
        self._target = next_target
        return context.target_positions(
            {"AAPL": next_target},
            reason=(
                "Waiting for complete moving-average windows"
                if not ready
                else "fast_ma is above slow_ma"
                if next_target
                else "fast_ma is not above slow_ma"
            ),
            dependencies=(fast, slow) if ready else (),
            conditions=(condition,),
            signal="LONG" if next_target else "EXIT" if previous_target else "WARMUP",
            previous_state="LONG" if previous_target else "FLAT",
            next_state="LONG" if next_target else "FLAT",
            transition=next_target != previous_target,
            target_state=1 if next_target else 0,
        )
