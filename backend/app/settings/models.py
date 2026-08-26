from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

AlpacaFeed = Literal["iex", "sip"]
CredentialSource = Literal["VAULT", "ENVIRONMENT", "NONE"]
VerificationStatus = Literal["UNVERIFIED", "VERIFIED", "FAILED"]


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AlpacaCredentialInput(SettingsModel):
    api_key: SecretStr = Field(min_length=8, max_length=256)
    secret_key: SecretStr = Field(min_length=8, max_length=256)
    feed: AlpacaFeed = "iex"

    @field_validator("api_key", "secret_key")
    @classmethod
    def reject_surrounding_whitespace(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw != raw.strip():
            raise ValueError("Credentials cannot begin or end with whitespace")
        return value


class AlpacaIntegrationStatus(SettingsModel):
    provider: Literal["alpaca"] = "alpaca"
    configured: bool
    source: CredentialSource
    masked_api_key: str | None
    feed: AlpacaFeed
    verification_status: VerificationStatus
    last_verified_at: datetime | None = None
    last_error: str | None = None
    removable: bool = False


@dataclass(frozen=True, slots=True)
class AlpacaResolvedCredentials:
    api_key: str
    secret_key: str
    feed: AlpacaFeed
    source: CredentialSource
