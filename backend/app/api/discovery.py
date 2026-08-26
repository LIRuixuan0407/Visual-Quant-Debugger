from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.datasets import dataset_registry
from app.discovery import (
    AttachHypothesisRun,
    CreateHypothesis,
    CreateHypothesisRevision,
    DiscoveryEngine,
    DiscoverySuggestion,
    ResearchHypothesis,
    hypothesis_repository,
)
from app.factor_relationships import factor_relationship_repository
from app.factors import FactorResearchEngine, factor_research_repository
from app.portfolio_lab import PortfolioResearchEngine, PortfolioStrategyFactory
from app.portfolio_lab.repository import portfolio_research_repository
from app.research_ledger import research_ledger
from app.sdk.registry import strategy_registry
from app.walk_forward import walk_forward_repository

router = APIRouter(prefix="/api", tags=["discovery"])
portfolio_engine = PortfolioResearchEngine(
    dataset_registry,
    factor_research_repository,
    FactorResearchEngine(dataset_registry),
)
engine = DiscoveryEngine(
    dataset_registry,
    factor_research_repository,
    factor_relationship_repository,
    walk_forward_repository,
    portfolio_research_repository,
    hypothesis_repository,
    portfolio_engine,
    PortfolioStrategyFactory(strategy_registry, factor_research_repository),
    research_ledger,
)


def _record(hypothesis_id: str) -> ResearchHypothesis:
    record = hypothesis_repository.get(hypothesis_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Hypothesis '{hypothesis_id}' was not found")
    return record


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/hypotheses", response_model=tuple[ResearchHypothesis, ...])
def list_hypotheses() -> tuple[ResearchHypothesis, ...]:
    return hypothesis_repository.list()


@router.get("/hypotheses/suggestions", response_model=tuple[DiscoverySuggestion, ...])
def list_discovery_suggestions() -> tuple[DiscoverySuggestion, ...]:
    try:
        return engine.suggestions()
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.get("/hypotheses/{hypothesis_id}", response_model=ResearchHypothesis)
def get_hypothesis(hypothesis_id: str) -> ResearchHypothesis:
    return _record(hypothesis_id)


@router.post("/hypotheses", response_model=ResearchHypothesis, status_code=201)
def create_hypothesis(request: CreateHypothesis) -> ResearchHypothesis:
    try:
        return engine.create(request)
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post(
    "/hypotheses/{hypothesis_id}/revisions",
    response_model=ResearchHypothesis,
    status_code=201,
)
def create_hypothesis_revision(
    hypothesis_id: str,
    request: CreateHypothesisRevision,
) -> ResearchHypothesis:
    try:
        return engine.create_revision(_record(hypothesis_id), request)
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post("/hypotheses/{hypothesis_id}/candidate", response_model=ResearchHypothesis)
def build_candidate(hypothesis_id: str) -> ResearchHypothesis:
    try:
        return engine.build_candidate(_record(hypothesis_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post("/hypotheses/{hypothesis_id}/validate", response_model=ResearchHypothesis)
def validate_hypothesis(hypothesis_id: str) -> ResearchHypothesis:
    try:
        return engine.validate(_record(hypothesis_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post(
    "/hypotheses/{hypothesis_id}/reveal-holdout",
    response_model=ResearchHypothesis,
)
def reveal_hypothesis_holdout(hypothesis_id: str) -> ResearchHypothesis:
    try:
        return engine.reveal_holdout(_record(hypothesis_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post("/hypotheses/{hypothesis_id}/strategy", response_model=ResearchHypothesis)
def create_hypothesis_strategy(hypothesis_id: str) -> ResearchHypothesis:
    try:
        return engine.create_strategy(_record(hypothesis_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post("/hypotheses/{hypothesis_id}/runs", response_model=ResearchHypothesis)
def attach_hypothesis_run(
    hypothesis_id: str,
    request: AttachHypothesisRun,
) -> ResearchHypothesis:
    try:
        return engine.attach_run(_record(hypothesis_id), request.run_id, request.trace_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc
