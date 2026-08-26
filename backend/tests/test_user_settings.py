from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.paper as paper_api
import app.api.settings as settings_api
from app.main import app
from app.market_data import AlpacaStockReferenceClient, StockSecurity
from app.settings import AlpacaCredentialInput, IntegrationVault


def test_vault_encrypts_alpaca_credentials_and_returns_only_masked_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    vault = IntegrationVault(tmp_path)
    api_key = "PKUSER1234567890"
    secret_key = "super-private-secret-value"

    status = vault.save_alpaca(
        AlpacaCredentialInput(api_key=api_key, secret_key=secret_key, feed="sip")
    )

    assert status.configured is True
    assert status.source == "VAULT"
    assert status.masked_api_key == "PKUS••••7890"
    assert status.feed == "sip"
    encrypted = vault.payload_path.read_bytes()
    assert api_key.encode() not in encrypted
    assert secret_key.encode() not in encrypted
    resolved = vault.resolve_alpaca()
    assert resolved is not None
    assert resolved.secret_key == secret_key

    removed = vault.remove_alpaca()
    assert removed.configured is False
    assert not vault.payload_path.exists()


def test_user_alpaca_settings_api_saves_verifies_and_drives_provider_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    vault = IntegrationVault(tmp_path)
    monkeypatch.setattr(settings_api, "integration_vault", vault)
    monkeypatch.setattr(paper_api, "integration_vault", vault)

    async def verified_security(_client: AlpacaStockReferenceClient, symbol: str) -> StockSecurity:
        return StockSecurity(
            symbol=symbol,
            name="Apple Inc.",
            exchange="NASDAQ",
            status="active",
            tradable=True,
        )

    monkeypatch.setattr(AlpacaStockReferenceClient, "get_security", verified_security)
    client = TestClient(app)
    payload = {
        "api_key": "PKUSER1234567890",
        "secret_key": "super-private-secret-value",
        "feed": "sip",
    }

    saved = client.put("/api/me/integrations/alpaca", json=payload)
    assert saved.status_code == 200
    assert saved.json()["verification_status"] == "UNVERIFIED"
    assert saved.json()["masked_api_key"] == "PKUS••••7890"
    assert "super-private" not in saved.text

    verified = client.post("/api/me/integrations/alpaca/verify")
    assert verified.status_code == 200
    assert verified.json()["verification_status"] == "VERIFIED"
    assert verified.json()["last_verified_at"] is not None

    provider = client.get("/api/market-data/providers")
    assert provider.status_code == 200
    assert provider.json()[0]["configured"] is True
    assert provider.json()[0]["selected_feed"] == "sip"

    removed = client.delete("/api/me/integrations/alpaca")
    assert removed.status_code == 204
    assert client.get("/api/me/integrations/alpaca").json()["configured"] is False


def test_environment_credentials_are_visible_only_as_masked_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "ENVIRONMENTKEY1234")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "environment-secret")
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    status = IntegrationVault(tmp_path).alpaca_status()
    assert status.source == "ENVIRONMENT"
    assert status.masked_api_key == "ENVI••••1234"
    assert status.removable is False
