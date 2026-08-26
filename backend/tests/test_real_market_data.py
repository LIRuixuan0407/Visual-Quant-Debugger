from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import paper as paper_api
from app.datasets import DatasetRegistry
from app.main import app
from app.market_data import MarketBar, StockSecurity, StockSnapshot


class _ReferenceClient:
    security = StockSecurity(
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        status="active",
        tradable=True,
        fractionable=True,
    )

    async def search(self, query: str, *, limit: int = 20) -> tuple[StockSecurity, ...]:
        return (self.security,) if query.lower() in {"aapl", "apple"} else ()

    async def snapshot(self, symbol: str, *, feed: str = "iex") -> StockSnapshot:
        timestamp = datetime(2026, 8, 24, 20, 0, tzinfo=UTC)
        return StockSnapshot(
            security=self.security,
            feed="iex",
            market_timestamp=timestamp,
            received_at=timestamp,
            latest_trade_price=227.16,
            latest_trade_size=100,
        )

    async def historical_bars(
        self,
        symbols: tuple[str, ...],
        start: datetime,
        end: datetime,
        *,
        timeframe: str,
        feed: str,
    ) -> tuple[MarketBar, ...]:
        received = datetime(2026, 8, 25, tzinfo=UTC)
        return tuple(
            MarketBar(
                symbol="AAPL",
                timeframe="1Day",
                event_time=datetime(2026, 8, day, 20, 0, tzinfo=UTC),
                available_at=received,
                received_at=received,
                open=220 + day,
                high=222 + day,
                low=219 + day,
                close=221 + day,
                volume=1_000_000,
                provider="alpaca",
                feed="iex",
                provider_event_id=f"rest:AAPL:2026-08-{day}:r1",
            )
            for day in (20, 21)
        )


def test_real_stock_workspace_search_snapshot_and_dataset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(paper_api, "stock_reference_client", _ReferenceClient)
    monkeypatch.setattr(paper_api, "dataset_registry", DatasetRegistry(tmp_path))
    client = TestClient(app)

    search = client.get("/api/market-data/stocks/search", params={"q": "Apple"})
    assert search.status_code == 200
    assert search.json()[0]["name"] == "Apple Inc."

    snapshot = client.get("/api/market-data/stocks/AAPL/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["latest_trade_price"] == 227.16

    created = client.post(
        "/api/market-data/historical-datasets",
        json={
            "name": "AAPL real daily",
            "symbols": ["AAPL"],
            "start": "2026-08-20T00:00:00Z",
            "end": "2026-08-22T00:00:00Z",
            "timeframe": "1Day",
            "feed": "iex",
        },
    )
    assert created.status_code == 201
    dataset = created.json()
    assert dataset["source_type"] == "PROVIDER"
    assert dataset["row_count"] == 2
    assert dataset["symbols"] == ["AAPL"]
    assert dataset["provenance"]["provider"] == "alpaca"
    assert dataset["provenance"]["market_timestamp_end"] == "2026-08-21T20:00:00Z"


def test_real_stock_endpoints_are_explicitly_blocked_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        paper_api,
        "stock_reference_client",
        lambda: paper_api.AlpacaStockReferenceClient(api_key="", secret_key=""),
    )
    response = TestClient(app).get("/api/market-data/stocks/search", params={"q": "AAPL"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Alpaca credentials are not configured"
