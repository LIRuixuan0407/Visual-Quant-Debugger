from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import httpx

from app.broker.models import (
    BrokerAccountSnapshot,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerOrderUpdate,
)

ALPACA_PAPER_TRADING_BASE = "https://paper-api.alpaca.markets/v2"


def _headers(api_key: str, secret_key: str) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }


def _time(value: object | None, fallback: datetime | None = None) -> datetime:
    if value:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return fallback or datetime.now(UTC)


def _number(value: object | None, default: float = 0.0) -> float:
    return default if value in (None, "") else float(cast(str | float, value))


def normalize_alpaca_status(value: str) -> BrokerOrderStatus:
    status = value.strip().lower()
    if status == "filled":
        return "FILLED"
    if status == "partially_filled":
        return "PARTIALLY_FILLED"
    if status in {"canceled", "cancelled"}:
        return "CANCELLED"
    if status == "rejected":
        return "REJECTED"
    if status == "expired":
        return "EXPIRED"
    if status == "pending_cancel":
        return "PENDING_CANCEL"
    if status == "done_for_day":
        return "DONE_FOR_DAY"
    if status == "replaced":
        return "REPLACED"
    if status == "held":
        return "HELD"
    if status == "suspended":
        return "SUSPENDED"
    submitted = {
        "new",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "stopped",
        "calculated",
    }
    if status in submitted:
        return "SUBMITTED"
    return "UNKNOWN"


class AlpacaPaperBrokerAdapter:
    """Thin, paper-only Alpaca Trading API boundary.

    The live Trading API domain is intentionally not configurable here. This integration
    may never route an order outside Alpaca's paper environment.
    """

    def __init__(self, api_key: str, secret_key: str) -> None:
        if not api_key or not secret_key:
            raise RuntimeError("Alpaca Paper credentials are not configured")
        self._client = httpx.AsyncClient(
            base_url=ALPACA_PAPER_TRADING_BASE,
            headers=_headers(api_key, secret_key),
            timeout=20.0,
        )

    @staticmethod
    def _order(payload: dict[str, Any]) -> BrokerOrderUpdate:
        raw_status = str(payload.get("status") or "unknown")
        status = normalize_alpaca_status(raw_status)
        submitted_at = _time(payload.get("submitted_at") or payload.get("created_at"))
        updated_at = _time(payload.get("updated_at"), submitted_at)
        terminal_value = (
            payload.get("filled_at")
            or payload.get("canceled_at")
            or payload.get("expired_at")
            or payload.get("failed_at")
            or payload.get("replaced_at")
        )
        rejection_reason = payload.get("reject_reason") or payload.get("failed_reason")
        return BrokerOrderUpdate(
            provider_order_id=str(payload["id"]),
            client_order_id=str(payload["client_order_id"]),
            symbol=str(payload["symbol"]).upper(),
            side="BUY" if str(payload["side"]).lower() == "buy" else "SELL",
            ordered_quantity=_number(payload.get("qty")),
            filled_quantity=_number(payload.get("filled_qty")),
            average_fill_price=(
                None
                if payload.get("filled_avg_price") in (None, "")
                else _number(payload.get("filled_avg_price"))
            ),
            status=status,
            raw_status=raw_status,
            submitted_at=submitted_at,
            updated_at=updated_at,
            terminal_at=None if terminal_value is None else _time(terminal_value),
            rejection_reason=None if rejection_reason is None else str(rejection_reason),
        )

    async def account(self) -> BrokerAccountSnapshot:
        response = await self._client.get("/account")
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        return BrokerAccountSnapshot(
            account_id=str(payload["id"]),
            status=str(payload.get("status") or "UNKNOWN"),
            currency=str(payload.get("currency") or "USD"),
            cash=_number(payload.get("cash")),
            equity=_number(payload.get("equity")),
            buying_power=_number(payload.get("buying_power")),
            portfolio_value=_number(payload.get("portfolio_value")),
            trading_blocked=bool(payload.get("trading_blocked", False)),
        )

    async def submit_market_order(self, request: BrokerOrderRequest) -> BrokerOrderUpdate:
        response = await self._client.post(
            "/orders",
            json={
                "symbol": request.symbol,
                "qty": str(request.quantity),
                "side": request.side.lower(),
                "type": "market",
                "time_in_force": "day",
                "client_order_id": request.client_order_id,
            },
        )
        response.raise_for_status()
        return self._order(cast(dict[str, Any], response.json()))

    async def get_order(self, provider_order_id: str) -> BrokerOrderUpdate:
        response = await self._client.get(f"/orders/{provider_order_id}")
        response.raise_for_status()
        return self._order(cast(dict[str, Any], response.json()))

    async def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderUpdate:
        response = await self._client.get(
            "/orders:by_client_order_id", params={"client_order_id": client_order_id}
        )
        response.raise_for_status()
        return self._order(cast(dict[str, Any], response.json()))

    async def cancel_order(self, provider_order_id: str) -> None:
        response = await self._client.delete(f"/orders/{provider_order_id}")
        if response.status_code not in {204, 404, 422}:
            response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
