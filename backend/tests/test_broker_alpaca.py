from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx

from app.broker.alpaca import (
    ALPACA_PAPER_TRADING_BASE,
    AlpacaPaperBrokerAdapter,
    normalize_alpaca_status,
)
from app.broker.models import BrokerOrderRequest


def test_alpaca_statuses_are_normalized_without_losing_raw_state() -> None:
    assert normalize_alpaca_status("new") == "SUBMITTED"
    assert normalize_alpaca_status("partially_filled") == "PARTIALLY_FILLED"
    assert normalize_alpaca_status("pending_cancel") == "PENDING_CANCEL"
    assert normalize_alpaca_status("canceled") == "CANCELLED"
    assert normalize_alpaca_status("rejected") == "REJECTED"
    assert normalize_alpaca_status("an_alpaca_state_added_later") == "UNKNOWN"


def test_adapter_is_paper_only_and_submits_idempotent_market_day_order() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "alpaca-order-1",
                "client_order_id": "vqd-paper-session-order-1",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "2.5",
                "filled_qty": "0",
                "filled_avg_price": None,
                "status": "accepted",
                "submitted_at": "2025-01-02T14:30:00Z",
                "updated_at": "2025-01-02T14:30:00Z",
            },
        )

    async def run() -> object:
        adapter = AlpacaPaperBrokerAdapter("paper-key", "paper-secret")
        await adapter._client.aclose()
        adapter._client = httpx.AsyncClient(
            base_url=ALPACA_PAPER_TRADING_BASE,
            headers={
                "APCA-API-KEY-ID": "paper-key",
                "APCA-API-SECRET-KEY": "paper-secret",
            },
            transport=httpx.MockTransport(handler),
        )
        update = await adapter.submit_market_order(
            BrokerOrderRequest(
                client_order_id="vqd-paper-session-order-1",
                symbol="AAPL",
                side="BUY",
                quantity=2.5,
                reference_price=190.0,
                submitted_at=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
            )
        )
        await adapter.close()
        return update

    update = asyncio.run(run())

    assert str(captured["url"]).startswith("https://paper-api.alpaca.markets/v2/")
    assert captured["payload"] == {
        "symbol": "AAPL",
        "qty": "2.5",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "vqd-paper-session-order-1",
    }
    assert (captured["headers"])["apca-api-key-id"] == "paper-key"  # type: ignore[index]
    assert update.status == "SUBMITTED"
    assert update.client_order_id == "vqd-paper-session-order-1"
