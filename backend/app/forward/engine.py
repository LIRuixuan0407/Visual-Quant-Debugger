from dataclasses import dataclass, field
from typing import Literal

from app.backtest import BacktestParameters, run_backtest
from app.diagnostics import calculate_metrics
from app.forward.feed import HistoricalBarFeed
from app.forward.models import (
    ForwardSessionSnapshot,
    ForwardSessionSummary,
    ForwardTrace,
    PendingTransition,
)
from app.models import BacktestResult, MarketBar, TimelineRow
from app.portfolio import Portfolio
from app.sdk.runtime import StrategyRuntime, legacy_timeline
from app.strategies import PairsTradingStrategy
from app.trace import TraceBuildConfiguration, build_trace
from app.trace.models import TimelineEvent
from app.trace.validation import collect_look_ahead_diagnostics_from_events


@dataclass(slots=True)
class ForwardSession:
    session_id: str
    strategy_id: str
    dataset_id: str
    source_bars: tuple[MarketBar, ...]
    parameters: BacktestParameters
    strategy_fingerprint: str = ""
    dataset_revision: str | None = None
    status: Literal["CREATED", "RUNNING", "PAUSED", "COMPLETED", "STOPPED", "ERROR"] = "CREATED"
    feed: HistoricalBarFeed = field(init=False)
    portfolio: Portfolio = field(init=False)
    rows: list[TimelineRow] = field(default_factory=list)
    events: list[TimelineEvent] = field(default_factory=list)
    pending: list[PendingTransition] = field(default_factory=list)
    expired_order_count: int = 0
    current_signal_state: str = "WARMUP"
    runtime: StrategyRuntime = field(init=False)

    def __post_init__(self) -> None:
        self.feed = HistoricalBarFeed(self.source_bars)
        self.runtime = StrategyRuntime(
            strategy=PairsTradingStrategy(gross_target=self.parameters.gross_target),
            parameters={
                "lookback": self.parameters.strategy.lookback,
                "entry_z": self.parameters.strategy.entry_z,
                "exit_z": self.parameters.strategy.exit_z,
            },
            initial_cash=self.parameters.initial_cash,
            fee_bps=self.parameters.fee_bps,
            slippage_bps=self.parameters.slippage_bps,
            spread_bps=self.parameters.spread_bps,
            market_impact_bps=self.parameters.market_impact_bps,
        )
        self.portfolio = self.runtime.portfolio

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
        if self.status not in {"RUNNING", "PAUSED", "CREATED"}:
            raise ValueError(f"Cannot stop a {self.status} session")
        self.status = "STOPPED"

    def _trace_config(self) -> TraceBuildConfiguration:
        p = self.parameters
        return TraceBuildConfiguration(
            dataset_name=self.dataset_id,
            strategy_id=self.strategy_id,
            strategy_name="Pairs Trading",
            parameters={
                "lookback": p.strategy.lookback,
                "entry_z": p.strategy.entry_z,
                "exit_z": p.strategy.exit_z,
                "initial_cash": p.initial_cash,
                "gross_target": p.gross_target,
                "fee_bps": p.fee_bps,
                "slippage_bps": p.slippage_bps,
                "spread_bps": p.spread_bps,
                "market_impact_bps": p.market_impact_bps,
            },
            lookback=p.strategy.lookback,
            initial_cash=p.initial_cash,
        )

    def step(self) -> None:
        if self.status != "RUNNING":
            raise ValueError(f"Cannot step a {self.status} session")
        bar = self.feed.next_bar()
        if bar is None:
            self.status = "COMPLETED"
            return
        index = self.feed.processed - 1
        self.runtime.step(bar.as_frame(), total_bars=self.feed.total)
        for pending_index, pending in enumerate(self.pending):
            if pending.status == "PENDING" and pending.scheduled_bar_index == index:
                self.pending[pending_index] = pending.model_copy(
                    update={
                        "status": "FILLED",
                        "resolved_at": bar.timestamp,
                        "scheduled_at": bar.timestamp,
                    }
                )
        projected = legacy_timeline(tuple(self.runtime.rows))
        row = projected[-1]
        decision = row.decision
        feature = row.feature
        self.current_signal_state = decision.action
        self.rows.append(row)
        metrics = calculate_metrics(tuple(self.rows), self.parameters.initial_cash)
        # Reuse the exact Trace 1.0 builder, but append only the newly revealed event
        # to the session trace.
        prefix_trace = build_trace(
            self.feed.available_bars, tuple(self.rows), metrics, self._trace_config()
        )
        new_event = prefix_trace.timeline[-1]
        if self.events and self.events[-1].event_id == new_event.event_id:
            raise RuntimeError("Forward trace attempted to rewrite an existing event")
        self.events.append(new_event)

        if decision.signal_id is not None and feature.hedge_ratio is not None:
            scheduled = index + 1
            pending = PendingTransition(
                pending_id=f"pending-{decision.signal_id}",
                source_signal_id=decision.signal_id,
                source_event_id=new_event.event_id,
                source_bar_index=index,
                target_position=decision.target_position,
                hedge_ratio=feature.hedge_ratio,
                status="PENDING",
                scheduled_bar_index=scheduled,
            )
            if scheduled >= self.feed.total:
                pending = pending.model_copy(
                    update={"status": "EXPIRED_END_OF_DATA", "resolved_at": bar.timestamp}
                )
                self.expired_order_count += 1
            self.pending.append(pending)

        if self.feed.processed >= self.feed.total:
            for idx, pending in enumerate(self.pending):
                if pending.status == "PENDING":
                    self.pending[idx] = pending.model_copy(
                        update={"status": "EXPIRED_END_OF_DATA", "resolved_at": bar.timestamp}
                    )
                    self.expired_order_count += 1
            self.status = "COMPLETED"

    def trace(self) -> ForwardTrace:
        diagnostics = collect_look_ahead_diagnostics_from_events(self.events)
        return ForwardTrace(
            session_id=self.session_id,
            strategy_id=self.strategy_id,
            parameters=self._trace_config().parameters,
            timeline=tuple(self.events),
            diagnostics=diagnostics,
        )

    def summary(self) -> ForwardSessionSummary:
        if self.rows:
            metrics = calculate_metrics(tuple(self.rows), self.parameters.initial_cash)
            final_equity = self.rows[-1].portfolio.equity
            executions = sum(len(row.executions) for row in self.rows)
            signals = sum(row.decision.signal_id is not None for row in self.rows)
            total_return = metrics.total_return
            max_drawdown = metrics.max_drawdown
            fees = self.rows[-1].portfolio.cumulative_fees
            slippage = self.rows[-1].portfolio.cumulative_slippage
        else:
            final_equity = self.parameters.initial_cash
            executions = signals = 0
            total_return = max_drawdown = fees = slippage = 0.0
        # Derive trade counts from a same-prefix trace so trade lifecycle remains trace-native.
        if self.rows:
            metrics = calculate_metrics(tuple(self.rows), self.parameters.initial_cash)
            trace = build_trace(
                self.feed.available_bars, tuple(self.rows), metrics, self._trace_config()
            )
            closed = sum(t.status == "CLOSED" for t in trace.trades)
            open_ = sum(t.status == "OPEN" for t in trace.trades)
        else:
            closed = open_ = 0
        return ForwardSessionSummary(
            initial_equity=self.parameters.initial_cash,
            final_equity=final_equity,
            total_return=total_return,
            max_drawdown=max_drawdown,
            fees=fees,
            slippage=slippage,
            signal_count=signals,
            execution_count=executions,
            closed_trade_count=closed,
            open_trade_count=open_,
            processed_bars=self.feed.processed,
            expired_order_count=self.expired_order_count,
        )

    def snapshot(self) -> ForwardSessionSnapshot:
        latest = self.events[-1] if self.events else None
        equity = latest.pnl_snapshot.equity if latest else self.parameters.initial_cash
        return ForwardSessionSnapshot(
            session_id=self.session_id,
            status=self.status,
            strategy_id=self.strategy_id,
            dataset_id=self.dataset_id,
            parameters=self._trace_config().parameters,
            processed_bar_count=self.feed.processed,
            total_bar_count=self.feed.total,
            current_timestamp=None if latest is None else latest.timestamp,
            cash=self.portfolio.cash,
            quantity_a=self.portfolio.quantity_a,
            quantity_b=self.portfolio.quantity_b,
            equity=equity,
            cumulative_pnl=equity - self.parameters.initial_cash,
            cumulative_fees=self.portfolio.cumulative_fees,
            cumulative_slippage=self.portfolio.cumulative_slippage,
            current_signal_state=self.current_signal_state,
            pending_transitions=tuple(self.pending),
            latest_event=latest,
            summary=self.summary(),
        )

    def same_path_batch(self) -> BacktestResult | None:
        if len(self.feed.available_bars) < 3:
            return None
        return run_backtest(self.feed.available_bars, self.parameters)
