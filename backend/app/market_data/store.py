from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from enum import StrEnum

from app.market_data.models import MarketBar
from app.models import MarketFrame


class MarketApplyKind(StrEnum):
    BUFFERED = "BUFFERED"
    FRAME_READY = "FRAME_READY"
    CORRECTION = "CORRECTION_APPLIED"
    DUPLICATE = "DUPLICATE_IGNORED"
    OUT_OF_ORDER = "OUT_OF_ORDER_REJECTED"


class PointInTimeMarketStore:
    """Versioned market history preserving what was available at every knowledge time."""

    def __init__(self, symbols: tuple[str, ...]) -> None:
        normalized = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
        if not normalized:
            raise ValueError("At least one symbol is required")
        self.symbols = normalized
        self._versions: dict[tuple[str, datetime], list[MarketBar]] = defaultdict(list)
        self._seen: set[tuple[str, datetime, int]] = set()
        self._completed: set[datetime] = set()
        self.last_completed_time: datetime | None = None

    def classify(self, bar: MarketBar) -> MarketApplyKind:
        if bar.symbol not in self.symbols:
            raise ValueError(f"Unexpected symbol '{bar.symbol}'")
        if bar.identity in self._seen:
            return MarketApplyKind.DUPLICATE
        versions = self._versions[(bar.symbol, bar.event_time)]
        if versions and bar.revision <= versions[-1].revision:
            return MarketApplyKind.DUPLICATE
        if bar.is_correction:
            return MarketApplyKind.CORRECTION
        if self.last_completed_time is not None and bar.event_time < self.last_completed_time:
            return MarketApplyKind.OUT_OF_ORDER
        other_symbols_ready = all(
            symbol == bar.symbol or bool(self._versions[(symbol, bar.event_time)])
            for symbol in self.symbols
        )
        if bar.event_time in self._completed:
            return MarketApplyKind.DUPLICATE
        return MarketApplyKind.FRAME_READY if other_symbols_ready else MarketApplyKind.BUFFERED

    def commit(self, bar: MarketBar, kind: MarketApplyKind) -> MarketFrame | None:
        if kind in {MarketApplyKind.DUPLICATE, MarketApplyKind.OUT_OF_ORDER}:
            return None
        self._seen.add(bar.identity)
        versions = self._versions[(bar.symbol, bar.event_time)]
        versions.append(bar)
        versions.sort(key=lambda item: (item.revision, item.available_at))
        if kind == MarketApplyKind.CORRECTION:
            return self.frame(bar.event_time)
        if kind == MarketApplyKind.FRAME_READY:
            self._completed.add(bar.event_time)
            if self.last_completed_time is None or bar.event_time > self.last_completed_time:
                self.last_completed_time = bar.event_time
            return self.frame(bar.event_time)
        return None

    def frame(
        self, event_time: datetime, *, known_at: datetime | None = None
    ) -> MarketFrame | None:
        values: dict[str, dict[str, float]] = {}
        for symbol in self.symbols:
            versions = self._versions[(symbol, event_time)]
            eligible = (
                versions
                if known_at is None
                else [item for item in versions if item.available_at <= known_at]
            )
            if not eligible:
                return None
            bar = eligible[-1]
            values[symbol] = {
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
        availability = max(
            (
                versions
                if known_at is None
                else [item for item in versions if item.available_at <= known_at]
            )[-1].available_at
            for symbol in self.symbols
            if (versions := self._versions[(symbol, event_time)])
        )
        return MarketFrame(timestamp=event_time, values=values, available_at=availability)

    def versions(self, symbol: str, event_time: datetime) -> tuple[MarketBar, ...]:
        return tuple(self._versions[(symbol.upper(), event_time)])

    @property
    def correction_count(self) -> int:
        return sum(max(0, len(items) - 1) for items in self._versions.values())
