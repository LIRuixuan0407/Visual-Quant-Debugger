from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as replay_router
from app.paper import paper_store


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await paper_store.service.start_recovered_tasks()
    yield
    await paper_store.service.shutdown()


app = FastAPI(
    title="Visual Quant Debugger API",
    version="0.23.0",
    description=(
        "Phase 0-23 API surface for native and framework-backed Python strategies, real "
        "US-equity market data, Alpaca Paper Broker execution, recorded-feed validation, and "
        "durable Trace, Replay, Diagnose, P&L evidence, historical universes, and point-in-time "
        "price-volume and fundamental factor research, backend-built multi-factor portfolios, "
        "point-in-time-safe Walk-Forward stability research, and deterministic multi-factor "
        "relationship, redundancy, overlap, and incremental-information studies, plus a "
        "data-snooping-aware Strategy Discovery Workbench with immutable hypothesis revisions."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(replay_router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "visual-quant-debugger-backend"}
