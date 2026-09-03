from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from app.broker import BrokerAccountSnapshot
from app.market_data import (
    MarketApplyKind,
    MarketBar,
    MarketClockSnapshot,
    PointInTimeMarketStore,
)
from app.models import Execution
from app.paper.lifecycle import validate_paper_transition
from app.paper.models import (
    JournalDisposition,
    MarketJournalEntry,
    MarketRevisionNotice,
    PaperAccountSnapshot,
    PaperBrokerEvent,
    PaperExecution,
    PaperMarketEvent,
    PaperPendingOrder,
    PaperSessionManifest,
    PaperSessionSnapshot,
    PaperSessionStatus,
    PaperTrace,
    RecoveryCheckpoint,
)
from app.sdk.loader import load_strategy
from app.sdk.models import ParameterValue
from app.sdk.runtime import StrategyRuntime
from app.sdk.tracing import (
    RuntimeTraceConfiguration,
    build_runtime_trace,
    calculate_runtime_metrics,
)
from app.trace.models import BacktestTrace


def _semantic_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class LivePaperSession:
    def __init__(self, manifest: PaperSessionManifest, strategy_path: str) -> None:
        self.manifest = manifest
        self.market_store = PointInTimeMarketStore(manifest.symbols)
        loaded = load_strategy(strategy_path, manifest.strategy_class_name)
        if loaded.source_fingerprint != manifest.strategy_fingerprint:
            raise ValueError("Paper session strategy snapshot fingerprint mismatch")
        strategy = loaded.strategy_class()
        definitions = {item.name: item for item in strategy.parameter_definitions()}
        parameters: dict[str, ParameterValue] = {
            name: manifest.parameters.get(name, spec.default) for name, spec in definitions.items()
        }
        self.runtime = StrategyRuntime(
            strategy=strategy,
            parameters=parameters,
            initial_cash=manifest.initial_cash,
            fee_bps=manifest.fee_bps,
            slippage_bps=manifest.slippage_bps,
            spread_bps=manifest.spread_bps,
            market_impact_bps=manifest.market_impact_bps,
            execution_mode=(
                "external" if manifest.execution_mode == "ALPACA_PAPER" else "simulated"
            ),
        )
        self.journal: list[MarketJournalEntry] = []
        self.revisions: list[MarketRevisionNotice] = []
        self.market_clock: MarketClockSnapshot | None = None
        self.broker_account: BrokerAccountSnapshot | None = None
        self.broker_events: list[PaperBrokerEvent] = []

    def _trace_configuration(self) -> RuntimeTraceConfiguration:
        historical = self.manifest.clock_mode == "HISTORICAL"
        dataset_id = (
            self.manifest.dataset_id
            if historical and self.manifest.dataset_id is not None
            else f"live:{self.manifest.provider}:{self.manifest.feed}"
        )
        dataset_name = (
            f"Historical Paper · {self.manifest.dataset_id}"
            if historical
            else (
                f"{self.manifest.provider.upper()} · {self.manifest.feed.upper()} "
                f"· {self.manifest.timeframe}"
            )
        )
        return RuntimeTraceConfiguration(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            strategy_id=self.manifest.strategy_id,
            strategy_name=self.manifest.strategy_name,
            parameters={
                **self.manifest.parameters,
                "initial_cash": self.manifest.initial_cash,
                "fee_bps": self.manifest.fee_bps,
                "slippage_bps": self.manifest.slippage_bps,
                "spread_bps": self.manifest.spread_bps,
                "market_impact_bps": self.manifest.market_impact_bps,
            },
            initial_cash=self.manifest.initial_cash,
            dataset_frequency=self.manifest.timeframe,
        )

    def classify(
        self,
        bar: MarketBar,
        *,
        historical_warmup: bool = False,
        force_evaluate: bool = False,
    ) -> tuple[MarketApplyKind, JournalDisposition]:
        kind = self.market_store.classify(bar)
        if kind == MarketApplyKind.FRAME_READY:
            disposition: JournalDisposition
            if historical_warmup:
                disposition = "HISTORICAL_WARMUP"
            else:
                disposition = (
                    "EVALUATED"
                    if force_evaluate or self.manifest.status != "PAUSED"
                    else "EVALUATION_SKIPPED_PAUSED"
                )
            return kind, disposition
        mapping: dict[MarketApplyKind, JournalDisposition] = {
            MarketApplyKind.BUFFERED: "BUFFERED",
            MarketApplyKind.CORRECTION: "CORRECTION_APPLIED",
            MarketApplyKind.DUPLICATE: "DUPLICATE_IGNORED",
            MarketApplyKind.OUT_OF_ORDER: "OUT_OF_ORDER_REJECTED",
        }
        return kind, mapping[kind]

    @staticmethod
    def _kind_for_entry(entry: MarketJournalEntry) -> MarketApplyKind:
        mapping: dict[JournalDisposition, MarketApplyKind] = {
            "BUFFERED": MarketApplyKind.BUFFERED,
            "HISTORICAL_WARMUP": MarketApplyKind.FRAME_READY,
            "EVALUATED": MarketApplyKind.FRAME_READY,
            "EVALUATION_SKIPPED_PAUSED": MarketApplyKind.FRAME_READY,
            "CORRECTION_APPLIED": MarketApplyKind.CORRECTION,
            "DUPLICATE_IGNORED": MarketApplyKind.DUPLICATE,
            "OUT_OF_ORDER_REJECTED": MarketApplyKind.OUT_OF_ORDER,
        }
        return mapping[entry.disposition]

    def apply_entry(self, entry: MarketJournalEntry) -> None:
        expected = len(self.journal) + 1
        if entry.sequence != expected:
            raise ValueError(f"Expected journal sequence {expected}, received {entry.sequence}")
        kind = self._kind_for_entry(entry)
        previous_versions = self.market_store.versions(entry.bar.symbol, entry.bar.event_time)
        frame = self.market_store.commit(entry.bar, kind)
        if kind == MarketApplyKind.CORRECTION and frame is not None:
            revised_decision_history = self.runtime.revise_visible_frame(frame)
            if previous_versions and revised_decision_history:
                previous = previous_versions[-1]
                self.revisions.append(
                    MarketRevisionNotice(
                        symbol=entry.bar.symbol,
                        event_time=entry.bar.event_time,
                        used_revision=previous.revision,
                        used_close=previous.close,
                        later_revision=entry.bar.revision,
                        later_close=entry.bar.close,
                        revision_available_at=entry.bar.available_at,
                    )
                )
        elif kind == MarketApplyKind.FRAME_READY and frame is not None:
            if entry.disposition == "HISTORICAL_WARMUP":
                self.runtime.step_historical_warmup(frame)
            elif entry.disposition == "EVALUATION_SKIPPED_PAUSED":
                self.runtime.step_without_strategy(frame)
            else:
                self.runtime.step(frame)
        self.journal.append(entry)

    def trace(self) -> PaperTrace:
        if self.runtime.rows:
            built = build_runtime_trace(tuple(self.runtime.rows), self._trace_configuration())
            timeline = built.timeline
            diagnostics = built.diagnostics
        else:
            timeline = ()
            diagnostics = ()
        return PaperTrace(
            session_id=self.manifest.session_id,
            strategy_id=self.manifest.strategy_id,
            parameters=self._trace_configuration().parameters,
            timeline=timeline,
            diagnostics=diagnostics,
            market_revisions=tuple(self.revisions),
            execution_mode=self.manifest.execution_mode,
            broker_events=tuple(self.broker_events),
        )

    def apply_broker_event(self, event: PaperBrokerEvent) -> None:
        if event.session_id != self.manifest.session_id:
            raise ValueError("Broker event does not belong to this paper session")
        if any(existing.event_id == event.event_id for existing in self.broker_events):
            return
        if event.sequence != len(self.broker_events) + 1:
            raise ValueError(
                f"Expected broker event sequence {len(self.broker_events) + 1}, "
                f"received {event.sequence}"
            )
        if event.fill_quantity > 0:
            if event.fill_price is None or event.execution_id is None:
                raise ValueError("Broker fill event requires fill price and execution id")
            direction = 1.0 if event.side == "BUY" else -1.0
            slippage = event.fill_quantity * direction * (event.fill_price - event.reference_price)
            self.runtime.apply_external_execution(
                Execution(
                    execution_id=event.execution_id,
                    source_order_id=event.order_id,
                    symbol=event.symbol,
                    side=event.side,
                    quantity=event.fill_quantity,
                    expected_price=event.reference_price,
                    fill_price=event.fill_price,
                    traded_notional=event.fill_quantity * event.fill_price,
                    fee=0.0,
                    slippage=slippage,
                    executed_at=event.occurred_at,
                )
            )
        self.broker_events.append(event)

    def research_trace(self) -> BacktestTrace | None:
        if not self.runtime.rows:
            return None
        return build_runtime_trace(tuple(self.runtime.rows), self._trace_configuration())

    def checkpoint(self) -> RecoveryCheckpoint:
        trace = self.trace()
        portfolio = self.runtime.portfolio
        portfolio_value = {
            "cash": portfolio.cash,
            "positions": dict(sorted(portfolio.positions.items())),
            "fees": portfolio.cumulative_fees,
            "slippage": portfolio.cumulative_slippage,
            "equity": self._equity(),
        }
        trace_value = {
            "timeline": [item.model_dump(mode="json") for item in trace.timeline],
            "diagnostics": [item.model_dump(mode="json") for item in trace.diagnostics],
            "revisions": [item.model_dump(mode="json") for item in trace.market_revisions],
            "broker_events": [item.model_dump(mode="json") for item in trace.broker_events],
        }
        return RecoveryCheckpoint(
            last_event_sequence=len(self.journal),
            last_processed_market_event_id=None
            if not self.journal
            else self.journal[-1].market_event_id,
            market_watermark=self.market_store.last_completed_time,
            portfolio_hash=_semantic_hash(portfolio_value),
            trace_semantic_hash=_semantic_hash(trace_value),
        )

    def _equity(self) -> float:
        if not self.runtime.visible_frames:
            return self.manifest.initial_cash
        prices = {
            symbol: fields["close"]
            for symbol, fields in self.runtime.visible_frames[-1].values.items()
            if "close" in fields
        }
        return self.runtime.portfolio.mark_prices(prices).equity

    def _account(self) -> PaperAccountSnapshot:
        portfolio = self.runtime.portfolio
        if self.runtime.rows:
            metrics = calculate_runtime_metrics(
                tuple(self.runtime.rows),
                self.manifest.initial_cash,
                dataset_frequency=self.manifest.timeframe,
            )
            max_drawdown = metrics.max_drawdown
        else:
            max_drawdown = 0.0
        pending = tuple(
            PaperPendingOrder(
                source_signal_id=item.signal_id,
                due_market_index=item.due_index,
                target_positions=dict(item.intent.target_positions or item.intent.target_weights),
            )
            for item in self.runtime.pending
        )
        executions = tuple(
            PaperExecution(
                execution_id=item.execution_id,
                symbol=item.symbol,
                side=item.side,
                quantity=item.quantity,
                fill_price=item.fill_price,
                fee=item.fee,
                slippage=item.slippage,
                executed_at=item.executed_at,
                spread_cost=item.spread_cost,
                market_impact=item.market_impact,
            )
            for row in self.runtime.rows
            for item in row.executions
        ) + tuple(
            PaperExecution(
                execution_id=item.execution_id,
                symbol=item.symbol,
                side=item.side,
                quantity=item.quantity,
                fill_price=item.fill_price,
                fee=item.fee,
                slippage=item.slippage,
                executed_at=item.executed_at,
                spread_cost=item.spread_cost,
                market_impact=item.market_impact,
            )
            for item in self.runtime.external_executions
        )
        equity = self._equity()
        return PaperAccountSnapshot(
            cash=portfolio.cash,
            positions=dict(portfolio.positions),
            equity=equity,
            net_pnl=equity - self.manifest.initial_cash,
            cumulative_fees=portfolio.cumulative_fees,
            cumulative_slippage=portfolio.cumulative_slippage,
            max_drawdown=max_drawdown,
            pending_orders=pending,
            executions=executions,
        )

    def snapshot(self) -> PaperSessionSnapshot:
        recent = tuple(
            PaperMarketEvent(
                sequence=entry.sequence,
                market_event_id=entry.market_event_id,
                disposition=entry.disposition,
                symbol=entry.bar.symbol,
                event_time=entry.bar.event_time,
                available_at=entry.bar.available_at,
                received_at=entry.bar.received_at,
                close=entry.bar.close,
                revision=entry.bar.revision,
                is_correction=entry.bar.is_correction,
                latency_ms=entry.bar.latency_ms,
            )
            for entry in self.journal[-100:]
        )
        trace = self.trace()
        return PaperSessionSnapshot(
            session_id=self.manifest.session_id,
            account_id=self.manifest.account_id,
            status=self.manifest.status,
            feed_status=self.manifest.feed_status,
            recovery_status=self.manifest.recovery_status,
            strategy_id=self.manifest.strategy_id,
            strategy_name=self.manifest.strategy_name,
            strategy_fingerprint=self.manifest.strategy_fingerprint,
            symbols=self.manifest.symbols,
            securities=self.manifest.securities,
            parameters=self.manifest.parameters,
            provider=self.manifest.provider,
            feed=self.manifest.feed,
            timeframe=self.manifest.timeframe,
            market_session=self.manifest.market_session,
            initial_cash=self.manifest.initial_cash,
            created_at=self.manifest.created_at,
            started_at=self.manifest.started_at,
            stopped_at=self.manifest.stopped_at,
            last_market_event=self.manifest.last_market_event,
            last_received_at=None if not self.journal else self.journal[-1].bar.received_at,
            last_event_sequence=len(self.journal),
            last_processed_market_event_id=None
            if self.manifest.checkpoint is None
            else self.manifest.checkpoint.last_processed_market_event_id,
            market_watermark=None
            if self.manifest.checkpoint is None
            else self.manifest.checkpoint.market_watermark,
            evaluated_bar_count=sum(
                entry.disposition in {"HISTORICAL_WARMUP", "EVALUATED", "EVALUATION_SKIPPED_PAUSED"}
                for entry in self.journal
            ),
            historical_warmup_bar_count=sum(
                entry.disposition == "HISTORICAL_WARMUP" for entry in self.journal
            ),
            correction_count=sum(
                entry.disposition == "CORRECTION_APPLIED" for entry in self.journal
            ),
            duplicate_count=sum(entry.disposition == "DUPLICATE_IGNORED" for entry in self.journal),
            out_of_order_count=sum(
                entry.disposition == "OUT_OF_ORDER_REJECTED" for entry in self.journal
            ),
            market_clock=self.market_clock,
            account=self._account(),
            recent_market_events=recent,
            recent_revisions=tuple(self.revisions[-20:]),
            latest_event=None if not trace.timeline else trace.timeline[-1],
            error_code=self.manifest.error_code,
            error_message=self.manifest.error_message,
            research_run_id=self.manifest.research_run_id,
            reference_run_id=self.manifest.reference_run_id,
            execution_mode=self.manifest.execution_mode,
            clock_mode=self.manifest.clock_mode,
            dataset_id=self.manifest.dataset_id,
            simulation_start=self.manifest.simulation_start,
            simulation_end=self.manifest.simulation_end,
            simulation_time=self.manifest.simulation_time,
            simulation_speed=self.manifest.simulation_speed,
            broker_status=self.manifest.broker_status,
            broker_account=self.broker_account,
            recent_broker_events=tuple(self.broker_events[-100:]),
        )

    def set_status(self, status: PaperSessionStatus, *, feed_status: str | None = None) -> None:
        validate_paper_transition(self.manifest.status, status)
        now = datetime.now(UTC)
        updates: dict[str, object] = {"status": status, "updated_at": now}
        if feed_status is not None:
            updates["feed_status"] = feed_status
        if status == "RUNNING" and self.manifest.started_at is None:
            updates["started_at"] = now
        if status == "STOPPED":
            updates["stopped_at"] = now
        self.manifest = self.manifest.model_copy(update=updates)

    def mark_error(self, code: str, message: str) -> None:
        validate_paper_transition(self.manifest.status, "ERROR")
        self.manifest = self.manifest.model_copy(
            update={
                "status": "ERROR",
                "feed_status": "DISCONNECTED",
                "recovery_status": "RECOVERY_DIVERGENCE"
                if code == "RECOVERY_DIVERGENCE"
                else self.manifest.recovery_status,
                "error_code": code,
                "error_message": message,
                "updated_at": datetime.now(UTC),
            }
        )
