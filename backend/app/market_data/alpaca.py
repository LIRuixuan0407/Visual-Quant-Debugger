from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx
from websockets.asyncio.client import ClientConnection, connect

from app.market_data.adapter import MarketDataAdapter
from app.market_data.models import (
    CalendarSession,
    MarketBar,
    MarketClockSnapshot,
    MarketDataConnectionState,
    MarketDataTimeframe,
    ProviderStatus,
    StockSecurity,
    StockSnapshot,
)

ALPACA_STREAM_BASE = "wss://stream.data.alpaca.markets/v2"
ALPACA_DATA_BASE = "https://data.alpaca.markets/v2"
ALPACA_TRADING_BASE = "https://paper-api.alpaca.markets/v2"
ALPACA_ALLOWED_FEEDS = ("iex", "sip")


def _alpaca_headers(api_key: str, secret_key: str) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }


def _parse_time(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class AlpacaStockReferenceClient:
    """Read-only client for the Alpaca US-equity catalog and market-data API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self._secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")

    def _headers(self) -> dict[str, str]:
        if not self._api_key or not self._secret_key:
            raise RuntimeError("Alpaca credentials are not configured")
        return _alpaca_headers(self._api_key, self._secret_key)

    @staticmethod
    def _security(payload: Mapping[str, object]) -> StockSecurity:
        return StockSecurity(
            symbol=str(payload["symbol"]),
            name=str(payload.get("name") or payload["symbol"]),
            exchange=str(payload.get("exchange") or "UNKNOWN"),
            status="active" if payload.get("status") == "active" else "inactive",
            tradable=bool(payload.get("tradable", False)),
            fractionable=bool(payload.get("fractionable", False)),
        )

    async def search(self, query: str, *, limit: int = 20) -> tuple[StockSecurity, ...]:
        needle = query.strip().casefold()
        if not needle:
            return ()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{ALPACA_TRADING_BASE}/assets",
                headers=self._headers(),
                params={"status": "active", "asset_class": "us_equity"},
            )
            response.raise_for_status()
        matches = (
            self._security(item)
            for item in cast(list[dict[str, object]], response.json())
            if needle in str(item.get("symbol", "")).casefold()
            or needle in str(item.get("name", "")).casefold()
            or needle in str(item.get("exchange", "")).casefold()
        )
        return tuple(item for item in matches if item.tradable)[:limit]

    async def get_security(self, symbol: str) -> StockSecurity:
        normalized = symbol.strip().upper()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{ALPACA_TRADING_BASE}/assets/{normalized}", headers=self._headers()
            )
            response.raise_for_status()
        return self._security(cast(dict[str, object], response.json()))

    @staticmethod
    def _snapshot_bar(
        symbol: str,
        payload: Mapping[str, object] | None,
        *,
        feed: str,
        timeframe: MarketDataTimeframe,
        received_at: datetime,
    ) -> MarketBar | None:
        if not payload:
            return None
        event_time = _parse_time(payload["t"])
        return MarketBar(
            symbol=symbol,
            timeframe=timeframe,
            event_time=event_time,
            available_at=received_at,
            received_at=received_at,
            open=float(cast(float, payload["o"])),
            high=float(cast(float, payload["h"])),
            low=float(cast(float, payload["l"])),
            close=float(cast(float, payload["c"])),
            volume=float(cast(float, payload["v"])),
            provider="alpaca",
            feed=feed,
            provider_event_id=f"snapshot:{symbol}:{event_time.isoformat()}",
        )

    async def snapshot(self, symbol: str, *, feed: str = "iex") -> StockSnapshot:
        normalized = symbol.strip().upper()
        if feed not in ALPACA_ALLOWED_FEEDS:
            raise ValueError("Alpaca stock feed must be 'iex' or 'sip'")
        security = await self.get_security(normalized)
        received = datetime.now(UTC)
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{ALPACA_DATA_BASE}/stocks/{normalized}/snapshot",
                headers=self._headers(),
                params={"feed": feed},
            )
            response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        trade = cast(dict[str, object] | None, payload.get("latestTrade"))
        minute = self._snapshot_bar(
            normalized,
            cast(dict[str, object] | None, payload.get("minuteBar")),
            feed=feed,
            timeframe="1Min",
            received_at=received,
        )
        daily = self._snapshot_bar(
            normalized,
            cast(dict[str, object] | None, payload.get("dailyBar")),
            feed=feed,
            timeframe="1Day",
            received_at=received,
        )
        timestamps = [
            item
            for item in (
                None if trade is None else _parse_time(trade["t"]),
                None if minute is None else minute.event_time,
                None if daily is None else daily.event_time,
            )
            if item is not None
        ]
        return StockSnapshot(
            security=security,
            feed=cast(Any, feed),
            market_timestamp=max(timestamps) if timestamps else received,
            received_at=received,
            latest_trade_price=None if trade is None else float(cast(float, trade["p"])),
            latest_trade_size=None if trade is None else float(cast(float, trade["s"])),
            minute_bar=minute,
            daily_bar=daily,
        )

    async def historical_bars(
        self,
        symbols: tuple[str, ...],
        start: datetime,
        end: datetime,
        *,
        timeframe: MarketDataTimeframe = "1Day",
        feed: str = "iex",
    ) -> tuple[MarketBar, ...]:
        if feed not in ALPACA_ALLOWED_FEEDS:
            raise ValueError("Alpaca stock feed must be 'iex' or 'sip'")
        received = datetime.now(UTC)
        bars: list[MarketBar] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for raw_symbol in symbols:
                symbol = raw_symbol.strip().upper()
                page_token: str | None = None
                while True:
                    params: dict[str, str | int] = {
                        "timeframe": timeframe,
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "feed": feed,
                        "limit": 10000,
                        "sort": "asc",
                    }
                    if page_token:
                        params["page_token"] = page_token
                    response = await client.get(
                        f"{ALPACA_DATA_BASE}/stocks/{symbol}/bars",
                        headers=self._headers(),
                        params=params,
                    )
                    response.raise_for_status()
                    payload = cast(dict[str, Any], response.json())
                    for item in cast(list[dict[str, object]], payload.get("bars", [])):
                        event_time = _parse_time(item["t"])
                        bars.append(
                            MarketBar(
                                symbol=symbol,
                                timeframe=timeframe,
                                event_time=event_time,
                                available_at=received,
                                received_at=received,
                                open=float(cast(float, item["o"])),
                                high=float(cast(float, item["h"])),
                                low=float(cast(float, item["l"])),
                                close=float(cast(float, item["c"])),
                                volume=float(cast(float, item["v"])),
                                provider="alpaca",
                                feed=feed,
                                provider_event_id=f"rest:{symbol}:{event_time.isoformat()}:r1",
                            )
                        )
                    page_token = cast(str | None, payload.get("next_page_token"))
                    if not page_token:
                        break
        return tuple(sorted(bars, key=lambda item: (item.event_time, item.symbol)))


def alpaca_provider_status(
    *, api_key: str | None = None, secret_key: str | None = None, feed: str | None = None
) -> ProviderStatus:
    selected = (feed or os.environ.get("ALPACA_DATA_FEED", "iex")).strip().lower()
    if selected not in ALPACA_ALLOWED_FEEDS:
        selected = "iex"
    return ProviderStatus(
        provider="alpaca",
        configured=bool(
            (api_key or os.environ.get("ALPACA_API_KEY"))
            and (secret_key or os.environ.get("ALPACA_SECRET_KEY"))
        ),
        feeds=ALPACA_ALLOWED_FEEDS,
        selected_feed=selected,
    )


class AlpacaMarketClock:
    def __init__(self, api_key: str, secret_key: str) -> None:
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }

    async def current(self) -> MarketClockSnapshot:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{ALPACA_TRADING_BASE}/clock", headers=self._headers)
            response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        return MarketClockSnapshot(
            timestamp=datetime.fromisoformat(str(payload["timestamp"]).replace("Z", "+00:00")),
            is_open=bool(payload["is_open"]),
            next_open=datetime.fromisoformat(str(payload["next_open"]).replace("Z", "+00:00")),
            next_close=datetime.fromisoformat(str(payload["next_close"]).replace("Z", "+00:00")),
        )

    async def calendar(self, start: str, end: str) -> tuple[CalendarSession, ...]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{ALPACA_TRADING_BASE}/calendar",
                headers=self._headers,
                params={"start": start, "end": end},
            )
            response.raise_for_status()
        sessions: list[CalendarSession] = []
        for item in cast(list[dict[str, Any]], response.json()):
            date = str(item["date"])
            market_timezone = ZoneInfo("America/New_York")
            sessions.append(
                CalendarSession(
                    date=date,
                    open=datetime.fromisoformat(f"{date}T{item['open']}:00").replace(
                        tzinfo=market_timezone
                    ),
                    close=datetime.fromisoformat(f"{date}T{item['close']}:00").replace(
                        tzinfo=market_timezone
                    ),
                )
            )
        return tuple(sessions)


class AlpacaStockMarketDataAdapter(MarketDataAdapter):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        secret_key: str | None = None,
        feed: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self._secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        selected = (feed or os.environ.get("ALPACA_DATA_FEED", "iex")).lower()
        if selected not in ALPACA_ALLOWED_FEEDS:
            raise ValueError("Alpaca stock feed must be 'iex' or 'sip'")
        self._feed = selected
        self._state: MarketDataConnectionState = "DISCONNECTED"
        self._socket: ClientConnection | None = None
        self._symbols: tuple[str, ...] = ()
        self._revisions: dict[tuple[str, datetime], int] = {}

    @property
    def provider(self) -> str:
        return "alpaca"

    @property
    def feed(self) -> str:
        return self._feed

    @property
    def connection_state(self) -> MarketDataConnectionState:
        return self._state

    @property
    def market_clock(self) -> AlpacaMarketClock:
        self._require_credentials()
        return AlpacaMarketClock(self._api_key, self._secret_key)

    def _require_credentials(self) -> None:
        if not self._api_key or not self._secret_key:
            raise RuntimeError("Alpaca credentials are not configured")

    async def connect(self) -> None:
        self._require_credentials()
        self._state = "RECONNECTING"
        socket = await connect(f"{ALPACA_STREAM_BASE}/{self._feed}", open_timeout=15.0)
        self._socket = socket
        await socket.recv()
        await socket.send(
            json.dumps({"action": "auth", "key": self._api_key, "secret": self._secret_key})
        )
        auth = json.loads(cast(str, await socket.recv()))
        if not any(
            item.get("T") == "success" and item.get("msg") == "authenticated" for item in auth
        ):
            await socket.close()
            self._socket = None
            self._state = "DISCONNECTED"
            raise RuntimeError("Alpaca market-data authentication failed")
        self._state = "CONNECTED"

    async def subscribe(self, symbols: tuple[str, ...]) -> None:
        if self._socket is None or self._state != "CONNECTED":
            raise RuntimeError("Alpaca market-data socket is not connected")
        self._symbols = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
        await self._socket.send(
            json.dumps(
                {
                    "action": "subscribe",
                    "bars": list(self._symbols),
                    "updatedBars": list(self._symbols),
                }
            )
        )

    def normalize_message(
        self, payload: Mapping[str, object], *, received_at: datetime | None = None
    ) -> MarketBar | None:
        message_type = payload.get("T")
        if message_type not in {"b", "u"}:
            return None
        now = received_at or datetime.now(UTC)
        event_time = datetime.fromisoformat(str(payload["t"]).replace("Z", "+00:00"))
        symbol = str(payload["S"]).upper()
        key = (symbol, event_time)
        previous = self._revisions.get(key, 0)
        revision = max(2, previous + 1) if message_type == "u" else max(1, previous)
        self._revisions[key] = revision
        return MarketBar(
            symbol=symbol,
            event_time=event_time,
            available_at=now,
            received_at=now,
            open=float(cast(float, payload["o"])),
            high=float(cast(float, payload["h"])),
            low=float(cast(float, payload["l"])),
            close=float(cast(float, payload["c"])),
            volume=float(cast(float, payload["v"])),
            provider="alpaca",
            feed=self._feed,
            provider_event_id=f"{message_type}:{symbol}:{event_time.isoformat()}:r{revision}",
            revision=revision,
            is_correction=message_type == "u",
        )

    async def _iterate(self) -> AsyncIterator[MarketBar]:
        if self._socket is None:
            raise RuntimeError("Alpaca market-data socket is not connected")
        try:
            async for raw in self._socket:
                for payload in cast(list[dict[str, object]], json.loads(cast(str, raw))):
                    bar = self.normalize_message(payload)
                    if bar is not None:
                        yield bar
        finally:
            if self._state != "DISCONNECTED":
                self._state = "RECONNECTING"

    def events(self) -> AsyncIterator[MarketBar]:
        return self._iterate()

    async def disconnect(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close()
        self._state = "DISCONNECTED"

    async def historical_bars(
        self, symbols: tuple[str, ...], start: datetime, end: datetime
    ) -> tuple[MarketBar, ...]:
        return await AlpacaStockReferenceClient(
            api_key=self._api_key, secret_key=self._secret_key
        ).historical_bars(symbols, start, end, timeframe="1Min", feed=self._feed)
