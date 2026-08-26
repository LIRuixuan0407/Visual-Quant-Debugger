from fastapi import APIRouter, HTTPException

from app.datasets import dataset_registry
from app.factor_relationships import (
    CreateFactorRelationship,
    FactorRelationshipEngine,
    FactorRelationshipRecord,
    factor_relationship_repository,
)
from app.factors import FactorResearchEngine, factor_research_repository
from app.research_ledger import research_ledger

router = APIRouter(prefix="/api", tags=["factor-relationships"])
engine = FactorRelationshipEngine(
    dataset_registry,
    factor_research_repository,
    FactorResearchEngine(dataset_registry),
    research_ledger,
)


@router.get(
    "/factor-relationships",
    response_model=tuple[FactorRelationshipRecord, ...],
)
def list_factor_relationships() -> tuple[FactorRelationshipRecord, ...]:
    return factor_relationship_repository.list()


@router.get(
    "/factor-relationships/{relationship_id}",
    response_model=FactorRelationshipRecord,
)
def get_factor_relationship(relationship_id: str) -> FactorRelationshipRecord:
    record = factor_relationship_repository.get(relationship_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Factor relationship '{relationship_id}' was not found",
        )
    return record


@router.post(
    "/factor-relationships",
    response_model=FactorRelationshipRecord,
    status_code=201,
)
def create_factor_relationship(
    request: CreateFactorRelationship,
) -> FactorRelationshipRecord:
    try:
        return factor_relationship_repository.save(engine.create(request))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
