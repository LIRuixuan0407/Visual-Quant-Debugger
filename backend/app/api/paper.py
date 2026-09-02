from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, cast

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator

from app.datasets import (
    DatasetDefinition,
    DatasetProvenance,
    DatasetValidationError,
    dataset_registry,
)
from app.market_data import (
    AlpacaStockReferenceClient,
    MarketDataTimeframe,
    MarketRegion,
    ProviderStatus,
    StockSecurity,
    StockSnapshot,
    TdxStockReferenceClient,
    alpaca_provider_status,
    parse_tdx_symbol,
    tdx_provider_status,
)
from app.market_data.models import HistoricalBarsRequest
from app.paper import (
    CreatePaperAccount,
    CreatePaperSession,
    PaperAccount,
    PaperAccountList,
    PaperOperationalHealth,
    PaperOperationLog,
    PaperRecoveryReport,
    PaperSessionList,
    PaperSessionNotFoundError,
    PaperSessionSnapshot,
    PaperTrace,
    RuntimeConsistencyReport,
    paper_store,
)
from app.sdk.registry import strategy_registry
from app.settings import integration_vault

market_router = APIRouter(prefix="/api/market-data", tags=["market-data"])
router = APIRouter(prefix="/api/paper-sessions", tags=["paper-sessions"])
operations_router = APIRouter(prefix="/api/paper/sessions", tags=["paper-operations"])
account_router = APIRouter(prefix="/api/paper-accounts", tags=["paper-accounts"])
dataset_refresh_router = APIRouter(prefix="/api/datasets", tags=["datasets"])


class HistoricalDatasetRequest(HistoricalBarsRequest):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = ""
    # Backward-compatible API default. The VQD UI explicitly chooses TDX.
    provider: Literal["tdx", "alpaca"] = "alpaca"
    feed: Literal["tdx", "iex", "sip"] = "iex"


class ProviderDatasetRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    end: datetime
    revision_reason: str | None = None

    @field_validator("end")
    @classmethod
    def aware_end(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Refresh end must be timezone-aware")
        return value


def stock_reference_client() -> AlpacaStockReferenceClient:
    credentials = integration_vault.resolve_alpaca()
    if credentials is None:
        return AlpacaStockReferenceClient()
    return AlpacaStockReferenceClient(
        api_key=credentials.api_key, secret_key=credentials.secret_key
    )


def tdx_reference_client() -> TdxStockReferenceClient:
    return TdxStockReferenceClient()


def _market_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        status = 404 if exc.response.status_code == 404 else 502
        return HTTPException(status_code=status, detail="Market-data provider request failed")
    return HTTPException(status_code=422, detail=str(exc))


@account_router.get("", response_model=PaperAccountList)
def list_paper_accounts() -> PaperAccountList:
    return paper_store.service.list_accounts()


@account_router.post("", response_model=PaperAccount, status_code=201)
def create_paper_account(request: CreatePaperAccount) -> PaperAccount:
    return paper_store.service.create_account(request)


@account_router.get("/{account_id}", response_model=PaperAccount)
def get_paper_account(account_id: str) -> PaperAccount:
    try:
        return paper_store.service.get_account(account_id)
    except (PaperSessionNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=404, detail=f"Paper account '{account_id}' was not found"
        ) from exc


@market_router.get("/providers", response_model=tuple[ProviderStatus, ...])
def list_market_data_providers() -> tuple[ProviderStatus, ...]:
    credentials = integration_vault.resolve_alpaca()
    alpaca = (
        alpaca_provider_status()
        if credentials is None
        else alpaca_provider_status(
            api_key=credentials.api_key,
            secret_key=credentials.secret_key,
            feed=credentials.feed,
        )
    )
    return (alpaca, tdx_provider_status())


@market_router.get("/stocks/search", response_model=tuple[StockSecurity, ...])
async def search_stocks(
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    provider: Literal["tdx", "alpaca"] = "alpaca",
    market: MarketRegion = "US",
) -> tuple[StockSecurity, ...]:
    try:
        if provider == "tdx":
            return await tdx_reference_client().search(q, region=market, limit=limit)
        if market != "US":
            raise ValueError("Alpaca supports US equities only")
        return await stock_reference_client().search(q, limit=limit)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        raise _market_error(exc) from exc


@market_router.get("/stocks/{symbol}/snapshot", response_model=StockSnapshot)
async def get_stock_snapshot(
    symbol: str,
    feed: str = "iex",
    provider: Literal["tdx", "alpaca"] = "alpaca",
    market: MarketRegion = "US",
) -> StockSnapshot:
    try:
        if provider == "tdx":
            return await tdx_reference_client().snapshot(symbol, region=market)
        if market != "US":
            raise ValueError("Alpaca supports US equities only")
        return await stock_reference_client().snapshot(symbol, feed=feed)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        raise _market_error(exc) from exc


@market_router.post("/historical-datasets", response_model=DatasetDefinition, status_code=201)
async def create_historical_dataset(request: HistoricalDatasetRequest) -> DatasetDefinition:
    try:
        if request.provider == "tdx" and request.feed != "tdx":
            raise ValueError("TDX historical requests must use the 'tdx' feed")
        if request.provider == "alpaca" and request.feed not in {"iex", "sip"}:
            raise ValueError("Alpaca historical requests must use the IEX or SIP feed")
        retrieved = datetime.now(UTC)
        security_names: dict[str, str] = {}
        if request.provider == "tdx":
            tdx_client = tdx_reference_client()
            bars = await tdx_client.historical_bars(
                request.symbols,
                request.start,
                request.end,
                timeframe=request.timeframe,
                region=request.market,
                adjustment=request.adjustment,
            )
            for symbol in request.symbols:
                try:
                    security = await tdx_client.get_security(symbol, region=request.market)
                except (RuntimeError, ValueError):
                    continue
                security_names[security.symbol] = security.name
            feed = "tdx"
        else:
            if request.market != "US":
                raise ValueError("Alpaca supports US equities only")
            alpaca_client = stock_reference_client()
            bars = await alpaca_client.historical_bars(
                request.symbols,
                request.start,
                request.end,
                timeframe=request.timeframe,
                feed=request.feed,
            )
            for symbol in request.symbols:
                security = await alpaca_client.get_security(symbol)
                security_names[security.symbol] = security.name
            feed = request.feed
        if not bars:
            raise DatasetValidationError("The provider returned no bars for this request")
        return dataset_registry.commit_provider_bars(
            name=request.name,
            bars=bars,
            provenance=DatasetProvenance(
                provider=request.provider,
                feed=feed,
                requested_symbols=tuple(sorted({item.symbol for item in bars})),
                requested_start=request.start,
                requested_end=request.end,
                retrieved_at=retrieved,
                market_timestamp_start=min(item.event_time for item in bars),
                market_timestamp_end=max(item.event_time for item in bars),
                market=request.market,
                adjustment=request.adjustment if request.provider == "tdx" else None,
            ),
            security_names=security_names,
        )
    except (httpx.HTTPError, RuntimeError, ValueError, DatasetValidationError) as exc:
        raise _market_error(exc) from exc


@dataset_refresh_router.post("/{dataset_id}/refresh", response_model=DatasetDefinition)
async def refresh_provider_dataset(
    dataset_id: str, request: ProviderDatasetRefreshRequest
) -> DatasetDefinition:
    dataset = dataset_registry.get(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' was not found")
    if dataset.source_type != "PROVIDER" or dataset.provenance is None:
        raise HTTPException(
            status_code=422, detail="Only provider-backed datasets with provenance can be refreshed"
        )
    if dataset.dataset_family_id is None:
        raise HTTPException(status_code=422, detail="Dataset is not assigned to a version family")
    family = dataset_registry.get_family(dataset.dataset_family_id)
    if family is None:
        raise HTTPException(status_code=422, detail="Dataset version family is missing")
    if family.latest_dataset_id != dataset.dataset_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "Only the latest provider revision can be refreshed; "
                f"latest is '{family.latest_dataset_id}'"
            ),
        )
    start = dataset.provenance.requested_start
    if request.end <= start:
        raise HTTPException(status_code=422, detail="Refresh end must be after the original start")
    if dataset.frequency not in {"1Min", "5Min", "15Min", "1Hour", "1Day"}:
        raise HTTPException(
            status_code=422,
            detail=f"Provider dataset timeframe '{dataset.frequency}' cannot be refreshed",
        )
    timeframe = cast(MarketDataTimeframe, dataset.frequency)
    try:
        retrieved = datetime.now(UTC)
        symbols = dataset.provenance.requested_symbols or dataset.symbols
        if dataset.provenance.provider == "tdx":
            tdx = tdx_reference_client()
            bars = await tdx.historical_bars(
                symbols,
                start,
                request.end,
                timeframe=timeframe,
                region=dataset.provenance.market,
                adjustment=dataset.provenance.adjustment or "NONE",
            )
        elif dataset.provenance.provider == "alpaca":
            alpaca = stock_reference_client()
            bars = await alpaca.historical_bars(
                symbols,
                start,
                request.end,
                timeframe=timeframe,
                feed=dataset.provenance.feed,
            )
        else:
            raise DatasetValidationError(
                f"Provider '{dataset.provenance.provider}' cannot be refreshed by this build"
            )
        if not bars:
            raise DatasetValidationError("The provider returned no bars for this refresh")
        return dataset_registry.commit_provider_bars(
            name=dataset.name,
            bars=bars,
            provenance=DatasetProvenance(
                provider=dataset.provenance.provider,
                feed=dataset.provenance.feed,
                requested_symbols=tuple(sorted({item.symbol for item in bars})),
                requested_start=start,
                requested_end=request.end,
                retrieved_at=retrieved,
                market_timestamp_start=min(item.event_time for item in bars),
                market_timestamp_end=max(item.event_time for item in bars),
                market=dataset.provenance.market,
                adjustment=dataset.provenance.adjustment,
            ),
            security_names=dataset.security_names,
            dataset_family_id=dataset.dataset_family_id,
            revision_reason=(
                request.revision_reason
                or f"Provider refresh through {request.end.date().isoformat()}"
            ),
        )
    except (httpx.HTTPError, RuntimeError, ValueError, DatasetValidationError) as exc:
        raise _market_error(exc) from exc


def _not_found(exc: PaperSessionNotFoundError) -> HTTPException:
    session_id = str(exc.args[0])
    return HTTPException(status_code=404, detail=f"Paper session '{session_id}' was not found")


@router.get("", response_model=PaperSessionList)
def list_paper_sessions() -> PaperSessionList:
    return paper_store.service.list()


@router.post("", response_model=PaperSessionSnapshot, status_code=201)
def create_paper_session(request: CreatePaperSession) -> PaperSessionSnapshot:
    try:
        registration = strategy_registry.get_registration(request.strategy_id)
        if registration is not None and registration.runtime_kind == "framework":
            raise ValueError(
                "Live Paper currently supports VQD Native Strategy only; Framework Adapters "
                "are historical research integrations."
            )
        if request.provider == "fake":
            raise ValueError("The fake market provider is available to backend tests only")
        if request.provider == "tdx":
            if request.feed != "tdx":
                raise ValueError("TDX paper sessions must use the 'tdx' feed")
            region = cast(
                MarketRegion,
                {
                    "CN_REGULAR": "CN",
                    "HK_REGULAR": "HK",
                    "US_REGULAR": "US",
                }[request.market_session],
            )
            for symbol in request.symbols:
                parse_tdx_symbol(symbol, region=region)
        if request.provider == "alpaca":
            if request.market_session != "US_REGULAR":
                raise ValueError("Alpaca market data supports US paper sessions only")
            if request.feed not in {"iex", "sip"}:
                raise ValueError("Alpaca paper sessions must use the IEX or SIP feed")
        if request.execution_mode == "ALPACA_PAPER" and request.provider != "alpaca":
            raise ValueError("Alpaca Paper execution requires the Alpaca provider")
        return paper_store.service.create(request)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{session_id}", response_model=PaperSessionSnapshot)
def get_paper_session(session_id: str) -> PaperSessionSnapshot:
    try:
        return paper_store.service.get(session_id)
    except PaperSessionNotFoundError as exc:
        raise _not_found(exc) from exc


async def _transition(
    session_id: str,
    operation: Callable[[str], Awaitable[PaperSessionSnapshot]],
) -> PaperSessionSnapshot:
    try:
        return await operation(session_id)
    except PaperSessionNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{session_id}/start", response_model=PaperSessionSnapshot)
async def start_paper_session(session_id: str) -> PaperSessionSnapshot:
    return await _transition(session_id, paper_store.service.start)


@router.post("/{session_id}/pause", response_model=PaperSessionSnapshot)
async def pause_paper_session(session_id: str) -> PaperSessionSnapshot:
    return await _transition(session_id, paper_store.service.pause)


@router.post("/{session_id}/resume", response_model=PaperSessionSnapshot)
async def resume_paper_session(session_id: str) -> PaperSessionSnapshot:
    return await _transition(session_id, paper_store.service.resume)


@router.post("/{session_id}/stop", response_model=PaperSessionSnapshot)
async def stop_paper_session(session_id: str) -> PaperSessionSnapshot:
    return await _transition(session_id, paper_store.service.stop)


@operations_router.get("/{session_id}/health", response_model=PaperOperationalHealth)
def get_paper_health(session_id: str) -> PaperOperationalHealth:
    try:
        return paper_store.service.health(session_id)
    except PaperSessionNotFoundError as exc:
        raise _not_found(exc) from exc


@operations_router.get("/{session_id}/operations", response_model=PaperOperationLog)
def get_paper_operations(session_id: str) -> PaperOperationLog:
    try:
        return paper_store.service.operations(session_id)
    except PaperSessionNotFoundError as exc:
        raise _not_found(exc) from exc


@operations_router.get("/{session_id}/recovery", response_model=PaperRecoveryReport)
def get_paper_recovery(session_id: str) -> PaperRecoveryReport:
    try:
        return paper_store.service.recovery(session_id)
    except PaperSessionNotFoundError as exc:
        raise _not_found(exc) from exc


@operations_router.post("/{session_id}/recover", response_model=PaperRecoveryReport)
async def recover_paper_session(session_id: str) -> PaperRecoveryReport:
    try:
        return await paper_store.service.recover(session_id)
    except PaperSessionNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{session_id}/orders/{order_id}/cancel", response_model=PaperSessionSnapshot)
async def cancel_paper_order(session_id: str, order_id: str) -> PaperSessionSnapshot:
    try:
        return await paper_store.service.cancel_order(session_id, order_id)
    except PaperSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Paper order was not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{session_id}/trace", response_model=PaperTrace)
def get_paper_trace(session_id: str) -> PaperTrace:
    try:
        return paper_store.service.trace(session_id)
    except PaperSessionNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get("/{session_id}/runtime-consistency", response_model=RuntimeConsistencyReport)
def get_runtime_consistency(session_id: str) -> RuntimeConsistencyReport:
    try:
        return paper_store.service.runtime_consistency(session_id)
    except PaperSessionNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get("/{session_id}/events")
async def stream_paper_session(session_id: str) -> StreamingResponse:
    try:
        paper_store.service.get(session_id)
    except PaperSessionNotFoundError as exc:
        raise _not_found(exc) from exc

    async def event_stream() -> AsyncIterator[str]:
        async for payload in paper_store.service.stream(session_id):
            yield ": keepalive\n\n" if not payload else f"event: snapshot\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
