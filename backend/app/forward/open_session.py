from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.forward.models import (
    ForwardSessionSnapshot,
    ForwardSessionSummary,
    ForwardTrace,
    PendingTransition,
)
from app.models import MarketFrame
from app.sdk.models import ParameterValue, RuntimeFailure
from app.sdk.runtime import StrategyRuntime, StrategyRuntimeError
from app.sdk.strategy import VQDStrategy
from app.sdk.tracing import (
    RuntimeTraceConfiguration,
    build_runtime_trace,
    calculate_runtime_metrics,
)
from app.trace.models import BacktestTrace, TimelineEvent, TraceScalar


@dataclass(slots=True)
class OpenForwardSession:
    session_id: str
    strategy_id: str
    dataset_id: str
    source_frames: tuple[MarketFrame, ...]
    strategy_class: type[VQDStrategy]
    strategy_name: str
    strategy_version: str
    parameters: dict[str, ParameterValue]
    research_cutoff: datetime
    strategy_fingerprint: str = ""
    dataset_revision: str | None = None
    initial_cash: float = 100_000.0
    fee_bps: float = 5.0
    slippage_bps: float = 5.0
    spread_bps: float = 0.0
    market_impact_bps: float = 0.0
    dataset_frequency: str | None = None
    status: Literal["CREATED", "RUNNING", "PAUSED", "COMPLETED", "STOPPED", "FAILED"] = "CREATED"
    processed: int = 0
    events: list[TimelineEvent] = field(default_factory=list)
    pending: list[PendingTransition] = field(default_factory=list)
    expired_order_count: int = 0
    failure: RuntimeFailure | None = None
    runtime: StrategyRuntime = field(init=False)

    def __post_init__(self) -> None:
        self.runtime = self._new_runtime()

    def _new_runtime(self) -> StrategyRuntime:
        strategy = self.strategy_class()
        strategy_parameters = {
            item.name: self.parameters.get(item.name, item.default)
            for item in strategy.parameter_definitions()
        }
        return StrategyRuntime(
            strategy=strategy,
            parameters=strategy_parameters,
            initial_cash=self.initial_cash,
            fee_bps=self.fee_bps,
            slippage_bps=self.slippage_bps,
            spread_bps=self.spread_bps,
            market_impact_bps=self.market_impact_bps,
        )

    def _trace_parameters(self) -> dict[str, TraceScalar]:
        return {
            **self.runtime.parameters,
            "initial_cash": self.initial_cash,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "spread_bps": self.spread_bps,
            "market_impact_bps": self.market_impact_bps,
        }

    def _trace_configuration(self) -> RuntimeTraceConfiguration:
        return RuntimeTraceConfiguration(
            dataset_id=self.dataset_id,
            dataset_name=self.dataset_id,
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            parameters=self._trace_parameters(),
            initial_cash=self.initial_cash,
            dataset_frequency=self.dataset_frequency,
        )

    def start(self) -> None:
        if self.status != "CREATED":
            raise ValueError(f"Cannot start a {self.status} session")
        self.status = "RUNNING"

    def pause(self) -> None:
        if self.status != "RUNNING":
            raise ValueError(f"Cannot pause a {self.status} session")
        self.status = "PAUSED"

    def resume(self) -> None:
        if self.status != "PAUSED":
            raise ValueError(f"Cannot resume a {self.status} session")
        self.status = "RUNNING"

    def stop(self) -> None:
        if self.status not in {"CREATED", "RUNNING", "PAUSED"}:
            raise ValueError(f"Cannot stop a {self.status} session")
        self.status = "STOPPED"

    def step(self) -> None:
        if self.status != "RUNNING":
            raise ValueError(f"Cannot step a {self.status} session")
        if self.processed >= len(self.source_frames):
            self.status = "COMPLETED"
            return
        frame = self.source_frames[self.processed]
        index = self.processed
        try:
            row = self.runtime.step(frame, total_bars=len(self.source_frames))
        except StrategyRuntimeError as exc:
            self.failure = exc.failure
            self.status = "FAILED"
            return
        for pending_index, pending in enumerate(self.pending):
            if pending.status == "PENDING" and pending.scheduled_bar_index == index:
                self.pending[pending_index] = pending.model_copy(
                    update={
                        "status": "FILLED",
                        "scheduled_at": frame.timestamp,
                        "resolved_at": frame.timestamp,
                    }
                )
        prefix = build_runtime_trace(tuple(self.runtime.rows), self._trace_configuration())
        event = prefix.timeline[-1]
        self.events.append(event)
        self.processed += 1
        if row.decision.signal_id is not None:
            due = index + 1
            values = row.decision.intent.target_positions or row.decision.intent.target_weights
            pending = PendingTransition(
                pending_id=f"pending-{row.decision.signal_id}",
                source_signal_id=row.decision.signal_id,
                source_event_id=event.event_id,
                source_bar_index=index,
                target_position=row.decision.intent.target_state,
                hedge_ratio=0.0,
                status="PENDING",
                scheduled_bar_index=due,
                target_positions=dict(values),
            )
            if due >= len(self.source_frames):
                pending = pending.model_copy(
                    update={"status": "EXPIRED_END_OF_DATA", "resolved_at": frame.timestamp}
                )
                self.expired_order_count += 1
            self.pending.append(pending)
        if self.processed >= len(self.source_frames):
            for pending_index, pending in enumerate(self.pending):
                if pending.status == "PENDING":
                    self.pending[pending_index] = pending.model_copy(
                        update={
                            "status": "EXPIRED_END_OF_DATA",
                            "resolved_at": frame.timestamp,
                        }
                    )
                    self.expired_order_count += 1
            self.status = "COMPLETED"

    def trace(self) -> ForwardTrace:
        diagnostics = (
            ()
            if not self.runtime.rows
            else build_runtime_trace(
                tuple(self.runtime.rows), self._trace_configuration()
            ).diagnostics
        )
        return ForwardTrace(
            session_id=self.session_id,
            strategy_id=self.strategy_id,
            parameters=self._trace_parameters(),
            timeline=tuple(self.events),
            diagnostics=diagnostics,
        )

    def summary(self) -> ForwardSessionSummary:
        if not self.runtime.rows:
            return ForwardSessionSummary(
                initial_equity=self.initial_cash,
                final_equity=self.initial_cash,
                total_return=0.0,
                max_drawdown=0.0,
                fees=0.0,
                slippage=0.0,
                signal_count=0,
                execution_count=0,
                closed_trade_count=0,
                open_trade_count=0,
                processed_bars=0,
                expired_order_count=self.expired_order_count,
            )
        rows = tuple(self.runtime.rows)
        metrics = calculate_runtime_metrics(
            rows, self.initial_cash, dataset_frequency=self.dataset_frequency
        )
        trace = build_runtime_trace(rows, self._trace_configuration())
        return ForwardSessionSummary(
            initial_equity=self.initial_cash,
            final_equity=rows[-1].portfolio.equity,
            total_return=metrics.total_return,
            max_drawdown=metrics.max_drawdown,
            fees=metrics.total_fees,
            slippage=metrics.total_slippage,
            signal_count=sum(row.decision.signal_id is not None for row in rows),
            execution_count=sum(len(row.executions) for row in rows),
            closed_trade_count=sum(item.status == "CLOSED" for item in trace.trades),
            open_trade_count=sum(item.status == "OPEN" for item in trace.trades),
            processed_bars=self.processed,
            expired_order_count=self.expired_order_count,
        )

    def snapshot(self) -> ForwardSessionSnapshot:
        latest = self.events[-1] if self.events else None
        portfolio = self.runtime.portfolio
        equity = latest.pnl_snapshot.equity if latest else self.initial_cash
        return ForwardSessionSnapshot(
            session_id=self.session_id,
            status=self.status,
            strategy_id=self.strategy_id,
            dataset_id=self.dataset_id,
            parameters=self._trace_parameters(),
            processed_bar_count=self.processed,
            total_bar_count=len(self.source_frames),
            current_timestamp=None if latest is None else latest.timestamp,
            cash=portfolio.cash,
            quantity_a=portfolio.quantity_a,
            quantity_b=portfolio.quantity_b,
            positions=dict(portfolio.positions),
            equity=equity,
            cumulative_pnl=equity - self.initial_cash,
            cumulative_fees=portfolio.cumulative_fees,
            cumulative_slippage=portfolio.cumulative_slippage,
            current_signal_state=("WARMUP" if latest is None else latest.signal_evaluation.signal),
            pending_transitions=tuple(self.pending),
            latest_event=latest,
            summary=self.summary(),
            failure=self.failure,
        )

    def same_path_batch(self) -> BacktestTrace | None:
        if not self.runtime.rows:
            return None
        runtime = self._new_runtime()
        result = runtime.run(self.source_frames[: self.processed])
        if not result.rows:
            return None
        return build_runtime_trace(result.rows, self._trace_configuration())
