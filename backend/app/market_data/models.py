from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MarketDataConnectionState = Literal["CONNECTED", "RECONNECTING", "STALE", "DISCONNECTED"]
MarketRegion = Literal["CN", "HK", "US"]
MarketSession = Literal["CN_REGULAR", "HK_REGULAR", "US_REGULAR"]
MarketDataFeed = Literal["tdx", "iex", "sip"]
MarketDataTimeframe = Literal["1Min", "5Min", "15Min", "1Hour", "1Day"]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Market-data timestamps must be timezone-aware")
    return value


class MarketDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketBar(MarketDataModel):
    symbol: str
    timeframe: MarketDataTimeframe = "1Min"
    event_time: datetime
    available_at: datetime
    received_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)
    provider: str
    feed: str
    provider_event_id: str
    revision: int = Field(default=1, ge=1)
    is_correction: bool = False

    _aware_event = field_validator("event_time")(_aware)
    _aware_available = field_validator("available_at")(_aware)
    _aware_received = field_validator("received_at")(_aware)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        return normalized

    @model_validator(mode="after")
    def validate_ohlc_and_time(self) -> MarketBar:
        if self.available_at < self.event_time:
            raise ValueError("available_at cannot precede event_time")
        if self.received_at < self.available_at:
            raise ValueError("received_at cannot precede available_at")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.low > self.high:
            raise ValueError("low cannot exceed high")
        if self.is_correction and self.revision < 2:
            raise ValueError("A correction must have revision >= 2")
        return self

    @property
    def identity(self) -> tuple[str, datetime, int]:
        return self.symbol, self.event_time, self.revision

    @property
    def latency_ms(self) -> float:
        return (self.received_at - self.event_time).total_seconds() * 1000.0


class ProviderStatus(MarketDataModel):
    provider: str
    configured: bool
    feeds: tuple[str, ...]
    selected_feed: str
    timeframe: Literal["1Min"] = "1Min"
    market_session: MarketSession = "US_REGULAR"
    markets: tuple[MarketRegion, ...] = ("US",)
    requires_credentials: bool = True
    supports_live: bool = True
    supports_historical: bool = True
    note: str | None = None


class StockSecurity(MarketDataModel):
    symbol: str
    name: str
    exchange: str
    status: Literal["active", "inactive"]
    tradable: bool
    fractionable: bool = False
    market: MarketRegion = "US"
    currency: Literal["CNY", "HKD", "USD"] = "USD"
    lot_size: int = Field(default=1, ge=1)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        return normalized


class StockSnapshot(MarketDataModel):
    security: StockSecurity
    provider: str = "alpaca"
    feed: MarketDataFeed
    market_timestamp: datetime
    received_at: datetime
    latest_trade_price: float | None = None
    latest_trade_size: float | None = None
    minute_bar: MarketBar | None = None
    daily_bar: MarketBar | None = None
    market: MarketRegion = "US"
    freshness_status: Literal["LIVE", "DELAYED", "STALE", "CLOSED", "UNVERIFIED"] = "UNVERIFIED"
    freshness_seconds: float | None = Field(default=None, ge=0)

    _aware_market = field_validator("market_timestamp")(_aware)
    _aware_received = field_validator("received_at")(_aware)


class HistoricalBarsRequest(MarketDataModel):
    symbols: tuple[str, ...]
    start: datetime
    end: datetime
    timeframe: MarketDataTimeframe = "1Day"
    provider: Literal["tdx", "alpaca"] = "alpaca"
    market: MarketRegion = "US"
    feed: MarketDataFeed = "iex"
    adjustment: Literal["NONE", "QFQ", "HFQ"] = "NONE"

    _aware_times = field_validator("start", "end")(_aware)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(item.strip().upper() for item in value if item.strip()))
        if not normalized:
            raise ValueError("At least one symbol is required")
        return normalized

    @model_validator(mode="after")
    def validate_period(self) -> HistoricalBarsRequest:
        if self.end <= self.start:
            raise ValueError("Historical end must be after start")
        return self


class MarketClockSnapshot(MarketDataModel):
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime
    session: MarketSession = "US_REGULAR"

    _aware_timestamp = field_validator("timestamp")(_aware)
    _aware_next_open = field_validator("next_open")(_aware)
    _aware_next_close = field_validator("next_close")(_aware)


class CalendarSession(MarketDataModel):
    date: str
    open: datetime
    close: datetime

    _aware_open = field_validator("open")(_aware)
    _aware_close = field_validator("close")(_aware)
