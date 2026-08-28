from fastapi import APIRouter, HTTPException

from app.universes import (
    CreateHistoricalUniverse,
    HistoricalUniverse,
    UniverseIntegrityError,
    universe_repository,
)

router = APIRouter(prefix="/api/universes", tags=["universes"])


@router.post("", response_model=HistoricalUniverse, status_code=201)
def create_universe(request: CreateHistoricalUniverse) -> HistoricalUniverse:
    try:
        return universe_repository.create(request)
    except (UniverseIntegrityError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=tuple[HistoricalUniverse, ...])
def list_universes() -> tuple[HistoricalUniverse, ...]:
    return universe_repository.list()


@router.get("/{universe_id}", response_model=HistoricalUniverse)
def get_universe(universe_id: str) -> HistoricalUniverse:
    try:
        record = universe_repository.get(universe_id)
    except (UniverseIntegrityError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"Universe '{universe_id}' was not found")
    return record
