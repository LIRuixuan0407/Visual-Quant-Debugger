from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.broker import BrokerAccountSnapshot, BrokerOrderStatus
from app.market_data.models import MarketBar, MarketClockSnapshot, MarketDataConnectionState
from app.trace.models import Diagnostic, TimelineEvent, TraceScalar

PaperSessionStatus = Literal["CREATED", "RUNNING", "PAUSED", "STOPPED", "ERROR"]
PaperExecutionMode = Literal["VQD_SIMULATED", "ALPACA_PAPER"]
PaperMarketSession = Literal["CN_REGULAR", "HK_REGULAR", "US_REGULAR"]
BrokerConnectionStatus = Literal["NOT_USED", "DISCONNECTED", "CONNECTED", "RECONNECTING", "ERROR"]
RecoveryStatus = Literal["READY", "RECOVERING", "RECOVERY_DIVERGENCE"]
RecoveryReportStatus = Literal["READY", "RECOVERED", "RECOVERY_DIVERGENCE"]
PaperOperationType = Literal[
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
]
JournalDisposition = Literal[
    "BUFFERED",
    "EVALUATED",
    "EVALUATION_SKIPPED_PAUSED",
    "CORRECTION_APPLIED",
    "DUPLICATE_IGNORED",
    "OUT_OF_ORDER_REJECTED",
]


class PaperModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PaperSecurity(PaperModel):
    symbol: str
    name: str
    exchange: str
    status: Literal["active", "inactive"] = "active"


class CreatePaperSession(PaperModel):
    account_id: str | None = None
    strategy_id: str
    symbols: tuple[str, ...]
    securities: tuple[PaperSecurity, ...] = ()
    parameters: dict[str, int | float] = Field(default_factory=dict)
    provider: Literal["tdx", "alpaca", "fake"] = "alpaca"
    feed: Literal["tdx", "iex", "sip"] = "iex"
    timeframe: Literal["1Min"] = "1Min"
    market_session: PaperMarketSession = "US_REGULAR"
    initial_cash: float = Field(default=100_000.0, gt=0)
    fee_bps: float = Field(default=5.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)
    execution_mode: PaperExecutionMode = "VQD_SIMULATED"

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        symbols = tuple(dict.fromkeys(item.strip().upper() for item in value if item.strip()))
        if not symbols:
            raise ValueError("At least one symbol is required")
        return symbols


class RecoveryCheckpoint(PaperModel):
    last_event_sequence: int
    last_processed_market_event_id: str | None = None
    market_watermark: datetime | None = None
    portfolio_hash: str
    trace_semantic_hash: str


class PaperOperationEvent(PaperModel):
    operation_id: str
    sequence: int = Field(ge=1)
    session_id: str
    operation_type: PaperOperationType
    occurred_at: datetime
    message: str
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class PaperOperationLog(PaperModel):
    items: tuple[PaperOperationEvent, ...]


class PaperRecoveryReport(PaperModel):
    session_id: str
    status: RecoveryReportStatus
    journal_event_count: int = Field(ge=0)
    broker_event_count: int = Field(ge=0)
    recorded_portfolio_hash: str
    recovered_portfolio_hash: str
    recorded_trace_hash: str
    recovered_trace_hash: str
    broker_reconciled: bool
    account_reconciled: bool
    warnings: tuple[str, ...] = ()


class PaperOperationalHealth(PaperModel):
    session_id: str
    status: PaperSessionStatus
    feed_status: MarketDataConnectionState
    broker_status: BrokerConnectionStatus
    recovery_status: RecoveryStatus
    last_received_at: datetime | None
    last_market_event: datetime | None
    last_latency_ms: float | None
    stale_seconds: float = Field(ge=0)
    reconnect_count: int = Field(ge=0)
    backfill_count: int = Field(ge=0)
    backfilled_bar_count: int = Field(ge=0)
    open_order_count: int = Field(ge=0)
    partially_filled_order_count: int = Field(ge=0)
    broker_account_status: str | None = None
    broker_cash: float | None = None
    broker_equity: float | None = None
    broker_buying_power: float | None = None
    rejected_order_count: int = Field(ge=0)
    last_broker_event_at: datetime | None = None


class PaperSessionManifest(PaperModel):
    session_id: str
    account_id: str = ""
    session_version: Literal["1.0"] = "1.0"
    status: PaperSessionStatus
    feed_status: MarketDataConnectionState
    recovery_status: RecoveryStatus = "READY"
    strategy_id: str
    strategy_name: str
    strategy_version: str
    strategy_class_name: str
    strategy_fingerprint: str
    symbols: tuple[str, ...]
    securities: tuple[PaperSecurity, ...] = ()
    parameters: dict[str, int | float]
    provider: str
    feed: str
    timeframe: Literal["1Min"] = "1Min"
    market_session: PaperMarketSession = "US_REGULAR"
    initial_cash: float
    fee_bps: float
    slippage_bps: float
    execution_mode: PaperExecutionMode = "VQD_SIMULATED"
    broker_status: BrokerConnectionStatus = "NOT_USED"
    created_at: datetime
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    updated_at: datetime
    last_market_event: datetime | None = None
    checkpoint: RecoveryCheckpoint | None = None
    research_run_id: str | None = None
    reference_run_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class MarketJournalEntry(PaperModel):
    sequence: int = Field(ge=1)
    market_event_id: str = ""
    disposition: JournalDisposition
    bar: MarketBar


class PaperMarketEvent(PaperModel):
    sequence: int
    market_event_id: str
    disposition: JournalDisposition
    symbol: str
    event_time: datetime
    available_at: datetime
    received_at: datetime
    close: float
    revision: int
    is_correction: bool
    latency_ms: float


class MarketRevisionNotice(PaperModel):
    symbol: str
    event_time: datetime
    used_revision: int
    used_close: float
    later_revision: int
    later_close: float
    revision_available_at: datetime


class PaperPendingOrder(PaperModel):
    source_signal_id: str
    due_market_index: int
    target_positions: dict[str, float]


class PaperExecution(PaperModel):
    execution_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    fill_price: float
    fee: float
    slippage: float
    executed_at: datetime


class CreatePaperAccount(PaperModel):
    name: str = Field(default="Paper Account", min_length=1, max_length=120)
    initial_cash: float = Field(default=100_000.0, gt=0)
    currency: Literal["CNY", "HKD", "USD"] = "USD"


class PaperAccount(PaperModel):
    account_id: str
    name: str
    currency: Literal["CNY", "HKD", "USD"] = "USD"
    initial_cash: float
    cash: float
    positions: dict[str, float]
    equity: float
    cumulative_fees: float
    cumulative_slippage: float
    active_session_id: str | None
    created_at: datetime
    updated_at: datetime


class PaperAccountList(PaperModel):
    items: tuple[PaperAccount, ...]


class PaperOrder(PaperModel):
    order_id: str
    account_id: str
    session_id: str
    market_event_id: str
    source_signal_id: str
    source_decision_id: str
    source_intent_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    submitted_at: datetime
    status: Literal["CREATED"] | BrokerOrderStatus = "FILLED"
    execution_mode: PaperExecutionMode = "VQD_SIMULATED"
    provider: str = "vqd"
    provider_order_id: str | None = None
    client_order_id: str | None = None
    raw_status: str = "filled"
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    reference_price: float | None = None
    updated_at: datetime | None = None
    terminal_at: datetime | None = None
    rejection_reason: str | None = None


class PaperFill(PaperModel):
    fill_id: str
    execution_id: str
    order_id: str
    source_order_id: str
    account_id: str
    session_id: str
    market_event_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    reference_price: float
    fill_price: float
    traded_notional: float
    fee: float
    slippage: float
    executed_at: datetime
    provider: str = "vqd"
    provider_execution_id: str | None = None


class PaperBrokerEvent(PaperModel):
    event_id: str
    sequence: int = Field(ge=1)
    market_sequence: int = Field(ge=0)
    session_id: str
    provider: Literal["alpaca"] = "alpaca"
    event_type: str
    order_id: str
    provider_order_id: str | None = None
    client_order_id: str
    status: Literal["CREATED"] | BrokerOrderStatus
    raw_status: str
    symbol: str
    side: Literal["BUY", "SELL"]
    ordered_quantity: float
    filled_quantity: float
    fill_quantity: float = 0.0
    fill_price: float | None = None
    reference_price: float
    execution_id: str | None = None
    occurred_at: datetime
    received_at: datetime
    message: str | None = None


class RuntimeConsistencyReport(PaperModel):
    report_version: Literal["1.0"] = "1.0"
    session_id: str
    status: Literal["MATCH", "FIRST_RUNTIME_DIVERGENCE"]
    compared_event_count: int
    first_divergence_layer: (
        Literal["DECISION", "ORDER", "EXECUTION", "POSITION", "FEES", "EQUITY"] | None
    ) = None
    first_divergence_event_id: str | None = None
    message: str


class PaperAccountSnapshot(PaperModel):
    cash: float
    positions: dict[str, float]
    equity: float
    net_pnl: float
    cumulative_fees: float
    cumulative_slippage: float
    max_drawdown: float
    pending_orders: tuple[PaperPendingOrder, ...]
    executions: tuple[PaperExecution, ...]


class PaperSessionSnapshot(PaperModel):
    session_id: str
    account_id: str
    status: PaperSessionStatus
    feed_status: MarketDataConnectionState
    recovery_status: RecoveryStatus
    strategy_id: str
    strategy_name: str
    strategy_fingerprint: str
    symbols: tuple[str, ...]
    securities: tuple[PaperSecurity, ...] = ()
    parameters: dict[str, int | float]
    provider: str
    feed: str
    timeframe: Literal["1Min"]
    market_session: PaperMarketSession
    initial_cash: float
    created_at: datetime
    started_at: datetime | None
    stopped_at: datetime | None
    last_market_event: datetime | None
    last_received_at: datetime | None
    last_event_sequence: int
    last_processed_market_event_id: str | None
    market_watermark: datetime | None
    evaluated_bar_count: int
    correction_count: int
    duplicate_count: int
    out_of_order_count: int
    market_clock: MarketClockSnapshot | None
    account: PaperAccountSnapshot
    recent_market_events: tuple[PaperMarketEvent, ...]
    recent_revisions: tuple[MarketRevisionNotice, ...]
    latest_event: TimelineEvent | None
    error_code: str | None
    error_message: str | None
    research_run_id: str | None
    reference_run_id: str | None = None
    orders: tuple[PaperOrder, ...] = ()
    fills: tuple[PaperFill, ...] = ()
    execution_mode: PaperExecutionMode = "VQD_SIMULATED"
    broker_status: BrokerConnectionStatus = "NOT_USED"
    broker_account: BrokerAccountSnapshot | None = None
    recent_broker_events: tuple[PaperBrokerEvent, ...] = ()


class PaperSessionList(PaperModel):
    items: tuple[PaperSessionSnapshot, ...]


class PaperTrace(PaperModel):
    trace_version: Literal["1.0"] = "1.0"
    session_id: str
    strategy_id: str
    parameters: dict[str, TraceScalar]
    timeline: tuple[TimelineEvent, ...]
    diagnostics: tuple[Diagnostic, ...]
    market_revisions: tuple[MarketRevisionNotice, ...]
    execution_mode: PaperExecutionMode = "VQD_SIMULATED"
    broker_events: tuple[PaperBrokerEvent, ...] = ()
