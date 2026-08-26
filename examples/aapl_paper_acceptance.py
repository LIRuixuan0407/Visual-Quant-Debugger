"""Minimal Native Strategy for the real Alpaca Paper acceptance run.

The strategy still requires an authentic AAPL market bar. It targets one
share after observing a positive close so the broker path is deterministic.
"""

from app.sdk import (
    DataRequirements,
    StrategyContext,
    StrategyMetadata,
    TargetPortfolioIntent,
    VQDStrategy,
    parameter,
)


class AaplPaperAcceptance(VQDStrategy):
    metadata = StrategyMetadata(
        strategy_id="acceptance.aapl-paper",
        name="AAPL Paper Acceptance",
        version="1.0.0",
        description="Targets one AAPL share to verify the real Alpaca Paper order path.",
        data_requirements=DataRequirements(
            required_fields=("close",),
            symbols=("AAPL",),
            symbol_count=1,
            minimum_history=1,
        ),
    )
    quantity = parameter(
        default=1.0,
        minimum=1.0,
        maximum=1.0,
        step=1.0,
        unit="share",
        description="Fixed one-share target for a minimal Paper Broker acceptance order.",
    )

    def initialize(self, context: StrategyContext) -> None:
        self._target = 0.0

    def on_bar(self, context: StrategyContext) -> TargetPortfolioIntent:
        close = context.current("AAPL")
        observed_close = context.feature(
            name="observed_close",
            value=close.value,
            inputs=(close,),
            formula="AAPL.close",
            window_start=context.current_time,
            window_end=context.current_time,
        )
        valid = close.value > 0
        condition = context.condition(
            left="observed_close",
            left_value=close.value,
            operator=">",
            right="zero",
            right_value=0.0,
            result=valid,
            description="A real positive AAPL close is available",
        )
        previous_target = self._target
        self._target = self.quantity if valid else 0.0
        return context.target_positions(
            {"AAPL": self._target},
            reason="Real AAPL close observed; target the one-share acceptance position",
            dependencies=(observed_close,),
            conditions=(condition,),
            signal="ACCEPTANCE_BUY" if self._target else "WAITING_FOR_REAL_BAR",
            previous_state="LONG" if previous_target else "FLAT",
            next_state="LONG" if self._target else "FLAT",
            transition=self._target != previous_target,
            target_state=1 if self._target else 0,
        )
