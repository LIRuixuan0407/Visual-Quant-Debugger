from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.sdk.registry import strategy_registry
from app.strategies.definition import (
    StrategyDefinition,
    get_strategy_definition,
    list_strategy_definitions,
)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class ImportStrategyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    class_name: str | None = None


def _require_local(request: Request) -> None:
    host = request.client.host if request.client is not None else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=403,
            detail="Local Python registration is only available from the local VQD interface",
        )


@router.get("", response_model=tuple[StrategyDefinition, ...])
def list_strategies() -> tuple[StrategyDefinition, ...]:
    return list_strategy_definitions()


@router.post("/import", response_model=StrategyDefinition, status_code=201)
def import_strategy(payload: ImportStrategyRequest, request: Request) -> StrategyDefinition:
    _require_local(request)
    try:
        registration = strategy_registry.add(payload.path, payload.class_name)
        definition = get_strategy_definition(registration.strategy_id)
        if definition is None:
            raise ValueError("Imported strategy could not be loaded from the Native Registry")
        return definition
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{strategy_id}", response_model=StrategyDefinition)
def get_strategy(strategy_id: str) -> StrategyDefinition:
    definition = get_strategy_definition(strategy_id)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' was not found")
    return definition
