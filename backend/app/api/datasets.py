from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict

from app.datasets import (
    CompatibilityCheck,
    DatasetDefinition,
    DatasetFamily,
    DatasetImportRequest,
    DatasetPreview,
    DatasetRevisionDiff,
    DatasetValidationError,
    dataset_registry,
)
from app.strategies.definition import get_strategy_definition

router = APIRouter(prefix="/api", tags=["datasets"])


class CompatibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    dataset_id: str
    parameters: dict[str, int | float] = {}


class DatasetRowsPreview(BaseModel):
    dataset_id: str
    rows: tuple[dict[str, str | float], ...]


@router.get("/dataset-families", response_model=tuple[DatasetFamily, ...])
def list_dataset_families() -> tuple[DatasetFamily, ...]:
    return dataset_registry.families()


@router.get("/dataset-families/{dataset_family_id}", response_model=DatasetFamily)
def get_dataset_family(dataset_family_id: str) -> DatasetFamily:
    family = dataset_registry.get_family(dataset_family_id)
    if family is None:
        raise HTTPException(
            status_code=404, detail=f"Dataset family '{dataset_family_id}' was not found"
        )
    return family


@router.get(
    "/dataset-families/{dataset_family_id}/revisions",
    response_model=tuple[DatasetDefinition, ...],
)
def list_dataset_family_revisions(dataset_family_id: str) -> tuple[DatasetDefinition, ...]:
    family = dataset_registry.get_family(dataset_family_id)
    if family is None:
        raise HTTPException(
            status_code=404, detail=f"Dataset family '{dataset_family_id}' was not found"
        )
    return dataset_registry.revisions(dataset_family_id)


@router.get("/datasets/compare", response_model=DatasetRevisionDiff)
def compare_dataset_revisions(
    left: str = Query(min_length=1), right: str = Query(min_length=1)
) -> DatasetRevisionDiff:
    try:
        return dataset_registry.compare(left, right)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@router.get("/datasets", response_model=tuple[DatasetDefinition, ...])
def list_datasets() -> tuple[DatasetDefinition, ...]:
    return dataset_registry.list()


@router.get("/datasets/{dataset_id}", response_model=DatasetDefinition)
def get_dataset(dataset_id: str) -> DatasetDefinition:
    definition = dataset_registry.get(dataset_id)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' was not found")
    return definition


@router.get("/datasets/{dataset_id}/preview", response_model=DatasetRowsPreview)
def get_dataset_preview(dataset_id: str) -> DatasetRowsPreview:
    definition = dataset_registry.get(dataset_id)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' was not found")
    frames = dataset_registry.load_frames(dataset_id)
    rows: list[dict[str, str | float]] = []
    for frame in frames[:8]:
        for symbol, fields in frame.values.items():
            rendered: dict[str, str | float] = {
                "timestamp": frame.timestamp.isoformat(),
                "symbol": symbol,
            }
            rendered.update(fields)
            rows.append(rendered)
    return DatasetRowsPreview(dataset_id=dataset_id, rows=tuple(rows))


@router.post("/datasets/import/preview", response_model=DatasetPreview)
async def preview_dataset(file: Annotated[UploadFile, File(...)]) -> DatasetPreview:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Select a .csv file")
    try:
        return dataset_registry.preview(file.filename, await file.read())
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/datasets/import", response_model=DatasetDefinition, status_code=201)
def import_dataset(request: DatasetImportRequest) -> DatasetDefinition:
    try:
        return dataset_registry.commit(request)
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/compatibility-checks", response_model=CompatibilityCheck)
def check_compatibility(request: CompatibilityRequest) -> CompatibilityCheck:
    strategy = get_strategy_definition(request.strategy_id)
    if strategy is None:
        raise HTTPException(
            status_code=404, detail=f"Strategy '{request.strategy_id}' was not found"
        )
    dataset = dataset_registry.get(request.dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{request.dataset_id}' was not found")
    requirements = strategy.data_requirements
    missing_fields = tuple(
        field for field in requirements.required_fields if field not in dataset.fields
    )
    missing_symbols = tuple(
        symbol for symbol in requirements.symbols if symbol not in dataset.symbols
    )
    minimum_history = requirements.minimum_history
    if request.strategy_id == "pairs-trading" and "lookback" in request.parameters:
        minimum_history = int(request.parameters["lookback"]) * 2 - 1
    reasons: list[str] = []
    if not strategy.available:
        reasons.append(strategy.unavailable_reason or "Strategy runtime is unavailable")
    if missing_fields:
        reasons.append(f"Missing required fields: {', '.join(missing_fields)}")
    if missing_symbols:
        reasons.append(f"Missing required symbols: {', '.join(missing_symbols)}")
    if requirements.symbol_count is not None and len(dataset.symbols) != requirements.symbol_count:
        reasons.append(
            f"Strategy requires {requirements.symbol_count} symbols exactly; dataset provides "
            f"{len(dataset.symbols)}"
        )
    if dataset.synchronized_bar_count < minimum_history:
        reasons.append(
            f"Strategy requires at least {minimum_history} synchronized bars; dataset provides "
            f"{dataset.synchronized_bar_count}"
        )
    return CompatibilityCheck(
        strategy_id=request.strategy_id,
        dataset_id=request.dataset_id,
        compatible=not reasons,
        required_fields=requirements.required_fields,
        provided_fields=dataset.fields,
        required_symbol_count=requirements.symbol_count,
        provided_symbol_count=len(dataset.symbols),
        required_symbols=requirements.symbols,
        missing_symbols=missing_symbols,
        minimum_history=minimum_history,
        synchronized_bar_count=dataset.synchronized_bar_count,
        reasons=tuple(reasons),
    )
