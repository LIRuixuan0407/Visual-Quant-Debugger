from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class MarketBar:
    timestamp: datetime
    asset_a: float
    asset_b: float

    def as_frame(self) -> "MarketFrame":
        return MarketFrame(
            timestamp=self.timestamp,
            values={
                "ASSET_A": {"close": self.asset_a},
                "ASSET_B": {"close": self.asset_b},
            },
        )


@dataclass(frozen=True, slots=True)
class MarketFrame:
    """A synchronized point-in-time market event keyed by symbol and canonical field."""

    timestamp: datetime
    values: Mapping[str, Mapping[str, float]]
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Market frame timestamps must be timezone-aware")
        if self.available_at is not None:
            if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
                raise ValueError("Market frame availability must be timezone-aware")
            if self.available_at < self.timestamp:
                raise ValueError("Market frame availability cannot precede its event time")
        normalized = {
            symbol: MappingProxyType(dict(fields)) for symbol, fields in self.values.items()
        }
        if not normalized:
            raise ValueError("Market frames must contain at least one symbol")
        object.__setattr__(self, "values", MappingProxyType(normalized))

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self.values)

    @property
    def knowledge_time(self) -> datetime:
        return self.available_at or self.timestamp

    def value(self, symbol: str, field: str = "close") -> float:
        try:
            return self.values[symbol][field]
        except KeyError as exc:
            raise KeyError(
                f"{symbol}.{field} is unavailable at {self.timestamp.isoformat()}"
            ) from exc
