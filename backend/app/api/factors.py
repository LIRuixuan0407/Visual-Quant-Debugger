from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.datasets import dataset_registry
from app.factors import (
    CreateFactorResearch,
    FactorDefinition,
    FactorInspection,
    FactorResearchEngine,
    FactorResearchRecord,
    FactorResearchSummary,
    FactorStrategyFactory,
    HistoricalMarketView,
    factor_research_repository,
)
from app.factors.models import CreateFactorStrategy, FactorStrategyArtifact
from app.factors.registry import factor_registry
from app.sdk.registry import strategy_registry

router = APIRouter(prefix="/api", tags=["factor-research"])
engine = FactorResearchEngine(dataset_registry)
strategy_factory = FactorStrategyFactory(strategy_registry)


def _record(research_id: str) -> FactorResearchRecord:
    record = factor_research_repository.get(research_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Factor research '{research_id}' was not found"
        )
    return record


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


class ImportFactorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    class_name: str | None = None


class ImportFactorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor: FactorDefinition
    checks: tuple[str, ...]
    security_model: str


def _require_local(request: Request) -> None:
    host = request.client.host if request.client is not None else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=403,
            detail="Local Python registration is only available from the local VQD interface",
        )


@router.get("/factors", response_model=tuple[FactorDefinition, ...])
def list_factors(
    category: str | None = None,
    data_source: str | None = None,
) -> tuple[FactorDefinition, ...]:
    return tuple(
        item
        for item in factor_registry.list_definitions()
        if (category is None or item.category == category.upper())
        and (data_source is None or item.data_source == data_source.upper())
    )


@router.post("/factors/import", response_model=ImportFactorResponse, status_code=201)
def import_factor(payload: ImportFactorRequest, request: Request) -> ImportFactorResponse:
    _require_local(request)
    try:
        registration = factor_registry.add(payload.path, payload.class_name)
        definition = factor_registry.definition(registration.factor_id)
        return ImportFactorResponse(
            factor=definition,
            checks=(
                "SYNTAX",
                "VQD_FACTOR_SDK",
                "FACTOR_ID",
                "PARAMETERS",
                "REQUIRED_FIELDS",
                "LOOKBACK",
                "POINT_IN_TIME_CONTEXT",
                "SOURCE_FINGERPRINT",
            ),
            security_model=(
                "Trusted local Python: the source executes with the backend process permissions; "
                "VQD does not provide a sandbox."
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.get("/factor-research", response_model=tuple[FactorResearchSummary, ...])
def list_factor_research() -> tuple[FactorResearchSummary, ...]:
    return factor_research_repository.list()


@router.post("/factor-research", response_model=FactorResearchRecord, status_code=201)
def create_factor_research(request: CreateFactorResearch) -> FactorResearchRecord:
    try:
        return factor_research_repository.save(engine.create(request))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.get("/factor-research/{research_id}", response_model=FactorResearchRecord)
def get_factor_research(research_id: str) -> FactorResearchRecord:
    return _record(research_id)


@router.post("/factor-research/{research_id}/validate", response_model=FactorResearchRecord)
def validate_factor_research(research_id: str) -> FactorResearchRecord:
    try:
        return factor_research_repository.save(engine.reveal(_record(research_id), "VALIDATION"))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post("/factor-research/{research_id}/reveal-holdout", response_model=FactorResearchRecord)
def reveal_factor_holdout(research_id: str) -> FactorResearchRecord:
    try:
        return factor_research_repository.save(engine.reveal(_record(research_id), "HOLDOUT"))
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.get("/factor-research/{research_id}/inspect", response_model=FactorInspection)
def inspect_factor(
    research_id: str,
    symbol: str,
    timestamp: datetime,
) -> FactorInspection:
    try:
        return engine.inspect(_record(research_id), symbol, timestamp)
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post(
    "/factor-research/{research_id}/strategy",
    response_model=FactorStrategyArtifact,
    status_code=201,
)
def create_factor_strategy(
    research_id: str, request: CreateFactorStrategy
) -> FactorStrategyArtifact:
    record = _record(research_id)
    try:
        artifact = strategy_factory.create(record, request)
        factor_research_repository.save(record.model_copy(update={"strategy": artifact}))
        return artifact
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.get("/historical-market/{dataset_id}", response_model=HistoricalMarketView)
def historical_market(
    dataset_id: str,
    as_of: Annotated[datetime, Query()],
    symbol: str | None = None,
    fundamental_dataset_id: str | None = None,
    universe_id: str | None = None,
) -> HistoricalMarketView:
    try:
        return engine.historical_market(
            dataset_id,
            as_of,
            symbol,
            fundamental_dataset_id=fundamental_dataset_id,
            universe_id=universe_id,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc
