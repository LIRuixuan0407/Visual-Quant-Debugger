from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.datasets import dataset_registry
from app.factors import FactorResearchEngine, factor_research_repository
from app.portfolio_lab import (
    CreatePortfolioResearch,
    PortfolioResearchEngine,
    PortfolioResearchRecord,
    PortfolioResearchSummary,
    PortfolioStrategyArtifact,
    PortfolioStrategyFactory,
    portfolio_research_repository,
)
from app.research_ledger import ResearchLedgerEntry, research_ledger
from app.sdk.registry import strategy_registry

router = APIRouter(prefix="/api", tags=["portfolio-lab"])
factor_engine = FactorResearchEngine(dataset_registry)
engine = PortfolioResearchEngine(
    dataset_registry,
    factor_research_repository,
    factor_engine,
)
strategy_factory = PortfolioStrategyFactory(strategy_registry, factor_research_repository)


def _record(research_id: str) -> PortfolioResearchRecord:
    record = portfolio_research_repository.get(research_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Portfolio research '{research_id}' was not found",
        )
    return record


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/portfolio-research", response_model=tuple[PortfolioResearchSummary, ...])
def list_portfolio_research() -> tuple[PortfolioResearchSummary, ...]:
    return portfolio_research_repository.list()


@router.post("/portfolio-research", response_model=PortfolioResearchRecord, status_code=201)
def create_portfolio_research(request: CreatePortfolioResearch) -> PortfolioResearchRecord:
    try:
        return portfolio_research_repository.save(engine.create(request))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.get("/portfolio-research/{research_id}", response_model=PortfolioResearchRecord)
def get_portfolio_research(research_id: str) -> PortfolioResearchRecord:
    return _record(research_id)


@router.post(
    "/portfolio-research/{research_id}/validate",
    response_model=PortfolioResearchRecord,
)
def validate_portfolio_research(research_id: str) -> PortfolioResearchRecord:
    try:
        return portfolio_research_repository.save(engine.reveal(_record(research_id), "VALIDATION"))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post(
    "/portfolio-research/{research_id}/reveal-holdout",
    response_model=PortfolioResearchRecord,
)
def reveal_portfolio_holdout(research_id: str) -> PortfolioResearchRecord:
    try:
        return portfolio_research_repository.save(engine.reveal(_record(research_id), "HOLDOUT"))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post(
    "/portfolio-research/{research_id}/strategy",
    response_model=PortfolioStrategyArtifact,
    status_code=201,
)
def create_portfolio_strategy(research_id: str) -> PortfolioStrategyArtifact:
    record = _record(research_id)
    if record.revealed_stage == "RESEARCH":
        raise HTTPException(status_code=422, detail="Reveal Validation before creating a strategy")
    try:
        artifact = strategy_factory.create(record)
        portfolio_research_repository.save(record.model_copy(update={"strategy": artifact}))
        research_ledger.save(
            ResearchLedgerEntry.new(
                entry_id=f"ledger-strategy-{artifact.strategy_id}",
                kind="PORTFOLIO",
                artifact_id=record.portfolio_research_id,
                revision=2,
                dataset_ids=(record.dataset_id,),
                dataset_fingerprints=(record.dataset_fingerprint,),
                factor_ids=record.factor_ids,
                strategy_id=artifact.strategy_id,
                strategy_revision=artifact.source_fingerprint,
                known_evidence=tuple(item.stage for item in record.stages),
                metadata={"event": "CREATE_NATIVE_STRATEGY"},
            )
        )
        return artifact
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc
