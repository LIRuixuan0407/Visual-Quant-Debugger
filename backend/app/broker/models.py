from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BrokerOrderStatus = Literal[
    "SUBMITTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "PENDING_CANCEL",
    "CANCELLED",
    "REJECTED",
    "EXPIRED",
    "DONE_FOR_DAY",
    "REPLACED",
    "HELD",
    "SUSPENDED",
    "UNKNOWN",
]


class BrokerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BrokerOrderRequest(BrokerModel):
    client_order_id: str = Field(min_length=1, max_length=128)
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0)
    reference_price: float = Field(gt=0)
    submitted_at: datetime


class BrokerOrderUpdate(BrokerModel):
    provider: Literal["alpaca"] = "alpaca"
    provider_order_id: str
    client_order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    ordered_quantity: float
    filled_quantity: float
    average_fill_price: float | None = None
    status: BrokerOrderStatus
    raw_status: str
    submitted_at: datetime
    updated_at: datetime
    terminal_at: datetime | None = None
    rejection_reason: str | None = None


class BrokerAccountSnapshot(BrokerModel):
    provider: Literal["alpaca"] = "alpaca"
    account_id: str
    status: str
    currency: str
    cash: float
    equity: float
    buying_power: float
    portfolio_value: float
    trading_blocked: bool


TERMINAL_BROKER_STATUSES: frozenset[BrokerOrderStatus] = frozenset(
    {"FILLED", "CANCELLED", "REJECTED", "EXPIRED", "REPLACED"}
)
