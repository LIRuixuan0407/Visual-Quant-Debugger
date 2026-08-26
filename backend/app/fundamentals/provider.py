from __future__ import annotations

from typing import Protocol

from .models import CreateFundamentalDataset, FundamentalObservation, FundamentalProviderInfo


class FundamentalDataProvider(Protocol):
    def info(self) -> FundamentalProviderInfo: ...

    async def fetch(
        self, request: CreateFundamentalDataset
    ) -> tuple[FundamentalObservation, ...]: ...
