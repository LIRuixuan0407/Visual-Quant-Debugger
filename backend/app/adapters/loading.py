from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

from .models import AdapterDataRequirements, AdapterStrategyManifest


def load_source_module(source_path: str) -> tuple[ModuleType, Path]:
    path = Path(source_path).expanduser().resolve()
    if not path.is_file() or path.suffix != ".py":
        raise ValueError(f"Strategy source must be an existing .py file: {path}")
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:16]
    module_name = f"vqd_framework_strategy_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create an import specification for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module, path


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "framework-strategy"


def strategy_manifest(
    module: ModuleType,
    source_path: Path,
    *,
    requirements: AdapterDataRequirements,
) -> AdapterStrategyManifest:
    declared = getattr(module, "VQD_ADAPTER_MANIFEST", None)
    if declared is not None:
        return AdapterStrategyManifest.model_validate(declared)
    return AdapterStrategyManifest(
        strategy_id=slug(source_path.stem),
        name=source_path.stem.replace("_", " ").strip().title(),
        description="Registered framework strategy using framework defaults.",
        data_requirements=requirements,
    )
