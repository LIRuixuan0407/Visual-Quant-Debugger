import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api import router as replay_router
from app.api.workspaces import workspace_service
from app.paper import paper_store


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    workspace_service().ensure_default_workspace()
    await paper_store.service.start_recovered_tasks()
    yield
    await paper_store.service.shutdown()


app = FastAPI(
    title="Visual Quant Debugger API",
    version="0.30.0",
    description=(
        "Native and framework-backed Python strategies, real "
        "US-equity market data, Alpaca Paper Broker execution, recorded-feed validation, and "
        "durable Trace, Replay, Diagnose, P&L evidence, historical universes, and point-in-time "
        "price-volume and fundamental factor research, backend-built multi-factor portfolios, "
        "point-in-time-safe Walk-Forward stability research, and deterministic multi-factor "
        "relationship, redundancy, overlap, and incremental-information studies, plus a "
        "data-snooping-aware Strategy Discovery Workbench with immutable hypothesis revisions, "
        "content-verified Research Snapshots that freeze the complete research lineage, and "
        "controlled Experiment Compare reports spanning context, revisions, parameters, "
        "results, and recorded Run / Trace behavior, plus Research Integrity Guardrails that "
        "audit post-Holdout modification, future-data boundaries, dataset drift, strategy "
        "semantics, lineage completeness, and revision coverage, plus a unified, "
        "Idea-centered Research Workspace that connects the existing Data, Factor, "
        "Portfolio, Validation, Hypothesis, Native Strategy, and Run records, plus a "
        "revision-aware Global Research Lineage Explorer built only from explicit stored "
        "identifiers, plus deterministic local Global Search over existing lightweight "
        "research records, and immutable Data Quality and point-in-time audits over canonical "
        "Dataset, Factor, Fundamental, Universe, Run, and Trace evidence."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


def _demo_mode_enabled() -> bool:
    return os.environ.get("VQD_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


@app.middleware("http")
async def protect_public_demo(request: Request, call_next: RequestResponseEndpoint) -> Response:
    if (
        _demo_mode_enabled()
        and request.url.path.startswith("/api/")
        and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "This VQD instance is running in read-only demo mode. "
                    "Run VQD locally for research mutations and credential-backed integrations."
                )
            },
        )
    return await call_next(request)


app.include_router(replay_router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "visual-quant-debugger-backend"}


frontend_dist = Path(
    os.environ.get(
        "VQD_FRONTEND_DIST",
        Path(__file__).resolve().parents[2] / "frontend" / "dist",
    )
).resolve()

if frontend_dist.is_dir():
    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=assets_dir),
            name="frontend-assets",
        )

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)

        requested = (frontend_dist / path).resolve()
        if path and requested.is_relative_to(frontend_dist) and requested.is_file():
            return FileResponse(requested)

        return FileResponse(frontend_dist / "index.html")
