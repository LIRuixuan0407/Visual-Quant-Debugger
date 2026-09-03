from __future__ import annotations

import asyncio
import importlib
import importlib.util
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from zoneinfo import ZoneInfo

from app.market_data.adapter import MarketDataAdapter
from app.market_data.models import (
    MarketBar,
    MarketDataConnectionState,
    MarketDataTimeframe,
    MarketRegion,
    ProviderStatus,
    StockSecurity,
    StockSnapshot,
)

TdxAdjustment = Literal["NONE", "QFQ", "HFQ"]

_TDX_MARKET_CN = {"SZ": 0, "SH": 1, "BJ": 2}
_TDX_MARKET_HK = 31
_TDX_MARKET_US = 74
_TDX_HK_MARKETS = (31, 48, 49, 71)
_PERIODS: dict[MarketDataTimeframe, int] = {
    "5Min": 0,
    "15Min": 1,
    "1Hour": 3,
    "1Day": 4,
    "1Min": 7,
}
_ADJUSTMENTS: dict[TdxAdjustment, int] = {"NONE": 0, "QFQ": 1, "HFQ": 2}
_REGION_TZ: dict[MarketRegion, ZoneInfo] = {
    "CN": ZoneInfo("Asia/Shanghai"),
    "HK": ZoneInfo("Asia/Hong_Kong"),
    "US": ZoneInfo("America/New_York"),
}
_REGION_CURRENCY: dict[MarketRegion, Literal["CNY", "HKD", "USD"]] = {
    "CN": "CNY",
    "HK": "HKD",
    "US": "USD",
}
_REGION_EXCHANGE: dict[MarketRegion, str] = {"CN": "CN", "HK": "HKEX", "US": "US"}

_REGION_REGULAR_SESSIONS: dict[MarketRegion, tuple[tuple[int, int], ...]] = {
    "CN": ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60)),
    "HK": ((9 * 60 + 30, 12 * 60), (13 * 60, 16 * 60)),
    "US": ((9 * 60 + 30, 16 * 60),),
}


def _regular_market_is_open(region: MarketRegion, current_time: datetime) -> bool:
    local_time = current_time.astimezone(_REGION_TZ[region])
    if local_time.weekday() >= 5:
        return False
    minute = local_time.hour * 60 + local_time.minute
    return any(start <= minute < end for start, end in _REGION_REGULAR_SESSIONS[region])


class _FrameLike(Protocol):
    def to_dict(self, *, orient: str) -> list[dict[str, object]]: ...


class TdxClientProtocol(Protocol):
    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def get_stock_quotes(
        self, stocks: list[tuple[int, str]], fields: object | None = None
    ) -> _FrameLike: ...

    async def goods_quotes(
        self, stocks: list[tuple[int, str]], fields: object | None = None
    ) -> _FrameLike: ...

    async def get_stock_kline(
        self,
        market: int,
        code: str,
        period: int = 4,
        start: int = 0,
        count: int = 800,
        times: int = 1,
        adjust: int = 0,
    ) -> _FrameLike: ...

    async def goods_kline(
        self,
        market: int,
        code: str,
        period: int = 4,
        start: int = 0,
        count: int = 800,
        adjust: int = 0,
    ) -> _FrameLike: ...

    async def get_symbol_info(self, market: int, code: str) -> _FrameLike: ...

    async def goods_list(self, market: int, start: int = 0, count: int = 600) -> _FrameLike: ...


ClientFactory = Callable[[], TdxClientProtocol]


def _default_client_factory() -> TdxClientProtocol:
    try:
        module = importlib.import_module("easy_tdx.unified")
        client_class = module.AsyncUnifiedTdxClient
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "easy-tdx is not installed; install the backend dependencies again"
        ) from exc
    return cast(TdxClientProtocol, client_class())


def tdx_provider_status() -> ProviderStatus:
    installed = importlib.util.find_spec("easy_tdx") is not None
    return ProviderStatus(
        provider="tdx",
        configured=installed,
        feeds=("tdx",),
        selected_feed="tdx",
        markets=("CN", "HK", "US"),
        requires_credentials=False,
        supports_live=True,
        supports_historical=True,
        note=None if installed else "easy-tdx package is not installed",
    )


def _records(frame: object) -> list[dict[str, object]]:
    if frame is None:
        return []
    if isinstance(frame, list):
        return [cast(dict[str, object], item) for item in frame if isinstance(item, dict)]
    method = getattr(frame, "to_dict", None)
    if callable(method):
        value = method(orient="records")
        if isinstance(value, list):
            return [cast(dict[str, object], item) for item in value if isinstance(item, dict)]
    raise RuntimeError("easy-tdx returned an unsupported table shape")


def _value(row: Mapping[str, object], *names: str) -> object | None:
    for name in names:
        value = row.get(name)
        if value is not None:
            return value
    return None


def _float(row: Mapping[str, object], *names: str, default: float = 0.0) -> float:
    value = _value(row, *names)
    if value is None or value == "":
        return default
    return float(cast(Any, value))


def _dt(value: object, region: MarketRegion) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_REGION_TZ[region])
    return parsed.astimezone(UTC)


def _detect_cn_market(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return "BJ"
    if code.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


class TdxSymbol:
    def __init__(self, canonical: str, region: MarketRegion, market: int, code: str) -> None:
        self.canonical = canonical
        self.region = region
        self.market = market
        self.code = code


def parse_tdx_symbol(raw: str, *, region: MarketRegion | None = None) -> TdxSymbol:
    value = raw.strip().upper().replace(" ", "")
    if not value:
        raise ValueError("symbol is required")

    suffix_region: MarketRegion | None = None
    code = value
    cn_market: str | None = None
    if "." in value:
        head, suffix = value.rsplit(".", 1)
        if suffix in {"SH", "SZ", "BJ"}:
            suffix_region, code, cn_market = "CN", head, suffix
        elif suffix == "HK":
            suffix_region, code = "HK", head
        elif suffix == "US":
            suffix_region, code = "US", head
    elif value.startswith(("SH", "SZ", "BJ")) and len(value) > 2:
        cn_market, code, suffix_region = value[:2], value[2:], "CN"

    selected = suffix_region or region
    if selected is None:
        selected = "CN" if code.isdigit() and len(code) == 6 else "US"
    if region is not None and suffix_region is not None and suffix_region != region:
        raise ValueError(f"Symbol '{raw}' does not belong to the selected {region} market")

    if selected == "CN":
        if not code.isdigit() or len(code) != 6:
            raise ValueError("China A-share symbols must be six digits, e.g. 600519.SH")
        market_name = cn_market or _detect_cn_market(code)
        return TdxSymbol(f"{code}.{market_name}", "CN", _TDX_MARKET_CN[market_name], code)
    if selected == "HK":
        if not code.isdigit():
            raise ValueError("Hong Kong symbols must be numeric, e.g. 00700.HK")
        code = code.zfill(5)
        return TdxSymbol(f"{code}.HK", "HK", _TDX_MARKET_HK, code)
    if not code or len(code) > 22:
        raise ValueError("US symbol is invalid")
    return TdxSymbol(code, "US", _TDX_MARKET_US, code)


class TdxStockReferenceClient:
    def __init__(self, *, client_factory: ClientFactory | None = None) -> None:
        self._client_factory = client_factory or _default_client_factory

    async def _client(self) -> TdxClientProtocol:
        # AsyncUnifiedTdxClient lazily opens the A-share or extension channel on
        # the first matching request. Avoid forcing an A-share connection for
        # HK/US-only calls.
        return self._client_factory()

    @staticmethod
    async def _resolve_symbol(
        client: TdxClientProtocol, symbol: TdxSymbol
    ) -> tuple[TdxSymbol, dict[str, object]]:
        if symbol.region == "CN":
            rows = _records(await client.get_stock_quotes([(symbol.market, symbol.code)]))
        elif symbol.region == "HK":
            rows = _records(
                await client.goods_quotes([(market, symbol.code) for market in _TDX_HK_MARKETS])
            )
        else:
            rows = _records(await client.goods_quotes([(symbol.market, symbol.code)]))
        if not rows:
            raise ValueError(f"TDX returned no quote for {symbol.canonical}")
        row = rows[0]
        if symbol.region == "HK":
            raw_market = _value(row, "market")
            if raw_market is not None:
                resolved_market = int(cast(Any, raw_market))
                if resolved_market in _TDX_HK_MARKETS:
                    symbol = TdxSymbol(
                        symbol.canonical, symbol.region, resolved_market, symbol.code
                    )
        return symbol, row

    async def _quote_row(self, symbol: TdxSymbol) -> tuple[TdxSymbol, dict[str, object]]:
        client = await self._client()
        try:
            return await self._resolve_symbol(client, symbol)
        finally:
            await client.close()

    async def get_security(
        self, symbol: str, *, region: MarketRegion | None = None
    ) -> StockSecurity:
        parsed = parse_tdx_symbol(symbol, region=region)
        parsed, row = await self._quote_row(parsed)
        lot_size = max(
            1,
            int(
                _float(
                    row,
                    "lot_size",
                    "lot_size_info",
                    default=100.0 if parsed.region == "CN" else 1.0,
                )
            ),
        )
        return StockSecurity(
            symbol=parsed.canonical,
            name=str(_value(row, "name") or parsed.code),
            exchange=(
                parsed.canonical.rsplit(".", 1)[1]
                if parsed.region == "CN"
                else _REGION_EXCHANGE[parsed.region]
            ),
            status="active",
            tradable=True,
            fractionable=False,
            market=parsed.region,
            currency=_REGION_CURRENCY[parsed.region],
            lot_size=lot_size,
        )

    async def search(
        self, query: str, *, region: MarketRegion = "CN", limit: int = 20
    ) -> tuple[StockSecurity, ...]:
        # The zero-credential path intentionally treats the security code as the primary key.
        # This keeps search deterministic and avoids downloading an entire exchange catalog.
        # TDX market-wide name lookup can be added later without changing the API contract.
        try:
            security = await self.get_security(query, region=region)
        except ValueError:
            return ()
        return (security,)[:limit]

    async def _kline_rows(
        self,
        client: TdxClientProtocol,
        symbol: TdxSymbol,
        timeframe: MarketDataTimeframe,
        *,
        start_offset: int,
        count: int,
        adjustment: TdxAdjustment,
    ) -> list[dict[str, object]]:
        period = _PERIODS[timeframe]
        adjust = _ADJUSTMENTS[adjustment]
        if symbol.region == "CN":
            frame = await client.get_stock_kline(
                symbol.market,
                symbol.code,
                period=period,
                start=start_offset,
                count=count,
                adjust=adjust,
            )
        else:
            frame = await client.goods_kline(
                symbol.market,
                symbol.code,
                period=period,
                start=start_offset,
                count=count,
                adjust=adjust,
            )
        return _records(frame)

    def _bar(
        self,
        symbol: TdxSymbol,
        row: Mapping[str, object],
        timeframe: MarketDataTimeframe,
        received_at: datetime,
        *,
        adjustment: TdxAdjustment,
    ) -> MarketBar:
        raw_time = _value(row, "datetime", "date", "time")
        if raw_time is None:
            raise ValueError("TDX kline row is missing datetime")
        event_time = _dt(raw_time, symbol.region)
        close = _float(row, "close", "price")
        return MarketBar(
            symbol=symbol.canonical,
            timeframe=timeframe,
            event_time=event_time,
            available_at=received_at,
            received_at=received_at,
            open=_float(row, "open", default=close),
            high=_float(row, "high", default=close),
            low=_float(row, "low", default=close),
            close=close,
            volume=_float(row, "vol", "volume"),
            provider="tdx",
            feed="tdx",
            provider_event_id=(
                f"tdx:{symbol.canonical}:{timeframe}:{event_time.isoformat()}:{adjustment.lower()}"
            ),
        )

    async def historical_bars(
        self,
        symbols: tuple[str, ...],
        start: datetime,
        end: datetime,
        *,
        timeframe: MarketDataTimeframe = "1Day",
        region: MarketRegion | None = None,
        adjustment: TdxAdjustment = "NONE",
    ) -> tuple[MarketBar, ...]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Historical timestamps must be timezone-aware")
        if end <= start:
            raise ValueError("Historical end must be after start")
        received = datetime.now(UTC)
        result: list[MarketBar] = []
        client = await self._client()
        try:
            for raw_symbol in symbols:
                symbol = parse_tdx_symbol(raw_symbol, region=region)
                symbol, _ = await self._resolve_symbol(client, symbol)
                offset = 0
                while True:
                    rows = await self._kline_rows(
                        client,
                        symbol,
                        timeframe,
                        start_offset=offset,
                        count=800,
                        adjustment=adjustment,
                    )
                    if not rows:
                        break
                    parsed: list[MarketBar] = []
                    for row in rows:
                        try:
                            bar = self._bar(
                                symbol,
                                row,
                                timeframe,
                                received,
                                adjustment=adjustment,
                            )
                        except (TypeError, ValueError):
                            continue
                        parsed.append(bar)
                        if timeframe == "1Day":
                            market_date = bar.event_time.astimezone(
                                _REGION_TZ[symbol.region]
                            ).date()
                            in_requested_range = start.date() <= market_date <= end.date()
                        else:
                            in_requested_range = start <= bar.event_time <= end
                        if in_requested_range:
                            result.append(bar)
                    if timeframe == "1Day" and parsed:
                        oldest_reached = (
                            min(
                                item.event_time.astimezone(_REGION_TZ[symbol.region]).date()
                                for item in parsed
                            )
                            <= start.date()
                        )
                    else:
                        oldest_reached = (
                            bool(parsed) and min(item.event_time for item in parsed) <= start
                        )
                    if not parsed or oldest_reached or len(rows) < 800:
                        break
                    offset += len(rows)
                    if offset >= 80_000:
                        break
        finally:
            await client.close()
        unique = {(item.symbol, item.event_time): item for item in result}
        return tuple(sorted(unique.values(), key=lambda item: (item.event_time, item.symbol)))

    async def snapshot(self, symbol: str, *, region: MarketRegion | None = None) -> StockSnapshot:
        parsed = parse_tdx_symbol(symbol, region=region)
        received = datetime.now(UTC)
        parsed, row = await self._quote_row(parsed)
        client = await self._client()
        try:
            minute_rows = await self._kline_rows(
                client, parsed, "1Min", start_offset=0, count=2, adjustment="NONE"
            )
            daily_rows = await self._kline_rows(
                client, parsed, "1Day", start_offset=0, count=1, adjustment="NONE"
            )
        finally:
            await client.close()
        minute = (
            None
            if not minute_rows
            else max(
                (
                    self._bar(parsed, item, "1Min", received, adjustment="NONE")
                    for item in minute_rows
                ),
                key=lambda item: item.event_time,
            )
        )
        daily = (
            None
            if not daily_rows
            else self._bar(parsed, daily_rows[-1], "1Day", received, adjustment="NONE")
        )
        security = StockSecurity(
            symbol=parsed.canonical,
            name=str(_value(row, "name") or parsed.code),
            exchange=(
                parsed.canonical.rsplit(".", 1)[1]
                if parsed.region == "CN"
                else _REGION_EXCHANGE[parsed.region]
            ),
            status="active",
            tradable=True,
            fractionable=False,
            market=parsed.region,
            currency=_REGION_CURRENCY[parsed.region],
            lot_size=max(
                1,
                int(
                    _float(
                        row,
                        "lot_size",
                        "lot_size_info",
                        default=100.0 if parsed.region == "CN" else 1.0,
                    )
                ),
            ),
        )
        market_timestamp = (
            minute.event_time
            if minute is not None
            else daily.event_time
            if daily is not None
            else received
        )
        local_now = received.astimezone(_REGION_TZ[parsed.region])
        weekday_open = local_now.weekday() < 5
        minutes = local_now.hour * 60 + local_now.minute
        if parsed.region == "CN":
            open_now = weekday_open and (570 <= minutes < 690 or 780 <= minutes < 900)
        elif parsed.region == "HK":
            open_now = weekday_open and (570 <= minutes < 720 or 780 <= minutes < 960)
        else:
            open_now = weekday_open and 570 <= minutes < 960
        age = None if minute is None else max(0.0, (received - minute.event_time).total_seconds())
        freshness = (
            "UNVERIFIED"
            if age is None
            else "CLOSED"
            if not open_now
            else "LIVE"
            if age <= 180
            else "DELAYED"
            if age <= 1800
            else "STALE"
        )
        return StockSnapshot(
            security=security,
            provider="tdx",
            feed="tdx",
            market_timestamp=market_timestamp,
            received_at=received,
            latest_trade_price=(
                _float(row, "close", "price", default=minute.close if minute is not None else 0.0)
                or None
            ),
            latest_trade_size=_float(row, "last_volume", "last_vol", default=0.0) or None,
            minute_bar=minute,
            daily_bar=daily,
            market=parsed.region,
            freshness_status=cast(Any, freshness),
            freshness_seconds=age,
        )


class TdxMarketDataAdapter(MarketDataAdapter):
    """Poll finalized TDX one-minute bars for CN/HK/US paper sessions."""

    def __init__(
        self,
        *,
        region: MarketRegion,
        poll_interval: float = 3.0,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._region = region
        self._poll_interval = poll_interval
        self._client_factory = client_factory or _default_client_factory
        self._client: TdxClientProtocol | None = None
        self._symbols: tuple[TdxSymbol, ...] = ()
        self._state: MarketDataConnectionState = "DISCONNECTED"
        self._last_emitted: dict[str, datetime] = {}
        self._reference = TdxStockReferenceClient(client_factory=self._client_factory)

    @property
    def provider(self) -> str:
        return "tdx"

    @property
    def feed(self) -> str:
        return "tdx"

    @property
    def connection_state(self) -> MarketDataConnectionState:
        return self._state

    async def connect(self) -> None:
        if self._client is None:
            self._client = self._client_factory()
        self._state = "CONNECTED"

    async def subscribe(self, symbols: tuple[str, ...]) -> None:
        if self._state != "CONNECTED" or self._client is None:
            raise RuntimeError("TDX adapter is not connected")
        parsed = tuple(parse_tdx_symbol(item, region=self._region) for item in symbols)
        if not parsed:
            raise ValueError("At least one symbol is required")
        self._symbols = parsed

    async def _latest_finalized(self, symbol: TdxSymbol) -> MarketBar | None:
        client = self._client
        if client is None:
            raise RuntimeError("TDX adapter is not connected")
        rows = await self._reference._kline_rows(
            client, symbol, "1Min", start_offset=0, count=2, adjustment="NONE"
        )
        if not rows:
            return None
        received = datetime.now(UTC)
        current_minute = (
            received.astimezone(_REGION_TZ[self._region])
            .replace(second=0, microsecond=0)
            .astimezone(UTC)
        )
        completed: list[MarketBar] = []
        for row in rows:
            raw_time = _value(row, "datetime", "date", "time")
            if raw_time is None:
                continue
            try:
                # TDX includes the current, unfinished minute and its clock can lead
                # ours slightly. Filter it before MarketBar validates availability.
                if _dt(raw_time, symbol.region) >= current_minute:
                    continue
                completed.append(
                    self._reference._bar(symbol, row, "1Min", received, adjustment="NONE")
                )
            except (TypeError, ValueError):
                continue
        return None if not completed else max(completed, key=lambda item: item.event_time)

    async def _iterate(self) -> AsyncIterator[MarketBar]:
        if not self._symbols:
            raise RuntimeError("No TDX symbols are subscribed")
        while self._state != "DISCONNECTED":
            emitted = False
            for symbol in self._symbols:
                bar = await self._latest_finalized(symbol)
                if bar is None:
                    continue
                previous = self._last_emitted.get(symbol.canonical)
                if previous is not None and bar.event_time <= previous:
                    continue
                self._last_emitted[symbol.canonical] = bar.event_time
                emitted = True
                yield bar
            if not emitted:
                delay = (
                    self._poll_interval
                    if _regular_market_is_open(self._region, datetime.now(UTC))
                    else max(self._poll_interval, 60.0)
                )
                await asyncio.sleep(delay)

    def events(self) -> AsyncIterator[MarketBar]:
        return self._iterate()

    async def disconnect(self) -> None:
        self._state = "DISCONNECTED"
        client, self._client = self._client, None
        if client is not None:
            await client.close()

    async def historical_bars(
        self, symbols: tuple[str, ...], start: datetime, end: datetime
    ) -> tuple[MarketBar, ...]:
        return await self._reference.historical_bars(
            symbols,
            start,
            end,
            timeframe="1Min",
            region=self._region,
            adjustment="NONE",
        )
