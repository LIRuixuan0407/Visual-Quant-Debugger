from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from app.market_data.adapter import MarketDataAdapter
from app.market_data.models import MarketBar, MarketDataConnectionState


class HistoricalMarketDataAdapter(MarketDataAdapter):
    def __init__(self, bars: tuple[MarketBar, ...]) -> None:
        self._bars = bars
        self._symbols: tuple[str, ...] = ()
        self._state: MarketDataConnectionState = "DISCONNECTED"

    @property
    def provider(self) -> str:
        return "historical"

    @property
    def feed(self) -> str:
        return "workspace"

    @property
    def connection_state(self) -> MarketDataConnectionState:
        return self._state

    async def connect(self) -> None:
        self._state = "CONNECTED"

    async def subscribe(self, symbols: tuple[str, ...]) -> None:
        if self._state != "CONNECTED":
            raise RuntimeError("Historical adapter is not connected")
        self._symbols = tuple(symbol.upper() for symbol in symbols)

    async def _iterate(self) -> AsyncIterator[MarketBar]:
        if not self._symbols:
            raise RuntimeError("No symbols are subscribed")
        for bar in self._bars:
            if bar.symbol in self._symbols:
                yield bar

    def events(self) -> AsyncIterator[MarketBar]:
        return self._iterate()

    async def disconnect(self) -> None:
        self._state = "DISCONNECTED"

    async def historical_bars(
        self, symbols: tuple[str, ...], start: datetime, end: datetime
    ) -> tuple[MarketBar, ...]:
        selected = {symbol.upper() for symbol in symbols}
        return tuple(
            bar for bar in self._bars if bar.symbol in selected and start <= bar.event_time <= end
        )
