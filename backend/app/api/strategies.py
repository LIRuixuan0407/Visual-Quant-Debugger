import hashlib
import os
from contextlib import suppress
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
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


def _require_python_upload(request: Request) -> None:
    host = request.client.host if request.client is not None else ""
    trusted_container = os.environ.get("VQD_TRUST_PYTHON_UPLOADS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"} and not trusted_container:
        raise HTTPException(
            status_code=403,
            detail=(
                "Python uploads are disabled for non-local clients. "
                "Set VQD_TRUST_PYTHON_UPLOADS=true only when the VQD server "
                "is bound to a trusted local interface."
            ),
        )


def _store_uploaded_python(content: bytes, filename: str | None, *, kind: str) -> Path:
    if len(content) > 2 * 1024 * 1024:
        raise ValueError("Python source uploads are limited to 2 MiB")
    original = Path(filename or "uploaded.py")
    if original.suffix.lower() != ".py":
        raise ValueError("Only .py source files can be uploaded")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Python source must be UTF-8 encoded") from exc
    digest = hashlib.sha256(content).hexdigest()[:12]
    safe_stem = "".join(
        char if char.isalnum() or char in {"-", "_"} else "-" for char in original.stem
    )
    root = strategy_registry.workspace_root / ".vqd" / "user-code" / kind
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{safe_stem or kind}-{digest}.py"
    target.write_bytes(content)
    return target


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


@router.post("/upload", response_model=StrategyDefinition, status_code=201)
async def upload_strategy(
    request: Request,
    file: Annotated[UploadFile, File()],
    class_name: Annotated[str | None, Form()] = None,
) -> StrategyDefinition:
    _require_python_upload(request)
    target: Path | None = None
    try:
        target = _store_uploaded_python(await file.read(), file.filename, kind="strategies")
        registration = strategy_registry.add(target, class_name)
        definition = get_strategy_definition(registration.strategy_id)
        if definition is None:
            raise ValueError("Uploaded strategy could not be loaded from the Native Registry")
        return definition
    except (KeyError, TypeError, ValueError) as exc:
        if (
            target is not None
            and target.exists()
            and strategy_registry.get_registration(target.stem) is None
        ):
            # Registry failures should not leave arbitrary temporary uploads around.
            # Successful registrations intentionally retain the exact source for reproducibility.
            with suppress(OSError):
                target.unlink()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{strategy_id}", response_model=StrategyDefinition)
def get_strategy(strategy_id: str) -> StrategyDefinition:
    definition = get_strategy_definition(strategy_id)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' was not found")
    return definition
