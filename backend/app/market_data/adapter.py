from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from app.market_data.models import MarketBar, MarketDataConnectionState


class MarketDataAdapter(ABC):
    """Provider-independent contract for finalized one-minute market bars."""

    @property
    @abstractmethod
    def provider(self) -> str: ...

    @property
    @abstractmethod
    def feed(self) -> str: ...

    @property
    @abstractmethod
    def connection_state(self) -> MarketDataConnectionState: ...

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def subscribe(self, symbols: tuple[str, ...]) -> None: ...

    @abstractmethod
    def events(self) -> AsyncIterator[MarketBar]: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def historical_bars(
        self, symbols: tuple[str, ...], start: datetime, end: datetime
    ) -> tuple[MarketBar, ...]: ...
