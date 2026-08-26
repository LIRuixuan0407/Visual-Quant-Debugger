from dataclasses import dataclass
from datetime import datetime

from app.models import MarketBar


@dataclass(slots=True)
class HistoricalBarFeed:
    _bars: tuple[MarketBar, ...]
    _next_index: int = 0

    @property
    def total(self) -> int:
        return len(self._bars)

    @property
    def processed(self) -> int:
        return self._next_index

    @property
    def available_bars(self) -> tuple[MarketBar, ...]:
        return self._bars[: self._next_index]

    @property
    def watermark(self) -> datetime | None:
        return None if self._next_index == 0 else self._bars[self._next_index - 1].timestamp

    def next_bar(self) -> MarketBar | None:
        if self._next_index >= len(self._bars):
            return None
        bar = self._bars[self._next_index]
        self._next_index += 1
        return bar

    def future_bar(self, offset: int = 0) -> MarketBar:
        """Explicitly reject future access; useful at adapter boundaries and in tests."""
        raise RuntimeError(
            f"Future market data is unavailable at the current watermark (offset={offset})"
        )
