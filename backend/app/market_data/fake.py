from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime

from app.market_data.adapter import MarketDataAdapter
from app.market_data.models import MarketBar, MarketDataConnectionState


class FakeLiveMarketDataAdapter(MarketDataAdapter):
    """Deterministic provider used by contract, reconnect, and recovery tests."""

    def __init__(self, *, feed: str = "iex") -> None:
        self._feed = feed
        self._state: MarketDataConnectionState = "DISCONNECTED"
        self._symbols: tuple[str, ...] = ()
        self._queue: asyncio.Queue[MarketBar | None] = asyncio.Queue()
        self._historical: list[MarketBar] = []

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def feed(self) -> str:
        return self._feed

    @property
    def connection_state(self) -> MarketDataConnectionState:
        return self._state

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    async def connect(self) -> None:
        self._state = "CONNECTED"

    async def subscribe(self, symbols: tuple[str, ...]) -> None:
        if self._state != "CONNECTED":
            raise RuntimeError("Fake adapter is not connected")
        self._symbols = tuple(symbol.upper() for symbol in symbols)

    async def _iterate(self) -> AsyncIterator[MarketBar]:
        while self._state != "DISCONNECTED":
            item = await self._queue.get()
            if item is None:
                return
            yield item

    def events(self) -> AsyncIterator[MarketBar]:
        return self._iterate()

    async def disconnect(self) -> None:
        self._state = "DISCONNECTED"
        await self._queue.put(None)

    async def simulate_disconnect(self) -> None:
        self._state = "RECONNECTING"

    async def simulate_reconnect(self) -> None:
        self._state = "CONNECTED"

    async def emit(self, bar: MarketBar) -> None:
        if self._state != "CONNECTED":
            raise RuntimeError("Cannot emit while fake feed is disconnected")
        await self._queue.put(bar)

    def add_historical(self, *bars: MarketBar) -> None:
        self._historical.extend(bars)

    async def historical_bars(
        self, symbols: tuple[str, ...], start: datetime, end: datetime
    ) -> tuple[MarketBar, ...]:
        selected = {symbol.upper() for symbol in symbols}
        return tuple(
            sorted(
                (
                    bar
                    for bar in self._historical
                    if bar.symbol in selected and start <= bar.event_time <= end
                ),
                key=lambda bar: (bar.event_time, bar.symbol, bar.revision),
            )
        )
