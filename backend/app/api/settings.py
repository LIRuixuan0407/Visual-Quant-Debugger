from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Response, status

from app.market_data import AlpacaStockReferenceClient
from app.settings import AlpacaCredentialInput, AlpacaIntegrationStatus, integration_vault

router = APIRouter(prefix="/api/me/integrations", tags=["user-settings"])


@router.get("/alpaca", response_model=AlpacaIntegrationStatus)
def get_alpaca_integration() -> AlpacaIntegrationStatus:
    return integration_vault.alpaca_status()


@router.put("/alpaca", response_model=AlpacaIntegrationStatus)
def save_alpaca_integration(request: AlpacaCredentialInput) -> AlpacaIntegrationStatus:
    return integration_vault.save_alpaca(request)


@router.post("/alpaca/verify", response_model=AlpacaIntegrationStatus)
async def verify_alpaca_integration() -> AlpacaIntegrationStatus:
    credentials = integration_vault.resolve_alpaca()
    if credentials is None:
        raise HTTPException(status_code=409, detail="Alpaca credentials are not configured")
    try:
        await AlpacaStockReferenceClient(
            api_key=credentials.api_key, secret_key=credentials.secret_key
        ).get_security("AAPL")
    except httpx.HTTPStatusError as exc:
        detail = "Alpaca rejected these credentials"
        integration_vault.record_alpaca_verification(successful=False, error=detail)
        code = 401 if exc.response.status_code in {401, 403} else 502
        raise HTTPException(status_code=code, detail=detail) from exc
    except httpx.HTTPError as exc:
        detail = "Alpaca could not be reached"
        integration_vault.record_alpaca_verification(successful=False, error=detail)
        raise HTTPException(status_code=502, detail=detail) from exc
    return integration_vault.record_alpaca_verification(successful=True)


@router.delete("/alpaca", status_code=status.HTTP_204_NO_CONTENT)
def remove_alpaca_integration() -> Response:
    current = integration_vault.alpaca_status()
    if current.source == "ENVIRONMENT":
        raise HTTPException(
            status_code=409,
            detail="Environment-managed credentials cannot be removed from the interface",
        )
    integration_vault.remove_alpaca()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
