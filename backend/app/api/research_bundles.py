from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from app.datasets import dataset_registry
from app.research_bundles import (
    BundleExportRequest,
    BundleImportPreview,
    BundleImportResult,
    ResearchBundleError,
    ResearchBundleService,
)
from app.research_snapshots import research_snapshot_repository
from app.runs import run_store

router = APIRouter(prefix="/api/research-bundles", tags=["research-bundles"])


def _service() -> ResearchBundleService:
    return ResearchBundleService(
        research_snapshot_repository,
        dataset_registry,
        run_store.repository,
    )


@router.post("/export")
def export_research_bundle(request: BundleExportRequest) -> Response:
    try:
        manifest, content = _service().export(request)
    except (KeyError, ResearchBundleError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = f"{manifest.bundle_id}.vqd-bundle.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-VQD-Bundle-ID": manifest.bundle_id,
        },
    )


@router.post("/preview", response_model=BundleImportPreview)
async def preview_research_bundle(request: Request) -> BundleImportPreview:
    content = await request.body()
    if not content:
        raise HTTPException(status_code=422, detail="Research Bundle upload is empty")
    try:
        return _service().preview(content)
    except (ResearchBundleError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import/{preview_id}", response_model=BundleImportResult)
def import_research_bundle(preview_id: str) -> BundleImportResult:
    try:
        return _service().import_preview(preview_id)
    except (ResearchBundleError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
