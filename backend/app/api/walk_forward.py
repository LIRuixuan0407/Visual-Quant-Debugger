from fastapi import APIRouter, HTTPException

from app.datasets import dataset_registry
from app.factors import FactorResearchEngine, factor_research_repository
from app.research_ledger import research_ledger
from app.runs import run_ledger
from app.sdk.registry import strategy_registry
from app.walk_forward import (
    CreateWalkForwardResearch,
    WalkForwardEngine,
    WalkForwardResearchRecord,
    walk_forward_repository,
)

router = APIRouter(prefix="/api", tags=["walk-forward"])
engine = WalkForwardEngine(
    dataset_registry,
    factor_research_repository,
    FactorResearchEngine(dataset_registry),
    strategy_registry,
    run_ledger,
    research_ledger,
)


@router.get("/walk-forward", response_model=tuple[WalkForwardResearchRecord, ...])
def list_walk_forward() -> tuple[WalkForwardResearchRecord, ...]:
    return walk_forward_repository.list()


@router.get("/walk-forward/{walk_forward_id}", response_model=WalkForwardResearchRecord)
def get_walk_forward(walk_forward_id: str) -> WalkForwardResearchRecord:
    record = walk_forward_repository.get(walk_forward_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Walk-Forward research '{walk_forward_id}' was not found",
        )
    return record


@router.post("/walk-forward", response_model=WalkForwardResearchRecord, status_code=201)
def create_walk_forward(request: CreateWalkForwardResearch) -> WalkForwardResearchRecord:
    try:
        return walk_forward_repository.save(engine.create(request))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
