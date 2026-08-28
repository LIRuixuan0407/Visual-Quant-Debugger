from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.datasets import dataset_registry
from app.fundamentals import (
    CreateFundamentalDataset,
    FundamentalDataset,
    FundamentalDatasetSummary,
    FundamentalProviderInfo,
    FundamentalSnapshot,
    SecCompanyFactsProvider,
    fundamental_repository,
)
from app.universes import HistoricalUniverse, universe_repository

router = APIRouter(prefix="/api", tags=["fundamental-research"])


def _summary(item: FundamentalDataset) -> FundamentalDatasetSummary:
    return FundamentalDatasetSummary(
        fundamental_dataset_id=item.fundamental_dataset_id,
        name=item.name,
        provider=item.provider,
        symbols=item.symbols,
        fields=item.fields,
        start_time=item.start_time,
        end_time=item.end_time,
        retrieved_at=item.retrieved_at,
        observation_count=len(item.observations),
        point_in_time_safe=item.point_in_time_safe,
        restatement_safe=item.restatement_safe,
        disclosure=item.disclosure,
    )


@router.get("/fundamental-providers", response_model=tuple[FundamentalProviderInfo, ...])
def list_fundamental_providers() -> tuple[FundamentalProviderInfo, ...]:
    return (SecCompanyFactsProvider().info(),)


@router.get("/fundamental-datasets", response_model=tuple[FundamentalDatasetSummary, ...])
def list_fundamental_datasets() -> tuple[FundamentalDatasetSummary, ...]:
    return tuple(_summary(item) for item in fundamental_repository.list())


@router.get("/fundamental-datasets/{dataset_id}", response_model=FundamentalDataset)
def get_fundamental_dataset(dataset_id: str) -> FundamentalDataset:
    dataset = fundamental_repository.get(dataset_id)
    if dataset is None:
        raise HTTPException(
            status_code=404, detail=f"Fundamental dataset '{dataset_id}' was not found"
        )
    return dataset


@router.post(
    "/fundamental-datasets/sec-companyfacts",
    response_model=FundamentalDatasetSummary,
    status_code=201,
)
async def download_sec_fundamentals(
    request: CreateFundamentalDataset,
) -> FundamentalDatasetSummary:
    provider = SecCompanyFactsProvider()
    try:
        observations = await provider.fetch(request)
        if not observations:
            raise ValueError("SEC returned no standardized observations for this request")
        dataset = fundamental_repository.create_dataset(
            name=request.name,
            provider="sec-companyfacts",
            observations=observations,
            start=request.start,
            end=request.end,
            retrieved_at=datetime.now(UTC),
            point_in_time_safe=True,
            restatement_safe=False,
            disclosure=(
                "NOT RESTATEMENT-SAFE: SEC Company Facts exposes filed dates, but the current API "
                "does not guarantee an immutable historical view of every post-acceptance "
                "correction."
            ),
        )
        return _summary(dataset)
    except (httpx.HTTPError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/fundamental-datasets/{dataset_id}/snapshot",
    response_model=FundamentalSnapshot,
)
def fundamental_snapshot(
    dataset_id: str,
    symbol: str,
    used_at: Annotated[datetime, Query()],
) -> FundamentalSnapshot:
    dataset = fundamental_repository.get(dataset_id)
    if dataset is None:
        raise HTTPException(
            status_code=404, detail=f"Fundamental dataset '{dataset_id}' was not found"
        )
    try:
        return fundamental_repository.snapshot(
            dataset,
            symbol=symbol.upper(),
            used_at=used_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/universes/static/{dataset_id}", response_model=HistoricalUniverse, status_code=201)
def create_static_universe(dataset_id: str) -> HistoricalUniverse:
    dataset = dataset_registry.get(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' was not found")
    return universe_repository.static_for_dataset(dataset)
