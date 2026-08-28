from fastapi import APIRouter, HTTPException

from app.corporate_actions import (
    AdjustedMarketView,
    CorporateActionDataset,
    CorporateActionIntegrityError,
    CorporateActionService,
    CreateCorporateActionDataset,
    PriceAdjustmentPolicy,
    corporate_action_repository,
)
from app.datasets import dataset_registry

router = APIRouter(prefix="/api/corporate-actions", tags=["corporate-actions"])


def _service() -> CorporateActionService:
    return CorporateActionService(corporate_action_repository, dataset_registry)


@router.post("", response_model=CorporateActionDataset, status_code=201)
def create_corporate_action_dataset(
    request: CreateCorporateActionDataset,
) -> CorporateActionDataset:
    try:
        return _service().create(request)
    except (CorporateActionIntegrityError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=tuple[CorporateActionDataset, ...])
def list_corporate_action_datasets() -> tuple[CorporateActionDataset, ...]:
    return corporate_action_repository.list()


@router.get("/{dataset_id}", response_model=CorporateActionDataset)
def get_corporate_action_dataset(dataset_id: str) -> CorporateActionDataset:
    try:
        record = corporate_action_repository.get(dataset_id)
    except (CorporateActionIntegrityError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Corporate Action dataset '{dataset_id}' was not found",
        )
    return record


@router.get("/{dataset_id}/market-view/{market_dataset_id}", response_model=AdjustedMarketView)
def get_adjusted_market_view(
    dataset_id: str,
    market_dataset_id: str,
    policy: PriceAdjustmentPolicy = "RAW",
) -> AdjustedMarketView:
    try:
        return _service().market_view(market_dataset_id, dataset_id, policy)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except (CorporateActionIntegrityError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
