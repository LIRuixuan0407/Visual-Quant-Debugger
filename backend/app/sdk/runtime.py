from __future__ import annotations

import traceback as traceback_module
from dataclasses import dataclass
from typing import Literal, cast

from app.corporate_actions.models import (
    CorporateAction,
    CorporateActionEvent,
    PriceAdjustmentPolicy,
)
from app.corporate_actions.service import CorporateActionService
from app.execution import ExecutionEngine
from app.fundamentals import FundamentalRepository
from app.models import (
    Execution,
    FeaturePoint,
    MarketFrame,
    SignalDecision,
    TimelineRow,
)
from app.portfolio import Portfolio
from app.sdk.context import StrategyContext
from app.sdk.models import (
    ParameterValue,
    RuntimeDecision,
    RuntimeFailure,
    RuntimeResult,
    RuntimeRow,
    TargetPortfolioIntent,
)
from app.sdk.strategy import VQDStrategy
from app.universes import HistoricalUniverse


@dataclass(frozen=True, slots=True)
class _PendingIntent:
    due_index: int
    signal_id: str
    intent: TargetPortfolioIntent


class StrategyRuntimeError(RuntimeError):
    def __init__(self, failure: RuntimeFailure) -> None:
        super().__init__(
            f"{failure.strategy_id} failed at {failure.timestamp.isoformat()} "
            f"(event {failure.event_index}): {failure.exception_type}: {failure.message}"
        )
        self.failure = failure


class StrategyRuntime:
    """The single incremental runtime used by batch and forward execution."""

    def __init__(
        self,
        *,
        strategy: VQDStrategy,
        parameters: dict[str, ParameterValue],
        initial_cash: float = 100_000.0,
        fee_bps: float = 5.0,
        slippage_bps: float = 5.0,
        additional_execution_delay_bars: int = 0,
        execution_mode: Literal["simulated", "external"] = "simulated",
        fundamental_repository: FundamentalRepository | None = None,
        corporate_actions: tuple[CorporateAction, ...] = (),
        price_adjustment_policy: PriceAdjustmentPolicy = "RAW",
        historical_universe: HistoricalUniverse | None = None,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if additional_execution_delay_bars < 0:
            raise ValueError("additional_execution_delay_bars must be non-negative")
        self.strategy = strategy
        self.parameters = dict(parameters)
        self.strategy.configure(self.parameters)
        self.portfolio = Portfolio(initial_cash)
        self.initial_cash = initial_cash
        self.execution_engine = ExecutionEngine(fee_bps, slippage_bps)
        self.additional_execution_delay_bars = additional_execution_delay_bars
        self.execution_mode = execution_mode
        self.fundamental_repository = fundamental_repository or FundamentalRepository()
        self.visible_frames: list[MarketFrame] = []
        self.rows: list[RuntimeRow] = []
        self.pending: list[_PendingIntent] = []
        self.unfilled_signal_count = 0
        self.failure: RuntimeFailure | None = None
        self._initialized = False
        self._signal_counter = 0
        self._feature_counter = 0
        self._dependency_counter = 0
        self._previous_target_signature: tuple[tuple[str, float], ...] = ()
        self.external_executions: list[Execution] = []
        self.corporate_actions = tuple(
            sorted(corporate_actions, key=lambda item: (item.effective_at, item.action_id))
        )
        self.price_adjustment_policy = price_adjustment_policy
        self.historical_universe = historical_universe
        self.corporate_action_events: list[CorporateActionEvent] = []
        self._processed_corporate_action_ids: set[str] = set()

    def _apply_due_corporate_actions(self, frame: MarketFrame) -> None:
        for action in self.corporate_actions:
            if (
                action.action_id in self._processed_corporate_action_ids
                or action.effective_at > frame.knowledge_time
            ):
                continue
            event = CorporateActionService.apply_action(
                self.portfolio,
                action,
                self.price_adjustment_policy,
            )
            self.corporate_action_events.append(event)
            self._processed_corporate_action_ids.add(action.action_id)

    def _next_feature_id(self) -> str:
        self._feature_counter += 1
        return f"feature-{self._feature_counter:06d}"

    def _next_dependency_id(self) -> str:
        self._dependency_counter += 1
        return f"dependency-{self._dependency_counter:06d}"

    @staticmethod
    def _close_prices(frame: MarketFrame) -> dict[str, float]:
        return {
            symbol: fields["close"] for symbol, fields in frame.values.items() if "close" in fields
        }

    @staticmethod
    def _signature(intent: TargetPortfolioIntent) -> tuple[tuple[str, float], ...]:
        values = intent.target_positions or intent.target_weights
        return tuple(sorted((symbol, float(value)) for symbol, value in values.items()))

    def _context(self) -> StrategyContext:
        return StrategyContext(
            frames=tuple(self.visible_frames),
            parameters=self.parameters,
            current_positions=self.portfolio.positions,
            previous_target_signature=self._previous_target_signature,
            next_feature_id=self._next_feature_id,
            next_dependency_id=self._next_dependency_id,
            fundamental_repository=self.fundamental_repository,
            active_symbols=(
                None
                if self.historical_universe is None
                else self.historical_universe.symbols_at(self.visible_frames[-1].knowledge_time)
            ),
        )

    def _execute_due(
        self, index: int, frame: MarketFrame
    ) -> tuple[tuple[object, ...], tuple[object, ...]]:
        from app.models import Order

        orders: list[Order] = []
        executions: list[Execution] = []
        prices = self._close_prices(frame)
        remaining: list[_PendingIntent] = []
        for pending in self.pending:
            if pending.due_index != index:
                remaining.append(pending)
                continue
            intent = pending.intent
            if intent.target_positions:
                targets = dict(intent.target_positions)
            else:
                if intent.gross_notional is None:
                    raise RuntimeError("A target-weight intent requires gross_notional")
                targets = self.execution_engine.resolve_gross_weights(
                    intent.target_weights, intent.gross_notional, prices
                )
            created_orders = self.execution_engine.create_target_orders(
                current_positions=self.portfolio.positions,
                target_positions=targets,
                submitted_at=frame.knowledge_time,
                source_signal_id=pending.signal_id,
                target_state=intent.target_state,
            )
            created_executions = (
                self.execution_engine.execute_at_prices(
                    created_orders, prices=prices, executed_at=frame.knowledge_time
                )
                if self.execution_mode == "simulated"
                else ()
            )
            if created_executions:
                self.portfolio.apply(created_executions)
            orders.extend(created_orders)
            executions.extend(created_executions)
        self.pending = remaining
        return tuple(orders), tuple(executions)

    def apply_external_execution(self, execution: Execution) -> None:
        """Apply one broker-confirmed fill without inventing a local execution."""
        if self.execution_mode != "external":
            raise RuntimeError("External fills require the external execution mode")
        self.portfolio.apply((execution,))
        self.external_executions.append(execution)

    def step(self, frame: MarketFrame, *, total_bars: int | None = None) -> RuntimeRow:
        index = len(self.visible_frames)
        self.visible_frames.append(frame)
        try:
            self._apply_due_corporate_actions(frame)
            orders_raw, executions_raw = self._execute_due(index, frame)
            from app.models import Execution, Order

            orders = cast(tuple[Order, ...], orders_raw)
            executions = cast(tuple[Execution, ...], executions_raw)
            context = self._context()
            if not self._initialized:
                self.strategy.initialize(context)
                self._initialized = True
            intent = self.strategy.on_bar(context)
            if intent is None:
                intent = context.target_positions(
                    self.portfolio.positions,
                    reason="Strategy emitted no target transition",
                    signal="HOLD",
                    transition=False,
                    previous_state="CURRENT",
                    next_state="CURRENT",
                )
            self._previous_target_signature = self._signature(intent)
            signal_id = None
            if intent.transition:
                self._signal_counter += 1
                signal_id = f"signal-{self._signal_counter:04d}"
                due_index = index + 1 + self.additional_execution_delay_bars
                if total_bars is not None and due_index >= total_bars:
                    self.unfilled_signal_count += 1
                else:
                    self.pending.append(_PendingIntent(due_index, signal_id, intent))
            decision = RuntimeDecision(
                signal_id=signal_id, intent=intent, decided_at=frame.knowledge_time
            )
            snapshot = self.portfolio.mark_prices(self._close_prices(frame))
            row = RuntimeRow(
                index=index,
                timestamp=frame.knowledge_time,
                market=frame.values,
                features=context.features,
                decision=decision,
                orders=orders,
                executions=executions,
                portfolio=snapshot,
                data_dependencies=context.data_dependencies,
            )
            self.rows.append(row)
            return row
        except Exception as exc:
            failure = RuntimeFailure(
                strategy_id=self.strategy.metadata.strategy_id,
                timestamp=frame.knowledge_time,
                event_index=index,
                exception_type=type(exc).__name__,
                message=str(exc),
                traceback="".join(
                    traceback_module.format_exception(type(exc), exc, exc.__traceback__)
                ),
            )
            self.failure = failure
            raise StrategyRuntimeError(failure) from exc

    def step_without_strategy(
        self, frame: MarketFrame, *, signal: str = "EVALUATION_SKIPPED_PAUSED"
    ) -> RuntimeRow:
        """Advance real market time and due executions without calling strategy.on_bar."""
        index = len(self.visible_frames)
        self.visible_frames.append(frame)
        try:
            self._apply_due_corporate_actions(frame)
            orders_raw, executions_raw = self._execute_due(index, frame)
            from app.models import Execution, Order

            orders = cast(tuple[Order, ...], orders_raw)
            executions = cast(tuple[Execution, ...], executions_raw)
            context = self._context()
            intent = context.target_positions(
                self.portfolio.positions,
                reason="Strategy evaluation skipped while live paper session was paused",
                signal=signal,
                transition=False,
                previous_state="CURRENT",
                next_state="CURRENT",
            )
            snapshot = self.portfolio.mark_prices(self._close_prices(frame))
            row = RuntimeRow(
                index=index,
                timestamp=frame.knowledge_time,
                market=frame.values,
                features=(),
                decision=RuntimeDecision(
                    signal_id=None, intent=intent, decided_at=frame.knowledge_time
                ),
                orders=orders,
                executions=executions,
                portfolio=snapshot,
                data_dependencies=context.data_dependencies,
            )
            self.rows.append(row)
            return row
        except Exception as exc:
            failure = RuntimeFailure(
                strategy_id=self.strategy.metadata.strategy_id,
                timestamp=frame.knowledge_time,
                event_index=index,
                exception_type=type(exc).__name__,
                message=str(exc),
                traceback="".join(
                    traceback_module.format_exception(type(exc), exc, exc.__traceback__)
                ),
            )
            self.failure = failure
            raise StrategyRuntimeError(failure) from exc

    def revise_visible_frame(self, frame: MarketFrame) -> bool:
        """Update future strategy history without mutating an already emitted RuntimeRow."""
        for index, existing in enumerate(self.visible_frames):
            if existing.timestamp == frame.timestamp:
                self.visible_frames[index] = frame
                return True
        return False

    def run(self, frames: tuple[MarketFrame, ...]) -> RuntimeResult:
        try:
            for frame in frames:
                self.step(frame, total_bars=len(frames))
        except StrategyRuntimeError:
            return RuntimeResult(
                status="PARTIAL" if self.rows else "FAILED",
                rows=tuple(self.rows),
                failure=self.failure,
                unfilled_signal_count=self.unfilled_signal_count,
            )
        return RuntimeResult(
            status="COMPLETED",
            rows=tuple(self.rows),
            failure=None,
            unfilled_signal_count=self.unfilled_signal_count,
        )


def legacy_timeline(rows: tuple[RuntimeRow, ...]) -> tuple[TimelineRow, ...]:
    """Project native runtime rows into the legacy domain without changing its semantics."""

    timeline: list[TimelineRow] = []
    for row in rows:
        features = {item.name: item for item in row.features}
        zscore = features.get("zscore")
        hedge = features.get("hedge_ratio")
        spread = features.get("spread")
        rolling_mean = features.get("rolling_mean")
        rolling_std = features.get("rolling_std")
        feature = FeaturePoint(
            hedge_ratio=None if hedge is None else hedge.value,
            spread=None if spread is None else spread.value,
            rolling_mean=None if rolling_mean is None else rolling_mean.value,
            rolling_std=None if rolling_std is None else rolling_std.value,
            zscore=None if zscore is None else zscore.value,
            window_start=None if zscore is None else zscore.window_start,
            window_end=None if zscore is None else zscore.window_end,
            available_at=row.timestamp,
        )
        intent = row.decision.intent
        action = cast(
            Literal["LONG_SPREAD", "SHORT_SPREAD", "CLOSE", "HOLD", "WARMUP"],
            intent.signal,
        )
        previous_target: Literal[-1, 0, 1] = (
            1
            if intent.previous_state == "LONG_SPREAD"
            else -1
            if intent.previous_state == "SHORT_SPREAD"
            else 0
        )
        decision = SignalDecision(
            signal_id=row.decision.signal_id,
            action=action,
            target_position=intent.target_state,
            reason=intent.reason,
            decided_at=row.timestamp,
            previous_target=previous_target,
            conditions=intent.conditions,
        )
        timeline.append(
            TimelineRow(
                timestamp=row.timestamp,
                asset_a=row.market["ASSET_A"]["close"],
                asset_b=row.market["ASSET_B"]["close"],
                feature=feature,
                decision=decision,
                orders=row.orders,
                executions=row.executions,
                portfolio=row.portfolio,
            )
        )
    return tuple(timeline)
