from __future__ import annotations

import asyncio
import hashlib
import json
import platform
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo

import httpx

from app.broker import (
    TERMINAL_BROKER_STATUSES,
    AlpacaPaperBrokerAdapter,
    BrokerOrderRequest,
    BrokerOrderUpdate,
    PaperBrokerAdapter,
)
from app.market_data import (
    AlpacaStockMarketDataAdapter,
    FakeLiveMarketDataAdapter,
    MarketBar,
    MarketDataAdapter,
)
from app.paper.lifecycle import recovery_target_status
from app.paper.models import (
    CreatePaperAccount,
    CreatePaperSession,
    MarketJournalEntry,
    PaperAccount,
    PaperAccountList,
    PaperBrokerEvent,
    PaperFill,
    PaperOperationalHealth,
    PaperOperationEvent,
    PaperOperationLog,
    PaperOrder,
    PaperRecoveryReport,
    PaperSessionList,
    PaperSessionManifest,
    PaperSessionSnapshot,
    PaperTrace,
    RecoveryCheckpoint,
    RuntimeConsistencyReport,
)
from app.paper.repository import PaperSessionNotFoundError, PaperSessionRepository
from app.paper.session import LivePaperSession
from app.runs.models import (
    ArtifactHashes,
    DatasetRevision,
    EnvironmentSnapshot,
    ExecutionModelRevision,
    ResearchPeriod,
    RunManifest,
    StrategyRevision,
)
from app.runs.repository import RunRepository, sha256_bytes
from app.runs.service import VQD_ENGINE_VERSION, _run_metrics, _trace_id, _trace_payload
from app.sdk.registry import StrategyRegistry, strategy_registry
from app.settings import integration_vault
from app.trace.models import BacktestTrace

AdapterFactory = Callable[[PaperSessionManifest], MarketDataAdapter]
BrokerAdapterFactory = Callable[[PaperSessionManifest], PaperBrokerAdapter]

_SENSITIVE_OPERATION_KEYS = ("api_key", "secret", "token", "credential", "password")
_OPEN_ORDER_STATUSES = frozenset(
    {
        "CREATED",
        "SUBMITTED",
        "PARTIALLY_FILLED",
        "PENDING_CANCEL",
        "HELD",
        "SUSPENDED",
        "UNKNOWN",
    }
)


class PaperSessionService:
    def __init__(
        self,
        repository: PaperSessionRepository,
        *,
        registry: StrategyRegistry | None = None,
        adapter_factory: AdapterFactory | None = None,
        broker_adapter_factory: BrokerAdapterFactory | None = None,
        run_repository: RunRepository | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry or strategy_registry
        self._adapter_factory = adapter_factory or self._default_adapter
        self._broker_adapter_factory = broker_adapter_factory or self._default_broker_adapter
        self.run_repository = run_repository or RunRepository(repository.workspace_root)
        self._sessions: dict[str, LivePaperSession] = {}
        self._adapters: dict[str, MarketDataAdapter] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._broker_adapters: dict[str, PaperBrokerAdapter] = {}
        self._broker_tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = {}
        self._recover_all()

    @staticmethod
    def _market_event_id(bar: MarketBar) -> str:
        identity = "|".join(
            (
                bar.provider,
                bar.feed,
                bar.symbol,
                bar.event_time.isoformat(),
                str(bar.revision),
            )
        ).encode()
        return f"market-{hashlib.sha256(identity).hexdigest()[:24]}"

    def _record_operation(
        self,
        session_id: str,
        operation_type: Literal[
            "CREATED",
            "STARTED",
            "PAUSED",
            "RESUMED",
            "STOP_REQUESTED",
            "STOPPED",
            "FEED_DISCONNECTED",
            "FEED_RECONNECTING",
            "FEED_RECONNECTED",
            "BACKFILL_STARTED",
            "BACKFILL_COMPLETED",
            "BROKER_RECONCILIATION",
            "RECOVERY_STARTED",
            "RECOVERY_COMPLETED",
            "RECOVERY_DIVERGENCE",
            "ERROR",
        ],
        message: str,
        metadata: dict[str, str | int | float | bool | None] | None = None,
    ) -> PaperOperationEvent:
        safe_metadata = {
            key: value
            for key, value in (metadata or {}).items()
            if not any(fragment in key.lower() for fragment in _SENSITIVE_OPERATION_KEYS)
        }
        sequence = len(self.repository.read_operations(session_id)) + 1
        event = PaperOperationEvent(
            operation_id=f"operation-{session_id.removeprefix('paper-')}-{sequence:08d}",
            sequence=sequence,
            session_id=session_id,
            operation_type=operation_type,
            occurred_at=datetime.now(UTC),
            message=message,
            metadata=safe_metadata,
        )
        self.repository.append_operation(session_id, event)
        return event

    @staticmethod
    def _operation_int_metadata(event: PaperOperationEvent, key: str) -> int:
        value = event.metadata.get(key, 0)
        if isinstance(value, bool) or value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        try:
            return int(value)
        except ValueError:
            return 0

    @staticmethod
    def _checkpoint_matches(
        recorded: RecoveryCheckpoint | None, recovered: RecoveryCheckpoint
    ) -> bool:
        if recorded is None:
            return True
        return bool(
            recovered.last_event_sequence == recorded.last_event_sequence
            and recovered.portfolio_hash == recorded.portfolio_hash
            and recovered.trace_semantic_hash == recorded.trace_semantic_hash
            and (
                recorded.last_processed_market_event_id is None
                or recovered.last_processed_market_event_id
                == recorded.last_processed_market_event_id
            )
            and (
                recorded.market_watermark is None
                or recovered.market_watermark == recorded.market_watermark
            )
        )

    def _replay_session(
        self, persisted: PaperSessionManifest
    ) -> tuple[LivePaperSession, int, int, Exception | None]:
        recovering = persisted.model_copy(update={"recovery_status": "RECOVERING"})
        session = LivePaperSession(
            recovering, str(self.repository.strategy_path(persisted.session_id))
        )
        broker_events = self.repository.read_broker_events(persisted.session_id)
        broker_index = 0
        journal = self.repository.read_journal(persisted.session_id)
        try:
            for entry in journal:
                session.apply_entry(entry)
                while (
                    broker_index < len(broker_events)
                    and broker_events[broker_index].market_sequence <= entry.sequence
                ):
                    session.apply_broker_event(broker_events[broker_index])
                    broker_index += 1
            while broker_index < len(broker_events):
                session.apply_broker_event(broker_events[broker_index])
                broker_index += 1
        except Exception as exc:
            return session, len(journal), len(broker_events), exc
        return session, len(journal), len(broker_events), None

    def _recover_session(
        self, stored: PaperSessionManifest, *, explicit: bool
    ) -> PaperRecoveryReport:
        persisted = self._ensure_manifest_account(stored)
        session_id = persisted.session_id
        recorded = persisted.checkpoint
        self._record_operation(
            session_id,
            "RECOVERY_STARTED",
            "Explicit recovery started" if explicit else "Startup recovery started",
        )
        try:
            session, journal_count, broker_count, replay_error = self._replay_session(persisted)
            if replay_error is not None:
                failed = persisted.model_copy(
                    update={
                        "status": "ERROR",
                        "feed_status": "DISCONNECTED",
                        "recovery_status": "RECOVERY_DIVERGENCE",
                        "error_code": "RECOVERY_DIVERGENCE",
                        "error_message": f"Recovery failed: {type(replay_error).__name__}",
                        "updated_at": datetime.now(UTC),
                    }
                )
                session.manifest = failed
                self._sessions[session_id] = session
                self._locks.setdefault(session_id, asyncio.Lock())
                self.repository.save_trace(session_id, session.trace())
                self.repository.save_manifest(failed, equity=session.snapshot().account.equity)
                report = PaperRecoveryReport(
                    session_id=session_id,
                    status="RECOVERY_DIVERGENCE",
                    journal_event_count=journal_count,
                    broker_event_count=broker_count,
                    recorded_portfolio_hash=(
                        "unavailable" if recorded is None else recorded.portfolio_hash
                    ),
                    recovered_portfolio_hash="unavailable",
                    recorded_trace_hash=(
                        "unavailable" if recorded is None else recorded.trace_semantic_hash
                    ),
                    recovered_trace_hash="unavailable",
                    broker_reconciled=False,
                    account_reconciled=False,
                    warnings=(
                        f"Recovery failed with {type(replay_error).__name__}.",
                        "Session remains available in ERROR state for inspection or stopping.",
                    ),
                )
                self.repository.save_recovery_report(report)
                self._record_operation(
                    session_id,
                    "RECOVERY_DIVERGENCE",
                    "Recovery could not replay the durable journal",
                    {"error_type": type(replay_error).__name__},
                )
                return report
            recovered = session.checkpoint()
            matches = self._checkpoint_matches(recorded, recovered)
            warnings: tuple[str, ...] = ()
            if not matches:
                session.manifest = session.manifest.model_copy(
                    update={
                        "status": "ERROR",
                        "feed_status": "DISCONNECTED",
                        "recovery_status": "RECOVERY_DIVERGENCE",
                        "error_code": "RECOVERY_DIVERGENCE",
                        "error_message": (
                            "Deterministic replay did not match the persisted checkpoint"
                        ),
                        "updated_at": datetime.now(UTC),
                    }
                )
                warnings = (
                    "Recovered runtime state does not match the persisted checkpoint.",
                    "Session was not resumed automatically.",
                )
            else:
                restored_status = recovery_target_status(
                    persisted.status, explicit=explicit
                )
                session.manifest = session.manifest.model_copy(
                    update={
                        "status": restored_status,
                        "recovery_status": "READY",
                        "checkpoint": recovered,
                        "error_code": None,
                        "error_message": None,
                        "updated_at": datetime.now(UTC),
                    }
                )
            self._sessions[session_id] = session
            self._locks.setdefault(session_id, asyncio.Lock())
            self.repository.save_trace(session_id, session.trace())
            self.repository.save_manifest(
                session.manifest, equity=session.snapshot().account.equity
            )
            self._reconcile_account(session)
            report = PaperRecoveryReport(
                session_id=session_id,
                status=(
                    "RECOVERY_DIVERGENCE"
                    if not matches
                    else "RECOVERED"
                    if explicit or recorded is not None
                    else "READY"
                ),
                journal_event_count=journal_count,
                broker_event_count=broker_count,
                recorded_portfolio_hash=(
                    recovered.portfolio_hash if recorded is None else recorded.portfolio_hash
                ),
                recovered_portfolio_hash=recovered.portfolio_hash,
                recorded_trace_hash=(
                    recovered.trace_semantic_hash
                    if recorded is None
                    else recorded.trace_semantic_hash
                ),
                recovered_trace_hash=recovered.trace_semantic_hash,
                broker_reconciled=session.manifest.execution_mode == "VQD_SIMULATED",
                account_reconciled=True,
                warnings=warnings,
            )
            self.repository.save_recovery_report(report)
            self._record_operation(
                session_id,
                "RECOVERY_DIVERGENCE" if not matches else "RECOVERY_COMPLETED",
                warnings[0] if warnings else "Runtime state recovered from durable journals",
                {
                    "journal_event_count": journal_count,
                    "broker_event_count": broker_count,
                    "checkpoint_match": matches,
                },
            )
            return report
        except Exception as exc:
            failed = persisted.model_copy(
                update={
                    "status": "ERROR",
                    "feed_status": "DISCONNECTED",
                    "recovery_status": "RECOVERY_DIVERGENCE",
                    "error_code": "RECOVERY_DIVERGENCE",
                    "error_message": f"Recovery failed: {type(exc).__name__}",
                    "updated_at": datetime.now(UTC),
                }
            )
            self.repository.save_manifest(failed, equity=persisted.initial_cash)
            report = PaperRecoveryReport(
                session_id=session_id,
                status="RECOVERY_DIVERGENCE",
                journal_event_count=len(self.repository.read_journal(session_id)),
                broker_event_count=len(self.repository.read_broker_events(session_id)),
                recorded_portfolio_hash=(
                    "unavailable" if recorded is None else recorded.portfolio_hash
                ),
                recovered_portfolio_hash="unavailable",
                recorded_trace_hash=(
                    "unavailable" if recorded is None else recorded.trace_semantic_hash
                ),
                recovered_trace_hash="unavailable",
                broker_reconciled=False,
                account_reconciled=False,
                warnings=(
                    f"Recovery failed with {type(exc).__name__}.",
                    "Session was not resumed automatically.",
                ),
            )
            self.repository.save_recovery_report(report)
            self._record_operation(
                session_id,
                "RECOVERY_DIVERGENCE",
                "Recovery could not rebuild a matching runtime state",
                {"error_type": type(exc).__name__},
            )
            return report

    def create_account(self, request: CreatePaperAccount) -> PaperAccount:
        now = datetime.now(UTC)
        account = PaperAccount(
            account_id=self.repository.new_account_id(),
            name=request.name.strip(),
            currency=request.currency,
            initial_cash=request.initial_cash,
            cash=request.initial_cash,
            positions={},
            equity=request.initial_cash,
            cumulative_fees=0.0,
            cumulative_slippage=0.0,
            active_session_id=None,
            created_at=now,
            updated_at=now,
        )
        self.repository.create_account(account)
        return account

    def get_account(self, account_id: str) -> PaperAccount:
        return self.repository.get_account(account_id)

    def list_accounts(self) -> PaperAccountList:
        return PaperAccountList(items=self.repository.list_accounts())

    def _ensure_manifest_account(self, manifest: PaperSessionManifest) -> PaperSessionManifest:
        if manifest.account_id:
            try:
                self.repository.get_account(manifest.account_id)
                return manifest
            except PaperSessionNotFoundError:
                pass
        account = self.create_account(
            CreatePaperAccount(
                name=f"Recovered {manifest.strategy_name}", initial_cash=manifest.initial_cash
            )
        )
        return manifest.model_copy(update={"account_id": account.account_id})

    @staticmethod
    def _default_adapter(manifest: PaperSessionManifest) -> MarketDataAdapter:
        if manifest.provider == "fake":
            return FakeLiveMarketDataAdapter(feed=manifest.feed)
        credentials = integration_vault.resolve_alpaca()
        return AlpacaStockMarketDataAdapter(
            api_key=None if credentials is None else credentials.api_key,
            secret_key=None if credentials is None else credentials.secret_key,
            feed=manifest.feed,
        )

    @staticmethod
    def _default_broker_adapter(_: PaperSessionManifest) -> PaperBrokerAdapter:
        credentials = integration_vault.resolve_alpaca()
        if credentials is None:
            raise RuntimeError("Alpaca Paper credentials are not configured")
        return AlpacaPaperBrokerAdapter(credentials.api_key, credentials.secret_key)

    def _recover_all(self) -> None:
        # Reconcile chronologically so the newest session for an account owns
        # the final durable balance and active-session pointer.
        for stored in reversed(self.repository.list_manifests()):
            self._recover_session(stored, explicit=False)

    def _active_session_id_for_account(
        self, account_id: str, *, exclude_session_id: str | None = None
    ) -> str | None:
        active = tuple(
            manifest
            for manifest in self.repository.list_manifests()
            if manifest.account_id == account_id
            and manifest.session_id != exclude_session_id
            and manifest.status in {"CREATED", "RUNNING", "PAUSED"}
        )
        return None if not active else max(active, key=lambda item: item.created_at).session_id

    def create(self, request: CreatePaperSession) -> PaperSessionSnapshot:
        if (
            request.securities
            and tuple(item.symbol.upper() for item in request.securities) != request.symbols
        ):
            raise ValueError("Security metadata must match the requested symbols in order")
        loaded = self.registry.load(request.strategy_id)
        strategy = loaded.strategy_class()
        definitions = {item.name: item for item in strategy.parameter_definitions()}
        unknown = sorted(set(request.parameters) - set(definitions))
        if unknown:
            raise ValueError(f"Unknown strategy parameters: {', '.join(unknown)}")
        resolved_parameters = {
            name: request.parameters.get(name, definition.default)
            for name, definition in definitions.items()
        }
        strategy.configure(resolved_parameters)
        requirements = strategy.metadata.data_requirements
        if (
            requirements.symbol_count is not None
            and len(request.symbols) != requirements.symbol_count
        ):
            raise ValueError(
                f"Strategy requires {requirements.symbol_count} symbol(s); "
                f"received {len(request.symbols)}"
            )
        if requirements.symbols and request.symbols != requirements.symbols:
            raise ValueError("Strategy requires symbols " + ", ".join(requirements.symbols))
        account = (
            self.create_account(
                CreatePaperAccount(
                    name=f"{strategy.metadata.name} Paper", initial_cash=request.initial_cash
                )
            )
            if request.account_id is None
            else self.repository.get_account(request.account_id)
        )
        active_session_id = self._active_session_id_for_account(account.account_id)
        if active_session_id is not None:
            raise ValueError(
                f"Paper account '{account.account_id}' already has active session "
                f"'{active_session_id}'"
            )
        now = datetime.now(UTC)
        session_id = self.repository.new_session_id()
        manifest = PaperSessionManifest(
            session_id=session_id,
            account_id=account.account_id,
            status="CREATED",
            feed_status="DISCONNECTED",
            strategy_id=strategy.metadata.strategy_id,
            strategy_name=strategy.metadata.name,
            strategy_version=strategy.metadata.version,
            strategy_class_name=loaded.strategy_class.__name__,
            strategy_fingerprint=loaded.source_fingerprint,
            symbols=request.symbols,
            securities=request.securities,
            parameters=resolved_parameters,
            provider=request.provider,
            feed=request.feed,
            timeframe=request.timeframe,
            market_session=request.market_session,
            initial_cash=account.cash,
            fee_bps=request.fee_bps,
            slippage_bps=request.slippage_bps,
            execution_mode=request.execution_mode,
            broker_status=(
                "DISCONNECTED" if request.execution_mode == "ALPACA_PAPER" else "NOT_USED"
            ),
            created_at=now,
            updated_at=now,
        )
        self.repository.create(manifest, loaded.source_path.read_bytes())
        self._record_operation(
            session_id,
            "CREATED",
            "Paper session created",
            {
                "account_id": account.account_id,
                "execution_mode": request.execution_mode,
                "provider": request.provider,
                "feed": request.feed,
            },
        )
        session = LivePaperSession(manifest, str(self.repository.strategy_path(session_id)))
        session.manifest = manifest.model_copy(update={"checkpoint": session.checkpoint()})
        self.repository.save_trace(session_id, session.trace())
        self.repository.save_manifest(session.manifest, equity=account.cash)
        checkpoint = session.checkpoint()
        self.repository.save_recovery_report(
            PaperRecoveryReport(
                session_id=session_id,
                status="READY",
                journal_event_count=0,
                broker_event_count=0,
                recorded_portfolio_hash=checkpoint.portfolio_hash,
                recovered_portfolio_hash=checkpoint.portfolio_hash,
                recorded_trace_hash=checkpoint.trace_semantic_hash,
                recovered_trace_hash=checkpoint.trace_semantic_hash,
                broker_reconciled=request.execution_mode == "VQD_SIMULATED",
                account_reconciled=True,
            )
        )
        self._sessions[session_id] = session
        self._locks[session_id] = asyncio.Lock()
        self.repository.save_account(
            account.model_copy(update={"active_session_id": session_id, "updated_at": now})
        )
        return session.snapshot()

    def _session(self, session_id: str) -> LivePaperSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise PaperSessionNotFoundError(session_id)
        return session

    def _snapshot(self, session: LivePaperSession) -> PaperSessionSnapshot:
        session_id = session.manifest.session_id
        return session.snapshot().model_copy(
            update={
                "orders": self.repository.list_orders(session_id),
                "fills": self.repository.list_fills(session_id),
            }
        )

    def get(self, session_id: str) -> PaperSessionSnapshot:
        return self._snapshot(self._session(session_id))

    def list(self) -> PaperSessionList:
        ordered = sorted(
            self._sessions.values(), key=lambda item: item.manifest.created_at, reverse=True
        )
        return PaperSessionList(items=tuple(self._snapshot(item) for item in ordered))

    def trace(self, session_id: str) -> PaperTrace:
        return self._session(session_id).trace()

    def operations(self, session_id: str) -> PaperOperationLog:
        self._session(session_id)
        return PaperOperationLog(items=self.repository.read_operations(session_id))

    def recovery(self, session_id: str) -> PaperRecoveryReport:
        self._session(session_id)
        return self.repository.load_recovery_report(session_id)

    def health(self, session_id: str) -> PaperOperationalHealth:
        session = self._session(session_id)
        snapshot = self._snapshot(session)
        operations = self.repository.read_operations(session_id)
        last_received_at = snapshot.last_received_at
        stale_seconds = (
            0.0
            if last_received_at is None
            else max(0.0, (datetime.now(UTC) - last_received_at).total_seconds())
        )
        open_orders = tuple(
            order for order in snapshot.orders if order.status in _OPEN_ORDER_STATUSES
        )
        broker_account = snapshot.broker_account
        return PaperOperationalHealth(
            session_id=session_id,
            status=snapshot.status,
            feed_status=snapshot.feed_status,
            broker_status=snapshot.broker_status,
            recovery_status=snapshot.recovery_status,
            last_received_at=last_received_at,
            last_market_event=snapshot.last_market_event,
            last_latency_ms=(
                None
                if not snapshot.recent_market_events
                else snapshot.recent_market_events[-1].latency_ms
            ),
            stale_seconds=stale_seconds,
            reconnect_count=sum(item.operation_type == "FEED_RECONNECTING" for item in operations),
            backfill_count=sum(item.operation_type == "BACKFILL_COMPLETED" for item in operations),
            backfilled_bar_count=sum(
                self._operation_int_metadata(item, "bar_count")
                for item in operations
                if item.operation_type == "BACKFILL_COMPLETED"
            ),
            open_order_count=len(open_orders),
            partially_filled_order_count=sum(
                order.status == "PARTIALLY_FILLED" for order in snapshot.orders
            ),
            broker_account_status=None if broker_account is None else broker_account.status,
            broker_cash=None if broker_account is None else broker_account.cash,
            broker_equity=None if broker_account is None else broker_account.equity,
            broker_buying_power=(None if broker_account is None else broker_account.buying_power),
            rejected_order_count=sum(order.status == "REJECTED" for order in snapshot.orders),
            last_broker_event_at=(
                None
                if not snapshot.recent_broker_events
                else snapshot.recent_broker_events[-1].received_at
            ),
        )

    async def recover(self, session_id: str) -> PaperRecoveryReport:
        session = self._session(session_id)
        if session.manifest.status != "ERROR":
            raise ValueError(f"Cannot recover a {session.manifest.status} session")
        if session.manifest.recovery_status != "RECOVERY_DIVERGENCE":
            raise ValueError("This session does not have a recoverable divergence report")
        self._assert_account_available(session)
        report = self._recover_session(self.repository.load_manifest(session_id), explicit=True)
        recovered = self._session(session_id)
        if report.status == "RECOVERED" and recovered.manifest.execution_mode == "ALPACA_PAPER":
            try:
                async with self._locks[session_id]:
                    await self._refresh_broker_state(recovered)
                    self._persist(recovered)
                report = report.model_copy(update={"broker_reconciled": True})
                self.repository.save_recovery_report(report)
                self._record_operation(
                    session_id,
                    "BROKER_RECONCILIATION",
                    "Broker account and open orders reconciled after recovery",
                )
            except Exception as exc:
                recovered.mark_error("BROKER_RECONCILIATION_FAILED", str(exc))
                self._persist(recovered)
                report = report.model_copy(
                    update={
                        "status": "RECOVERY_DIVERGENCE",
                        "broker_reconciled": False,
                        "warnings": report.warnings
                        + ("Broker reconciliation did not complete; session remains stopped.",),
                    }
                )
                self.repository.save_recovery_report(report)
                self._record_operation(
                    session_id,
                    "RECOVERY_DIVERGENCE",
                    "Broker reconciliation did not complete",
                    {"error_type": type(exc).__name__},
                )
        await self._publish(session_id)
        return report

    def register_adapter(self, session_id: str, adapter: MarketDataAdapter) -> None:
        self._session(session_id)
        self._adapters[session_id] = adapter

    def register_broker_adapter(self, session_id: str, adapter: PaperBrokerAdapter) -> None:
        session = self._session(session_id)
        if session.manifest.execution_mode != "ALPACA_PAPER":
            raise ValueError("Broker adapters are only valid for Alpaca Paper sessions")
        self._broker_adapters[session_id] = adapter

    def _broker_adapter(self, session: LivePaperSession) -> PaperBrokerAdapter:
        session_id = session.manifest.session_id
        adapter = self._broker_adapters.get(session_id)
        if adapter is None:
            adapter = self._broker_adapter_factory(session.manifest)
            self._broker_adapters[session_id] = adapter
        return adapter

    def _persist(self, session: LivePaperSession) -> None:
        checkpoint = session.checkpoint()
        session.manifest = session.manifest.model_copy(
            update={"checkpoint": checkpoint, "updated_at": datetime.now(UTC)}
        )
        snapshot = session.snapshot()
        self.repository.save_trace(session.manifest.session_id, session.trace())
        self.repository.save_manifest(session.manifest, equity=snapshot.account.equity)
        self._reconcile_account(session)

    def _reconcile_account(self, session: LivePaperSession) -> PaperAccount:
        stored = self.repository.get_account(session.manifest.account_id)
        snapshot = session.snapshot().account
        active = (
            session.manifest.session_id
            if session.manifest.status in {"CREATED", "RUNNING", "PAUSED"}
            else None
        )
        reconciled = stored.model_copy(
            update={
                "cash": snapshot.cash,
                "positions": snapshot.positions,
                "equity": snapshot.equity,
                "cumulative_fees": snapshot.cumulative_fees,
                "cumulative_slippage": snapshot.cumulative_slippage,
                "active_session_id": active,
                "updated_at": datetime.now(UTC),
            }
        )
        self.repository.save_account(reconciled)
        verified = self.repository.get_account(stored.account_id)
        if (
            verified.cash != snapshot.cash
            or verified.positions != snapshot.positions
            or verified.equity != snapshot.equity
            or verified.cumulative_fees != snapshot.cumulative_fees
            or verified.cumulative_slippage != snapshot.cumulative_slippage
        ):
            raise RuntimeError("Paper account reconciliation failed")
        return verified

    def _assert_account_available(self, session: LivePaperSession) -> None:
        account = self.repository.get_account(session.manifest.account_id)
        conflicting_session_id = self._active_session_id_for_account(
            account.account_id, exclude_session_id=session.manifest.session_id
        )
        if conflicting_session_id is not None:
            raise ValueError(
                f"Paper account '{account.account_id}' is already owned by active session "
                f"'{conflicting_session_id}'"
            )

    def _assert_account_ownership(self, session: LivePaperSession) -> None:
        self._assert_account_available(session)
        account = self.repository.get_account(session.manifest.account_id)
        if account.active_session_id != session.manifest.session_id:
            self.repository.save_account(
                account.model_copy(
                    update={
                        "active_session_id": session.manifest.session_id,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )

    async def _publish(self, session_id: str) -> None:
        payload = self.get(session_id).model_dump_json()
        for queue in tuple(self._subscribers.get(session_id, set())):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(payload)

    async def start(self, session_id: str, *, launch_task: bool = True) -> PaperSessionSnapshot:
        session = self._session(session_id)
        if session.manifest.status != "CREATED":
            raise ValueError(f"Cannot start a {session.manifest.status} session")
        if session.manifest.recovery_status != "READY":
            raise ValueError(
                f"Cannot start while recovery status is {session.manifest.recovery_status}"
            )
        self._assert_account_ownership(session)
        if session.manifest.execution_mode == "ALPACA_PAPER":
            try:
                broker = self._broker_adapter(session)
                account = await broker.account()
                if account.trading_blocked:
                    raise ValueError("Alpaca Paper account is currently blocked from trading")
                session.broker_account = account
                session.manifest = session.manifest.model_copy(
                    update={"broker_status": "CONNECTED"}
                )
            except Exception as exc:
                session.manifest = session.manifest.model_copy(
                    update={
                        "broker_status": "ERROR",
                        "error_code": "BROKER_CONNECTION_FAILED",
                        "error_message": str(exc),
                    }
                )
                self._persist(session)
                self._record_operation(
                    session_id,
                    "ERROR",
                    "Broker connection prevented the session from starting",
                    {"error_type": type(exc).__name__},
                )
                raise ValueError("Could not connect to the Alpaca Paper Broker") from exc
        session.set_status("RUNNING")
        self._persist(session)
        self._record_operation(session_id, "STARTED", "Paper session started")
        if launch_task:
            self._start_task(session_id)
            self._start_broker_task(session_id)
        await self._publish(session_id)
        return self._snapshot(session)

    async def pause(self, session_id: str) -> PaperSessionSnapshot:
        session = self._session(session_id)
        if session.manifest.status != "RUNNING":
            raise ValueError(f"Cannot pause a {session.manifest.status} session")
        session.set_status("PAUSED")
        self._persist(session)
        self._record_operation(
            session_id,
            "PAUSED",
            "Strategy decisions and new orders paused; market ingestion remains active",
        )
        await self._publish(session_id)
        return self._snapshot(session)

    async def resume(self, session_id: str) -> PaperSessionSnapshot:
        session = self._session(session_id)
        if session.manifest.status != "PAUSED":
            raise ValueError(f"Cannot resume a {session.manifest.status} session")
        if session.manifest.recovery_status != "READY":
            raise ValueError(
                f"Cannot resume while recovery status is {session.manifest.recovery_status}"
            )
        self._assert_account_ownership(session)
        session.set_status("RUNNING")
        self._persist(session)
        self._record_operation(
            session_id,
            "RESUMED",
            "Strategy decisions resumed from the current market stream",
        )
        self._start_task(session_id)
        self._start_broker_task(session_id)
        await self._publish(session_id)
        return self._snapshot(session)

    async def stop(self, session_id: str) -> PaperSessionSnapshot:
        session = self._session(session_id)
        if session.manifest.status not in {"CREATED", "RUNNING", "PAUSED", "ERROR"}:
            raise ValueError(f"Cannot stop a {session.manifest.status} session")
        self._record_operation(session_id, "STOP_REQUESTED", "Stop requested")
        if session.manifest.execution_mode == "ALPACA_PAPER":
            await self._cancel_open_broker_orders(session)
        session.set_status("STOPPED", feed_status="DISCONNECTED")
        task = self._tasks.pop(session_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        adapter = self._adapters.get(session_id)
        if adapter is not None:
            await adapter.disconnect()
        broker_task = self._broker_tasks.pop(session_id, None)
        if broker_task is not None and broker_task is not asyncio.current_task():
            broker_task.cancel()
        broker = self._broker_adapters.pop(session_id, None)
        if broker is not None:
            await broker.close()
        if session.manifest.execution_mode == "ALPACA_PAPER":
            session.manifest = session.manifest.model_copy(update={"broker_status": "DISCONNECTED"})
        self._persist(session)
        try:
            report = self.runtime_consistency(session_id)
        except Exception as exc:
            report = RuntimeConsistencyReport(
                session_id=session_id,
                status="FIRST_RUNTIME_DIVERGENCE",
                compared_event_count=len(session.trace().timeline),
                first_divergence_layer="DECISION",
                message=(
                    "Recorded Feed Replay failed during the consistency check: "
                    f"{type(exc).__name__}"
                ),
            )
        self.repository.save_consistency_report(report)
        if session.manifest.research_run_id is None:
            run_id = self._archive_paper_run(session, report)
            session.manifest = session.manifest.model_copy(update={"research_run_id": run_id})
            self._persist(session)
        if session.manifest.reference_run_id is None:
            try:
                reference_trace = self.recorded_reference_trace(session_id)
            except Exception:
                reference_trace = None
            reference_run_id = self._archive_paper_run(
                session,
                report,
                run_type="REFERENCE",
                trace_override=reference_trace,
            )
            session.manifest = session.manifest.model_copy(
                update={"reference_run_id": reference_run_id}
            )
            self._persist(session)
        self._record_operation(
            session_id,
            "STOPPED",
            "Paper session stopped and research evidence archived",
            {
                "paper_run_archived": session.manifest.research_run_id is not None,
                "reference_run_archived": session.manifest.reference_run_id is not None,
            },
        )
        await self._publish(session_id)
        return self._snapshot(session)

    def runtime_consistency(self, session_id: str) -> RuntimeConsistencyReport:
        expected_session = self._session(session_id)
        replay = LivePaperSession(
            expected_session.manifest,
            str(self.repository.strategy_path(session_id)),
        )
        broker_events = self.repository.read_broker_events(session_id)
        broker_index = 0
        for entry in self.repository.read_journal(session_id):
            replay.apply_entry(entry)
            while (
                broker_index < len(broker_events)
                and broker_events[broker_index].market_sequence <= entry.sequence
            ):
                replay.apply_broker_event(broker_events[broker_index])
                broker_index += 1
        while broker_index < len(broker_events):
            replay.apply_broker_event(broker_events[broker_index])
            broker_index += 1
        expected = expected_session.research_trace()
        actual = replay.research_trace()
        evaluated_ids = tuple(
            entry.market_event_id
            for entry in replay.journal
            if entry.disposition in {"EVALUATED", "EVALUATION_SKIPPED_PAUSED"}
        )
        if (expected is None) != (actual is None):
            return RuntimeConsistencyReport(
                session_id=session_id,
                status="FIRST_RUNTIME_DIVERGENCE",
                compared_event_count=0,
                first_divergence_layer="DECISION",
                message="Recorded Feed Replay produced a different timeline shape",
            )
        if expected is not None and actual is not None:
            layers = (
                ("DECISION", "signal_evaluation"),
                ("ORDER", "order_events"),
                ("EXECUTION", "execution_events"),
                ("POSITION", "position_snapshot"),
                ("FEES", "cost_snapshot"),
                ("EQUITY", "pnl_snapshot"),
            )
            for index, (left, right) in enumerate(
                zip(expected.timeline, actual.timeline, strict=False)
            ):
                for layer, field in layers:
                    if getattr(left, field) != getattr(right, field):
                        return RuntimeConsistencyReport(
                            session_id=session_id,
                            status="FIRST_RUNTIME_DIVERGENCE",
                            compared_event_count=index,
                            first_divergence_layer=cast(
                                Literal[
                                    "DECISION",
                                    "ORDER",
                                    "EXECUTION",
                                    "POSITION",
                                    "FEES",
                                    "EQUITY",
                                ],
                                layer,
                            ),
                            first_divergence_event_id=(
                                evaluated_ids[index] if index < len(evaluated_ids) else None
                            ),
                            message=f"First runtime divergence at {layer.lower()}",
                        )
            expected_order_count = sum(len(item.order_events) for item in actual.timeline)
            expected_fill_count = (
                sum(item.fill_quantity > 0 for item in broker_events)
                if expected_session.manifest.execution_mode == "ALPACA_PAPER"
                else sum(len(item.execution_events) for item in actual.timeline)
            )
            if len(self.repository.list_orders(session_id)) != expected_order_count:
                return RuntimeConsistencyReport(
                    session_id=session_id,
                    status="FIRST_RUNTIME_DIVERGENCE",
                    compared_event_count=len(actual.timeline),
                    first_divergence_layer="ORDER",
                    message="Persistent order ledger differs from Recorded Feed Replay",
                )
            if len(self.repository.list_fills(session_id)) != expected_fill_count:
                return RuntimeConsistencyReport(
                    session_id=session_id,
                    status="FIRST_RUNTIME_DIVERGENCE",
                    compared_event_count=len(actual.timeline),
                    first_divergence_layer="EXECUTION",
                    message="Persistent fill ledger differs from Recorded Feed Replay",
                )
        account = self.repository.get_account(expected_session.manifest.account_id)
        replay_account = replay.snapshot().account
        if account.positions != replay_account.positions:
            return RuntimeConsistencyReport(
                session_id=session_id,
                status="FIRST_RUNTIME_DIVERGENCE",
                compared_event_count=len(replay.runtime.rows),
                first_divergence_layer="POSITION",
                message="Paper account positions differ from Recorded Feed Replay",
            )
        if account.equity != replay_account.equity:
            return RuntimeConsistencyReport(
                session_id=session_id,
                status="FIRST_RUNTIME_DIVERGENCE",
                compared_event_count=len(replay.runtime.rows),
                first_divergence_layer="EQUITY",
                message="Paper account equity differs from Recorded Feed Replay",
            )
        return RuntimeConsistencyReport(
            session_id=session_id,
            status="MATCH",
            compared_event_count=len(replay.runtime.rows),
            message=(
                "Recorded Feed Replay matches decisions, orders, executions, positions, "
                "fees, and equity"
            ),
        )

    def recorded_reference_trace(self, session_id: str) -> BacktestTrace | None:
        expected_session = self._session(session_id)
        manifest = expected_session.manifest
        if manifest.execution_mode == "ALPACA_PAPER":
            manifest = manifest.model_copy(
                update={
                    "execution_mode": "VQD_SIMULATED",
                    "broker_status": "NOT_USED",
                }
            )
        replay = LivePaperSession(
            manifest,
            str(self.repository.strategy_path(session_id)),
        )
        for entry in self.repository.read_journal(session_id):
            replay.apply_entry(entry)
        return replay.research_trace()

    def _archive_paper_run(
        self,
        session: LivePaperSession,
        report: RuntimeConsistencyReport,
        *,
        run_type: Literal["PAPER", "REFERENCE"] = "PAPER",
        trace_override: BacktestTrace | None = None,
    ) -> str:
        trace = session.research_trace() if run_type == "PAPER" else trace_override
        run_id = self.run_repository.new_run_id()
        now = datetime.now(UTC)
        strategy_source = self.repository.strategy_path(session.manifest.session_id).read_bytes()
        journal_path = (
            self.repository.session_directory(session.manifest.session_id) / "market-events.jsonl"
        )
        market_events = journal_path.read_bytes()
        broker_events_path = (
            self.repository.session_directory(session.manifest.session_id) / "broker-events.jsonl"
        )
        broker_events = broker_events_path.read_bytes() if broker_events_path.is_file() else b""
        report_payload = (report.model_dump_json(indent=2) + "\n").encode()
        trace_payload = None if trace is None else _trace_payload(trace)
        dataset_fingerprint = sha256_bytes(market_events)
        run_fingerprint = sha256_bytes(
            json.dumps(
                {
                    "run_type": run_type,
                    "session_id": session.manifest.session_id,
                    "strategy": session.manifest.strategy_fingerprint,
                    "dataset": dataset_fingerprint,
                    "parameters": session.manifest.parameters,
                    "execution_mode": session.manifest.execution_mode,
                    "broker_events": sha256_bytes(broker_events),
                },
                sort_keys=True,
            ).encode()
        )
        execution = (
            ExecutionModelRevision(
                execution_model_id="alpaca-paper-market",
                version="1.0",
                description=(
                    "recorded close(t) decision; market order lifecycle and fills owned "
                    "by Alpaca Paper Broker"
                ),
            )
            if session.manifest.execution_mode == "ALPACA_PAPER" and run_type == "PAPER"
            else ExecutionModelRevision(
                execution_model_id="paper-next-close",
                version="1.0",
                description="recorded close(t) decision; local simulated close(t+1) fill",
            )
        )
        running = RunManifest(
            run_id=run_id,
            run_type=run_type,
            run_fingerprint=run_fingerprint,
            status="RUNNING",
            created_at=session.manifest.created_at,
            completed_at=None,
            strategy=StrategyRevision(
                strategy_id=session.manifest.strategy_id,
                name=session.manifest.strategy_name,
                version=session.manifest.strategy_version,
                class_name=session.manifest.strategy_class_name,
                source_fingerprint=sha256_bytes(strategy_source),
                original_source_path=str(
                    self.repository.strategy_path(session.manifest.session_id)
                ),
            ),
            dataset=DatasetRevision(
                dataset_id=f"recorded-feed:{session.manifest.session_id}",
                name=(
                    f"Recorded Feed · {session.manifest.provider.upper()} "
                    f"{session.manifest.feed.upper()}"
                ),
                content_fingerprint=dataset_fingerprint,
                source_timezone="UTC",
                symbols=session.manifest.symbols,
            ),
            period=ResearchPeriod(
                start=None if trace is None else trace.metadata.data_start,
                end=None if trace is None else trace.metadata.data_end,
                cutoff=None,
            ),
            parameters={
                **session.manifest.parameters,
                "initial_cash": session.manifest.initial_cash,
                "fee_bps": session.manifest.fee_bps,
                "slippage_bps": session.manifest.slippage_bps,
            },
            execution_model=execution,
            engine=EnvironmentSnapshot(
                python_version=platform.python_version(),
                platform=platform.platform(),
                vqd_version=VQD_ENGINE_VERSION,
            ),
            artifacts=ArtifactHashes(
                strategy_source_sha256=sha256_bytes(strategy_source),
                trace_sha256=None if trace_payload is None else sha256_bytes(trace_payload),
                recorded_market_events_sha256=dataset_fingerprint,
                runtime_consistency_sha256=sha256_bytes(report_payload),
                broker_events_sha256=(
                    sha256_bytes(broker_events)
                    if session.manifest.execution_mode == "ALPACA_PAPER" and run_type == "PAPER"
                    else None
                ),
            ),
        )
        self.run_repository.create_running(running, strategy_source)
        self.run_repository.write_paper_artifacts(
            run_id,
            market_events,
            report_payload,
            broker_events=(
                broker_events
                if session.manifest.execution_mode == "ALPACA_PAPER" and run_type == "PAPER"
                else None
            ),
        )
        completed = running.model_copy(
            update={
                "status": "COMPLETED",
                "completed_at": now,
                "trace_id": None if trace is None else _trace_id(run_id, trace),
                "metrics": None if trace is None else _run_metrics(trace),
            }
        )
        self.run_repository.finalize(completed, trace)
        return run_id

    async def ingest(self, session_id: str, bar: MarketBar) -> PaperSessionSnapshot:
        session = self._session(session_id)
        if session.manifest.status not in {"RUNNING", "PAUSED"}:
            raise ValueError(f"Cannot ingest into a {session.manifest.status} session")
        if bar.provider != session.manifest.provider or bar.feed != session.manifest.feed:
            raise ValueError("Market event provider/feed does not match the paper session")
        async with self._locks[session_id]:
            _, disposition = session.classify(bar)
            entry = MarketJournalEntry(
                sequence=len(session.journal) + 1,
                market_event_id=self._market_event_id(bar),
                disposition=disposition,
                bar=bar,
            )
            self.repository.append_session_journal(session_id, entry)
            try:
                session.apply_entry(entry)
            except Exception as exc:
                session.mark_error(type(exc).__name__, str(exc))
                self._persist(session)
                self._record_operation(
                    session_id,
                    "ERROR",
                    "Strategy runtime failed while applying a durable market event",
                    {"error_type": type(exc).__name__},
                )
                await self._publish(session_id)
                raise
            if disposition in {"EVALUATED", "EVALUATION_SKIPPED_PAUSED"}:
                await self._persist_runtime_records(session, entry.market_event_id)
            if disposition not in {"DUPLICATE_IGNORED", "OUT_OF_ORDER_REJECTED"}:
                session.manifest = session.manifest.model_copy(
                    update={"last_market_event": bar.event_time}
                )
            self._persist(session)
        await self._publish(session_id)
        return self._snapshot(session)

    @staticmethod
    def _client_order_id(session: LivePaperSession, runtime_order_id: str) -> str:
        return f"vqd-{session.manifest.session_id.removeprefix('paper-')}-{runtime_order_id}"[:128]

    @staticmethod
    def _broker_event_id(update: BrokerOrderUpdate) -> str:
        identity = "|".join(
            (
                update.provider_order_id,
                update.raw_status,
                f"{update.filled_quantity:.12g}",
                update.updated_at.isoformat(),
            )
        ).encode()
        return f"broker-{hashlib.sha256(identity).hexdigest()[:24]}"

    @staticmethod
    def _incremental_fill_price(
        previous_quantity: float,
        previous_average: float | None,
        update: BrokerOrderUpdate,
    ) -> float | None:
        delta = update.filled_quantity - previous_quantity
        if delta <= 1e-12 or update.average_fill_price is None:
            return None
        previous_notional = previous_quantity * (previous_average or 0.0)
        return (update.filled_quantity * update.average_fill_price - previous_notional) / delta

    async def _apply_broker_update(
        self,
        session: LivePaperSession,
        update: BrokerOrderUpdate,
        *,
        market_event_id: str | None = None,
    ) -> None:
        orders = self.repository.list_orders(session.manifest.session_id)
        existing = next(
            (
                item
                for item in orders
                if item.provider_order_id == update.provider_order_id
                or item.client_order_id == update.client_order_id
            ),
            None,
        )
        if existing is None:
            return
        event_id = self._broker_event_id(update)
        if any(item.event_id == event_id for item in session.broker_events):
            return
        fill_quantity = max(0.0, update.filled_quantity - existing.filled_quantity)
        fill_price = self._incremental_fill_price(
            existing.filled_quantity, existing.average_fill_price, update
        )
        execution_id = (
            None
            if fill_quantity <= 1e-12
            else f"alpaca:{update.provider_order_id}:{update.filled_quantity:.12g}"
        )
        occurred_at = update.terminal_at or update.updated_at
        event = PaperBrokerEvent(
            event_id=event_id,
            sequence=len(session.broker_events) + 1,
            market_sequence=len(session.journal),
            session_id=session.manifest.session_id,
            event_type=(
                "fill"
                if fill_quantity > 0 and update.status == "FILLED"
                else "partial_fill"
                if fill_quantity > 0
                else update.raw_status
            ),
            order_id=existing.order_id,
            provider_order_id=update.provider_order_id,
            client_order_id=update.client_order_id,
            status=update.status,
            raw_status=update.raw_status,
            symbol=update.symbol,
            side=update.side,
            ordered_quantity=update.ordered_quantity,
            filled_quantity=update.filled_quantity,
            fill_quantity=fill_quantity,
            fill_price=fill_price,
            reference_price=existing.reference_price or 0.0,
            execution_id=execution_id,
            occurred_at=occurred_at,
            received_at=datetime.now(UTC),
            message=update.rejection_reason,
        )
        self.repository.append_broker_event(session.manifest.session_id, event)
        session.apply_broker_event(event)
        updated_order = existing.model_copy(
            update={
                "market_event_id": market_event_id or existing.market_event_id,
                "provider_order_id": update.provider_order_id,
                "status": update.status,
                "raw_status": update.raw_status,
                "filled_quantity": update.filled_quantity,
                "average_fill_price": update.average_fill_price,
                "updated_at": update.updated_at,
                "terminal_at": update.terminal_at,
                "rejection_reason": update.rejection_reason,
            }
        )
        fills: tuple[PaperFill, ...] = ()
        if execution_id is not None and fill_price is not None:
            direction = 1.0 if update.side == "BUY" else -1.0
            slippage = (
                fill_quantity * direction * (fill_price - (existing.reference_price or fill_price))
            )
            fills = (
                PaperFill(
                    fill_id=f"{session.manifest.session_id}:fill:{execution_id}",
                    execution_id=f"{session.manifest.session_id}:{execution_id}",
                    order_id=existing.order_id,
                    source_order_id=existing.order_id,
                    account_id=session.manifest.account_id,
                    session_id=session.manifest.session_id,
                    market_event_id=market_event_id or existing.market_event_id,
                    symbol=update.symbol,
                    side=update.side,
                    quantity=fill_quantity,
                    reference_price=existing.reference_price or fill_price,
                    fill_price=fill_price,
                    traded_notional=fill_quantity * fill_price,
                    fee=0.0,
                    slippage=slippage,
                    executed_at=occurred_at,
                    provider="alpaca",
                    provider_execution_id=execution_id,
                ),
            )
        self.repository.save_orders_and_fills((updated_order,), fills)
        self._record_operation(
            session.manifest.session_id,
            "BROKER_RECONCILIATION",
            "Broker order state reconciled",
            {
                "order_id": updated_order.order_id,
                "status": updated_order.status,
                "fill_quantity": fill_quantity,
            },
        )

    async def _persist_runtime_records(
        self, session: LivePaperSession, market_event_id: str
    ) -> None:
        if not session.runtime.rows:
            return
        row = session.runtime.rows[-1]
        order_ids = {item.order_id for item in row.orders}
        orders = tuple(
            PaperOrder(
                order_id=f"{session.manifest.session_id}:{item.order_id}",
                account_id=session.manifest.account_id,
                session_id=session.manifest.session_id,
                market_event_id=market_event_id,
                source_signal_id=item.source_signal_id,
                source_decision_id=item.source_signal_id,
                source_intent_id=item.source_signal_id,
                symbol=item.symbol,
                side=item.side,
                quantity=item.quantity,
                submitted_at=item.submitted_at,
                status="CREATED" if session.manifest.execution_mode == "ALPACA_PAPER" else "FILLED",
                execution_mode=session.manifest.execution_mode,
                provider="alpaca" if session.manifest.execution_mode == "ALPACA_PAPER" else "vqd",
                client_order_id=(
                    self._client_order_id(session, item.order_id)
                    if session.manifest.execution_mode == "ALPACA_PAPER"
                    else None
                ),
                raw_status=(
                    "created" if session.manifest.execution_mode == "ALPACA_PAPER" else "filled"
                ),
                filled_quantity=(
                    0.0 if session.manifest.execution_mode == "ALPACA_PAPER" else item.quantity
                ),
                reference_price=float(row.market[item.symbol]["close"]),
                updated_at=item.submitted_at,
            )
            for item in row.orders
        )
        fills = tuple(
            PaperFill(
                fill_id=f"{session.manifest.session_id}:fill:{item.execution_id}",
                execution_id=f"{session.manifest.session_id}:{item.execution_id}",
                order_id=f"{session.manifest.session_id}:{item.source_order_id}",
                source_order_id=f"{session.manifest.session_id}:{item.source_order_id}",
                account_id=session.manifest.account_id,
                session_id=session.manifest.session_id,
                market_event_id=market_event_id,
                symbol=item.symbol,
                side=item.side,
                quantity=item.quantity,
                reference_price=item.expected_price,
                fill_price=item.fill_price,
                traded_notional=item.traded_notional,
                fee=item.fee,
                slippage=item.slippage,
                executed_at=item.executed_at,
            )
            for item in row.executions
            if item.source_order_id in order_ids
        )
        self.repository.save_orders_and_fills(orders, fills)
        if session.manifest.execution_mode != "ALPACA_PAPER":
            return
        broker = self._broker_adapter(session)
        for order in orders:
            try:
                update = await broker.submit_market_order(
                    BrokerOrderRequest(
                        client_order_id=order.client_order_id or order.order_id,
                        symbol=order.symbol,
                        side=order.side,
                        quantity=order.quantity,
                        reference_price=order.reference_price or 0.0,
                        submitted_at=order.submitted_at,
                    )
                )
                await self._apply_broker_update(session, update, market_event_id=market_event_id)
            except httpx.HTTPStatusError as exc:
                detail = "Alpaca rejected the Paper order request"
                with suppress(ValueError):
                    payload = exc.response.json()
                    if isinstance(payload, dict) and payload.get("message"):
                        detail = str(payload["message"])
                rejected = PaperBrokerEvent(
                    event_id=(
                        "broker-"
                        + hashlib.sha256((order.order_id + detail).encode()).hexdigest()[:24]
                    ),
                    sequence=len(session.broker_events) + 1,
                    market_sequence=len(session.journal),
                    session_id=session.manifest.session_id,
                    event_type="rejected",
                    order_id=order.order_id,
                    client_order_id=order.client_order_id or order.order_id,
                    status="REJECTED",
                    raw_status="http_rejected",
                    symbol=order.symbol,
                    side=order.side,
                    ordered_quantity=order.quantity,
                    filled_quantity=0.0,
                    reference_price=order.reference_price or 0.0,
                    occurred_at=datetime.now(UTC),
                    received_at=datetime.now(UTC),
                    message=detail,
                )
                self.repository.append_broker_event(session.manifest.session_id, rejected)
                session.apply_broker_event(rejected)
                self.repository.save_orders_and_fills(
                    (
                        order.model_copy(
                            update={
                                "status": "REJECTED",
                                "raw_status": "http_rejected",
                                "updated_at": rejected.received_at,
                                "terminal_at": rejected.occurred_at,
                                "rejection_reason": detail,
                            }
                        ),
                    ),
                    (),
                )
            except httpx.RequestError:
                # The request may have reached Alpaca even though the response was
                # lost. Keep the durable CREATED order and reconcile by the unique
                # client_order_id instead of risking a duplicate submission.
                session.manifest = session.manifest.model_copy(
                    update={"broker_status": "RECONNECTING"}
                )

    def _adapter(self, session: LivePaperSession) -> MarketDataAdapter:
        return self._adapters.setdefault(
            session.manifest.session_id, self._adapter_factory(session.manifest)
        )

    async def _reconcile_broker_order(self, session: LivePaperSession, order: PaperOrder) -> None:
        broker = self._broker_adapter(session)
        try:
            if order.provider_order_id:
                update = await broker.get_order(order.provider_order_id)
            elif order.client_order_id:
                try:
                    update = await broker.get_order_by_client_id(order.client_order_id)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        raise
                    update = await broker.submit_market_order(
                        BrokerOrderRequest(
                            client_order_id=order.client_order_id,
                            symbol=order.symbol,
                            side=order.side,
                            quantity=order.quantity,
                            reference_price=order.reference_price or 0.0,
                            submitted_at=order.submitted_at,
                        )
                    )
            else:
                return
            await self._apply_broker_update(session, update)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {404, 422}:
                raise

    async def _refresh_broker_state(self, session: LivePaperSession) -> None:
        broker = self._broker_adapter(session)
        previous_status = session.manifest.broker_status
        session.broker_account = await broker.account()
        for order in self.repository.list_orders(session.manifest.session_id):
            if order.status == "CREATED" or order.status not in TERMINAL_BROKER_STATUSES:
                await self._reconcile_broker_order(session, order)
        session.manifest = session.manifest.model_copy(
            update={
                "broker_status": "CONNECTED",
                "error_code": None,
                "error_message": None,
            }
        )
        if previous_status != "CONNECTED":
            self._record_operation(
                session.manifest.session_id,
                "BROKER_RECONCILIATION",
                "Broker account connection and open orders reconciled",
                {"previous_status": previous_status},
            )
        try:
            recovery_report = self.repository.load_recovery_report(session.manifest.session_id)
        except PaperSessionNotFoundError:
            recovery_report = None
        if (
            recovery_report is not None
            and recovery_report.status != "RECOVERY_DIVERGENCE"
            and not recovery_report.broker_reconciled
        ):
            self.repository.save_recovery_report(
                recovery_report.model_copy(update={"broker_reconciled": True})
            )

    async def _cancel_open_broker_orders(self, session: LivePaperSession) -> None:
        broker = self._broker_adapter(session)
        for order in self.repository.list_orders(session.manifest.session_id):
            if order.status in TERMINAL_BROKER_STATUSES:
                continue
            if order.provider_order_id:
                await broker.cancel_order(order.provider_order_id)
                await self._reconcile_broker_order(session, order)

    async def cancel_order(self, session_id: str, order_id: str) -> PaperSessionSnapshot:
        session = self._session(session_id)
        if session.manifest.execution_mode != "ALPACA_PAPER":
            raise ValueError("Only Alpaca Paper Broker orders can be cancelled")
        order = next(
            (item for item in self.repository.list_orders(session_id) if item.order_id == order_id),
            None,
        )
        if order is None:
            raise PaperSessionNotFoundError(order_id)
        if order.status in TERMINAL_BROKER_STATUSES:
            raise ValueError(f"Cannot cancel a {order.status} order")
        if order.provider_order_id is None:
            raise ValueError("The order has not been acknowledged by Alpaca yet")
        async with self._locks[session_id]:
            await self._broker_adapter(session).cancel_order(order.provider_order_id)
            await self._reconcile_broker_order(session, order)
            self._persist(session)
        await self._publish(session_id)
        return self._snapshot(session)

    def _start_broker_task(self, session_id: str) -> None:
        session = self._session(session_id)
        if session.manifest.execution_mode != "ALPACA_PAPER":
            return
        existing = self._broker_tasks.get(session_id)
        if existing is None or existing.done():
            self._broker_tasks[session_id] = asyncio.create_task(
                self._run_broker(session_id), name=f"vqd-broker-{session_id}"
            )

    async def _run_broker(self, session_id: str) -> None:
        delay = 1.0
        while self._session(session_id).manifest.status in {"RUNNING", "PAUSED"}:
            session = self._session(session_id)
            try:
                async with self._locks[session_id]:
                    await self._refresh_broker_state(session)
                    self._persist(session)
                await self._publish(session_id)
                delay = 1.0
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                session.manifest = session.manifest.model_copy(
                    update={
                        "broker_status": "RECONNECTING",
                        "error_code": "BROKER_RECONNECTING",
                        "error_message": (
                            f"Alpaca Paper reconciliation is retrying: {type(exc).__name__}"
                        ),
                    }
                )
                self._persist(session)
                await self._publish(session_id)
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 30.0)

    async def _is_regular_bar(
        self, session: LivePaperSession, bar: MarketBar, adapter: MarketDataAdapter
    ) -> bool:
        if session.manifest.market_session != "US_REGULAR" or not isinstance(
            adapter, AlpacaStockMarketDataAdapter
        ):
            return True
        date = bar.event_time.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        calendar = await adapter.market_clock.calendar(date, date)
        return any(item.open <= bar.event_time < item.close for item in calendar)

    async def backfill_gap(
        self, session_id: str, adapter: MarketDataAdapter, current_time: datetime
    ) -> tuple[MarketBar, ...]:
        session = self._session(session_id)
        last = session.market_store.last_completed_time
        if last is None:
            return ()
        start = last + timedelta(minutes=1)
        end = current_time.replace(second=0, microsecond=0) - timedelta(minutes=1)
        if start > end:
            return ()
        self._record_operation(
            session_id,
            "BACKFILL_STARTED",
            "Market data gap backfill started",
            {"start": start.isoformat(), "end": end.isoformat()},
        )
        bars = await adapter.historical_bars(session.manifest.symbols, start, end)
        accepted: list[MarketBar] = []
        for bar in bars:
            if await self._is_regular_bar(session, bar, adapter):
                await self.ingest(session_id, bar)
                accepted.append(bar)
        self._record_operation(
            session_id,
            "BACKFILL_COMPLETED",
            "Market data gap backfill completed",
            {"bar_count": len(accepted)},
        )
        return tuple(accepted)

    async def reconnect_once(
        self, session_id: str, *, current_time: datetime
    ) -> tuple[MarketBar, ...]:
        session = self._session(session_id)
        adapter = self._adapter(session)
        session.manifest = session.manifest.model_copy(update={"feed_status": "RECONNECTING"})
        self._persist(session)
        self._record_operation(
            session_id,
            "FEED_RECONNECTING",
            "Market data feed reconnect started",
        )
        await adapter.connect()
        backfilled = await self.backfill_gap(session_id, adapter, current_time)
        await adapter.subscribe(session.manifest.symbols)
        session.manifest = session.manifest.model_copy(
            update={
                "feed_status": "CONNECTED",
                "error_code": None,
                "error_message": None,
            }
        )
        self._persist(session)
        self._record_operation(
            session_id,
            "FEED_RECONNECTED",
            "Market data feed reconnected",
            {"backfilled_bar_count": len(backfilled)},
        )
        await self._publish(session_id)
        return backfilled

    def _start_task(self, session_id: str) -> None:
        existing = self._tasks.get(session_id)
        if existing is None or existing.done():
            self._tasks[session_id] = asyncio.create_task(
                self._run_feed(session_id), name=f"vqd-{session_id}"
            )

    async def _run_feed(self, session_id: str) -> None:
        delay = 1.0
        while self._session(session_id).manifest.status in {"RUNNING", "PAUSED"}:
            session = self._session(session_id)
            adapter = self._adapter(session)
            try:
                session.manifest = session.manifest.model_copy(
                    update={"feed_status": "RECONNECTING"}
                )
                self._persist(session)
                self._record_operation(
                    session_id,
                    "FEED_RECONNECTING",
                    "Market data feed connection attempt started",
                )
                await self._publish(session_id)
                await adapter.connect()
                if isinstance(adapter, AlpacaStockMarketDataAdapter):
                    session.market_clock = await adapter.market_clock.current()
                    await self.backfill_gap(session_id, adapter, session.market_clock.timestamp)
                await adapter.subscribe(session.manifest.symbols)
                session.manifest = session.manifest.model_copy(
                    update={
                        "feed_status": "CONNECTED",
                        "error_code": None,
                        "error_message": None,
                    }
                )
                self._persist(session)
                self._record_operation(
                    session_id,
                    "FEED_RECONNECTED",
                    "Market data feed connected",
                )
                await self._publish(session_id)
                delay = 1.0
                iterator = adapter.events().__aiter__()
                next_bar: asyncio.Future[MarketBar] = asyncio.ensure_future(iterator.__anext__())
                while session.manifest.status in {"RUNNING", "PAUSED"}:
                    done, _ = await asyncio.wait({next_bar}, timeout=90.0)
                    if not done:
                        if isinstance(adapter, AlpacaStockMarketDataAdapter):
                            session.market_clock = await adapter.market_clock.current()
                            feed_status = "STALE" if session.market_clock.is_open else "CONNECTED"
                            session.manifest = session.manifest.model_copy(
                                update={"feed_status": feed_status}
                            )
                            self._persist(session)
                            await self._publish(session_id)
                        continue
                    bar = next_bar.result()
                    next_bar = asyncio.ensure_future(iterator.__anext__())
                    if await self._is_regular_bar(session, bar, adapter):
                        await self.ingest(session_id, bar)
                next_bar.cancel()
                return
            except asyncio.CancelledError:
                await adapter.disconnect()
                raise
            except Exception as exc:
                # Strategy/runtime failures are terminal. Reconnecting the market feed
                # would replay the same durable input forever and hide the real cause.
                if session.manifest.status == "ERROR":
                    await adapter.disconnect()
                    await self._publish(session_id)
                    return
                session.manifest = session.manifest.model_copy(
                    update={
                        "feed_status": "RECONNECTING",
                        "error_code": "MARKET_DATA_GAP",
                        "error_message": (
                            "Market data gap detected; reconnect/backfill in progress: "
                            f"{type(exc).__name__}"
                        ),
                    }
                )
                self._persist(session)
                self._record_operation(
                    session_id,
                    "FEED_DISCONNECTED",
                    "Market data feed disconnected; retry is scheduled",
                    {"error_type": type(exc).__name__},
                )
                await self._publish(session_id)
                await adapter.disconnect()
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 30.0)

    async def start_recovered_tasks(self) -> None:
        for session_id, session in self._sessions.items():
            if (
                session.manifest.status in {"RUNNING", "PAUSED"}
                and session.manifest.recovery_status == "READY"
            ):
                self._start_task(session_id)
                self._start_broker_task(session_id)

    async def shutdown(self) -> None:
        tasks = (*self._tasks.values(), *self._broker_tasks.values())
        self._tasks.clear()
        self._broker_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        brokers = tuple(self._broker_adapters.values())
        self._broker_adapters.clear()
        if brokers:
            await asyncio.gather(*(broker.close() for broker in brokers), return_exceptions=True)

    async def stream(self, session_id: str) -> AsyncIterator[str]:
        self._session(session_id)
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        subscribers = self._subscribers.setdefault(session_id, set())
        subscribers.add(queue)
        try:
            yield self.get(session_id).model_dump_json()
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ""
        finally:
            subscribers.discard(queue)


class PaperSessionSupervisor(PaperSessionService):
    """Process-owned lifecycle supervisor for persistent paper sessions."""


class PaperStore:
    def __init__(self) -> None:
        self.repository = PaperSessionRepository()
        self.service = PaperSessionSupervisor(self.repository)

    def use_workspace(
        self,
        workspace_root: str | Path,
        *,
        registry: StrategyRegistry | None = None,
        adapter_factory: AdapterFactory | None = None,
        broker_adapter_factory: BrokerAdapterFactory | None = None,
    ) -> PaperSessionService:
        self.repository = PaperSessionRepository(workspace_root)
        self.service = PaperSessionSupervisor(
            self.repository,
            registry=registry,
            adapter_factory=adapter_factory,
            broker_adapter_factory=broker_adapter_factory,
        )
        return self.service


paper_store = PaperStore()
