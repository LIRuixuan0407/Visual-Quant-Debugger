from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from cryptography.fernet import Fernet, InvalidToken

from app.settings.models import (
    AlpacaCredentialInput,
    AlpacaFeed,
    AlpacaIntegrationStatus,
    AlpacaResolvedCredentials,
    VerificationStatus,
)
from app.workspace import default_workspace_root


def _mask(value: str) -> str:
    if len(value) <= 8:
        return f"{value[:2]}••••{value[-2:]}"
    return f"{value[:4]}••••{value[-4:]}"


class IntegrationVault:
    """Single-user encrypted integration settings for the local VQD workspace."""

    def __init__(self, workspace_root: str | Path | None = None) -> None:
        root = default_workspace_root() if workspace_root is None else Path(workspace_root)
        self.root = root.expanduser().resolve() / ".vqd" / "secrets"
        self.key_path = self.root / "vault.key"
        self.payload_path = self.root / "integrations.bin"

    @staticmethod
    def _secure(path: Path) -> None:
        if os.name != "nt":
            path.chmod(0o600)

    def _key(self, *, create: bool) -> bytes | None:
        configured = os.environ.get("VQD_SECRETS_MASTER_KEY")
        if configured:
            return configured.encode()
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        if not create:
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        temporary = self.key_path.with_suffix(".tmp")
        temporary.write_bytes(key + b"\n")
        self._secure(temporary)
        temporary.replace(self.key_path)
        self._secure(self.key_path)
        return key

    def _read(self) -> dict[str, Any]:
        if not self.payload_path.exists():
            return {"version": 1}
        key = self._key(create=False)
        if key is None:
            raise RuntimeError("The integration vault key is unavailable")
        try:
            decrypted = Fernet(key).decrypt(self.payload_path.read_bytes())
            value = json.loads(decrypted)
        except (InvalidToken, ValueError, OSError) as exc:
            raise RuntimeError("The integration vault could not be decrypted") from exc
        if not isinstance(value, dict) or value.get("version") != 1:
            raise RuntimeError("The integration vault has an unsupported format")
        return cast(dict[str, Any], value)

    def _write(self, payload: dict[str, Any]) -> None:
        key = self._key(create=True)
        if key is None:  # pragma: no cover - create=True always returns a key
            raise RuntimeError("The integration vault key is unavailable")
        self.root.mkdir(parents=True, exist_ok=True)
        encrypted = Fernet(key).encrypt(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )
        temporary = self.payload_path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        self._secure(temporary)
        temporary.replace(self.payload_path)
        self._secure(self.payload_path)

    def save_alpaca(self, request: AlpacaCredentialInput) -> AlpacaIntegrationStatus:
        payload = self._read()
        payload["alpaca"] = {
            "api_key": request.api_key.get_secret_value(),
            "secret_key": request.secret_key.get_secret_value(),
            "feed": request.feed,
            "verification_status": "UNVERIFIED",
            "last_verified_at": None,
            "last_error": None,
        }
        self._write(payload)
        return self.alpaca_status()

    def remove_alpaca(self) -> AlpacaIntegrationStatus:
        payload = self._read()
        payload.pop("alpaca", None)
        if len(payload) == 1:
            if self.payload_path.exists():
                self.payload_path.unlink()
        else:
            self._write(payload)
        return self.alpaca_status()

    def resolve_alpaca(self) -> AlpacaResolvedCredentials | None:
        saved = self._read().get("alpaca")
        if isinstance(saved, dict):
            feed = str(saved.get("feed", "iex"))
            return AlpacaResolvedCredentials(
                api_key=str(saved["api_key"]),
                secret_key=str(saved["secret_key"]),
                feed=cast(AlpacaFeed, feed if feed in {"iex", "sip"} else "iex"),
                source="VAULT",
            )
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        if not api_key or not secret_key:
            return None
        feed = os.environ.get("ALPACA_DATA_FEED", "iex").lower()
        return AlpacaResolvedCredentials(
            api_key=api_key,
            secret_key=secret_key,
            feed=cast(AlpacaFeed, feed if feed in {"iex", "sip"} else "iex"),
            source="ENVIRONMENT",
        )

    def alpaca_status(self) -> AlpacaIntegrationStatus:
        payload = self._read()
        saved = payload.get("alpaca")
        resolved = self.resolve_alpaca()
        if resolved is None:
            return AlpacaIntegrationStatus(
                configured=False,
                source="NONE",
                masked_api_key=None,
                feed="iex",
                verification_status="UNVERIFIED",
            )
        verification: VerificationStatus = "UNVERIFIED"
        last_verified_at = None
        last_error = None
        if isinstance(saved, dict):
            raw_verification = str(saved.get("verification_status", "UNVERIFIED"))
            if raw_verification in {"UNVERIFIED", "VERIFIED", "FAILED"}:
                verification = cast(VerificationStatus, raw_verification)
            raw_time = saved.get("last_verified_at")
            last_verified_at = None if raw_time is None else datetime.fromisoformat(str(raw_time))
            raw_error = saved.get("last_error")
            last_error = None if raw_error is None else str(raw_error)
        return AlpacaIntegrationStatus(
            configured=True,
            source=resolved.source,
            masked_api_key=_mask(resolved.api_key),
            feed=resolved.feed,
            verification_status=verification,
            last_verified_at=last_verified_at,
            last_error=last_error,
            removable=resolved.source == "VAULT",
        )

    def record_alpaca_verification(
        self, *, successful: bool, error: str | None = None
    ) -> AlpacaIntegrationStatus:
        payload = self._read()
        saved = payload.get("alpaca")
        if not isinstance(saved, dict):
            return self.alpaca_status()
        saved["verification_status"] = "VERIFIED" if successful else "FAILED"
        saved["last_verified_at"] = datetime.now(UTC).isoformat() if successful else None
        saved["last_error"] = error
        self._write(payload)
        return self.alpaca_status()


integration_vault = IntegrationVault()
