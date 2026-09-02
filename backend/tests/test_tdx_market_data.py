from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.market_data.tdx import TdxMarketDataAdapter, TdxStockReferenceClient, parse_tdx_symbol


class _Frame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def to_dict(self, *, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return self.rows


class _TdxClient:
    def __init__(self) -> None:
        self.connected = False
        self.calls: list[tuple[str, int, str]] = []
        self.live_rows: dict[tuple[int, str], list[dict[str, object]]] = {}

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    @staticmethod
    def _quote(market: int, code: str) -> _Frame:
        names = {"600519": "贵州茅台", "00700": "腾讯控股", "AAPL": "Apple Inc."}
        return _Frame(
            [
                {
                    "market": market,
                    "code": code,
                    "name": names.get(code, code),
                    "close": 100.0 + market,
                    "last_volume": 200,
                    "lot_size": 100 if market in {0, 1, 2} else 1,
                }
            ]
        )

    async def get_stock_quotes(
        self, stocks: list[tuple[int, str]], fields: object | None = None
    ) -> _Frame:
        del fields
        market, code = stocks[0]
        self.calls.append(("cn-quote", market, code))
        return self._quote(market, code)

    async def goods_quotes(
        self, stocks: list[tuple[int, str]], fields: object | None = None
    ) -> _Frame:
        del fields
        market, code = stocks[0]
        self.calls.append(("ex-quote", market, code))
        return self._quote(market, code)

    def _bars(self, market: int, code: str, period: int) -> _Frame:
        rows = self.live_rows.get((market, code))
        if rows is not None and period == 7:
            return _Frame(rows)
        return _Frame(
            [
                {
                    "datetime": datetime(2026, 8, 31),
                    "open": 99.0,
                    "high": 102.0,
                    "low": 98.0,
                    "close": 101.0,
                    "vol": 1234.0,
                },
                {
                    "datetime": datetime(2026, 9, 1),
                    "open": 101.0,
                    "high": 104.0,
                    "low": 100.0,
                    "close": 103.0,
                    "vol": 2345.0,
                },
            ]
        )

    async def get_stock_kline(
        self,
        market: int,
        code: str,
        period: int = 4,
        start: int = 0,
        count: int = 800,
        times: int = 1,
        adjust: int = 0,
    ) -> _Frame:
        del start, count, times, adjust
        self.calls.append(("cn-kline", market, code))
        return self._bars(market, code, period)

    async def goods_kline(
        self,
        market: int,
        code: str,
        period: int = 4,
        start: int = 0,
        count: int = 800,
        adjust: int = 0,
    ) -> _Frame:
        del start, count, adjust
        self.calls.append(("ex-kline", market, code))
        return self._bars(market, code, period)

    async def get_symbol_info(self, market: int, code: str) -> _Frame:
        return self._quote(market, code)

    async def goods_list(self, market: int, start: int = 0, count: int = 600) -> _Frame:
        del market, start, count
        return _Frame([])


def test_tdx_symbol_normalization_keeps_vqd_market_identity() -> None:
    assert parse_tdx_symbol("600519", region="CN").canonical == "600519.SH"
    assert parse_tdx_symbol("000001", region="CN").canonical == "000001.SZ"
    assert parse_tdx_symbol("430047", region="CN").canonical == "430047.BJ"
    assert parse_tdx_symbol("700", region="HK").canonical == "00700.HK"
    assert parse_tdx_symbol("AAPL", region="US").canonical == "AAPL"
    assert parse_tdx_symbol("AAPL.US").canonical == "AAPL"
    with pytest.raises(ValueError, match="selected CN market"):
        parse_tdx_symbol("00700.HK", region="CN")


def test_tdx_historical_bars_normalize_cn_hk_and_us_without_credentials() -> None:
    async def scenario() -> None:
        fake = _TdxClient()
        reference = TdxStockReferenceClient(client_factory=lambda: fake)
        start = datetime(2026, 8, 31, tzinfo=UTC)
        end = datetime(2026, 9, 1, 23, 59, tzinfo=UTC)

        cn = await reference.historical_bars(
            ("600519.SH",), start, end, timeframe="1Day", region="CN", adjustment="QFQ"
        )
        hk = await reference.historical_bars(
            ("00700.HK",), start, end, timeframe="1Day", region="HK"
        )
        us = await reference.historical_bars(
            ("AAPL",), start, end, timeframe="1Day", region="US"
        )

        assert [item.symbol for item in cn] == ["600519.SH", "600519.SH"]
        assert [item.symbol for item in hk] == ["00700.HK", "00700.HK"]
        assert [item.symbol for item in us] == ["AAPL", "AAPL"]
        assert all(item.provider == "tdx" and item.feed == "tdx" for item in (*cn, *hk, *us))
        assert ("cn-kline", 1, "600519") in fake.calls
        assert ("ex-kline", 31, "00700") in fake.calls
        assert ("ex-kline", 74, "AAPL") in fake.calls

    asyncio.run(scenario())



def test_tdx_hk_symbol_resolves_extension_market_before_loading_bars() -> None:
    class _GemClient(_TdxClient):
        async def goods_quotes(
            self, stocks: list[tuple[int, str]], fields: object | None = None
        ) -> _Frame:
            del fields
            assert (48, "08000") in stocks
            self.calls.append(("ex-quote", 48, "08000"))
            return self._quote(48, "08000")

    async def scenario() -> None:
        fake = _GemClient()
        reference = TdxStockReferenceClient(client_factory=lambda: fake)
        start = datetime(2026, 8, 31, tzinfo=UTC)
        end = datetime(2026, 9, 1, 23, 59, tzinfo=UTC)

        bars = await reference.historical_bars(
            ("08000.HK",), start, end, timeframe="1Day", region="HK"
        )

        assert [item.symbol for item in bars] == ["08000.HK", "08000.HK"]
        assert ("ex-kline", 48, "08000") in fake.calls

    asyncio.run(scenario())

def test_tdx_snapshot_exposes_market_currency_lot_and_freshness() -> None:
    async def scenario() -> None:
        fake = _TdxClient()
        reference = TdxStockReferenceClient(client_factory=lambda: fake)
        snapshot = await reference.snapshot("600519", region="CN")
        assert snapshot.provider == "tdx"
        assert snapshot.feed == "tdx"
        assert snapshot.market == "CN"
        assert snapshot.security.symbol == "600519.SH"
        assert snapshot.security.currency == "CNY"
        assert snapshot.security.lot_size == 100
        assert snapshot.freshness_status in {"LIVE", "DELAYED", "STALE", "CLOSED"}
        assert snapshot.freshness_seconds is not None

    asyncio.run(scenario())


def test_tdx_live_adapter_emits_only_completed_one_minute_bars() -> None:
    async def scenario() -> None:
        fake = _TdxClient()
        now_local = datetime.now(ZoneInfo("Asia/Shanghai")).replace(second=0, microsecond=0)
        fake.live_rows[(1, "600519")] = [
            {
                "datetime": now_local - timedelta(minutes=1),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "vol": 10.0,
            },
            {
                "datetime": now_local,
                "open": 100.5,
                "high": 102.0,
                "low": 100.0,
                "close": 101.5,
                "vol": 5.0,
            },
        ]
        adapter = TdxMarketDataAdapter(
            region="CN", poll_interval=0.001, client_factory=lambda: fake
        )
        await adapter.connect()
        await adapter.subscribe(("600519.SH",))
        emitted = await asyncio.wait_for(adapter.events().__anext__(), timeout=1.0)
        assert emitted.symbol == "600519.SH"
        assert emitted.event_time == (now_local - timedelta(minutes=1)).astimezone(UTC)
        assert emitted.close == 100.5
        await adapter.disconnect()

    asyncio.run(scenario())
