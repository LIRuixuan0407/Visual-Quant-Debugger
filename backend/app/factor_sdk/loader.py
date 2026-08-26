from __future__ import annotations

import hashlib
import importlib.util
import inspect
import re
import sys
import traceback as traceback_module
from dataclasses import dataclass
from pathlib import Path

from .factor import VQDFactor

FACTOR_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class LoadedFactor:
    factor_class: type[VQDFactor]
    source_path: Path
    source_fingerprint: str


class FactorLoadError(ValueError):
    def __init__(self, path: Path, exception_type: str, message: str, traceback: str) -> None:
        super().__init__(f"{path}: {exception_type}: {message}")
        self.path = path
        self.exception_type = exception_type
        self.message = message
        self.traceback = traceback


def source_fingerprint(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def validate_factor_class(factor_class: type[VQDFactor]) -> None:
    metadata = getattr(factor_class, "metadata", None)
    if metadata is None:
        raise ValueError("Factor class must declare FactorMetadata as 'metadata'")
    if not FACTOR_ID_PATTERN.fullmatch(metadata.factor_id):
        raise ValueError(
            "factor_id must start with a lowercase letter and use lowercase letters, digits, "
            "'.', '_' or '-' separators"
        )
    if (
        not metadata.name.strip()
        or not metadata.version.strip()
        or not metadata.description.strip()
    ):
        raise ValueError("Factor name, version, and description are required")
    if metadata.lookback < 0:
        raise ValueError("Factor lookback cannot be negative")
    if metadata.data_source not in {"MARKET", "FUNDAMENTAL", "MIXED"}:
        raise ValueError("Factor data_source must be MARKET, FUNDAMENTAL, or MIXED")
    if metadata.category not in {
        "PRICE_VOLUME",
        "VALUE",
        "QUALITY",
        "GROWTH",
        "LEVERAGE",
        "MIXED",
    }:
        raise ValueError("Unsupported factor category")
    if not all(field and field == field.lower() for field in metadata.required_fields):
        raise ValueError("required_fields must contain non-empty lowercase market fields")
    if not all(field and field == field.lower() for field in metadata.required_fundamental_fields):
        raise ValueError("required_fundamental_fields must contain lowercase field names")
    if metadata.data_source == "MARKET" and metadata.required_fundamental_fields:
        raise ValueError("MARKET factors cannot declare fundamental fields")
    if metadata.data_source == "FUNDAMENTAL" and not metadata.required_fundamental_fields:
        raise ValueError("FUNDAMENTAL factors must declare required_fundamental_fields")
    definitions = factor_class.parameter_definitions()
    if len({item.name for item in definitions}) != len(definitions):
        raise ValueError("Factor parameter names must be unique")
    factor_class()


def load_factor(path: str | Path, class_name: str | None = None) -> LoadedFactor:
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".py":
        raise FactorLoadError(source, "ValueError", "Factor source must be a .py file", "")
    if not source.is_file():
        raise FactorLoadError(source, "FileNotFoundError", "Factor file was not found", "")
    module_name = f"vqd_factor_{hashlib.sha256(str(source).encode()).hexdigest()[:16]}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise ImportError("Python could not create a module loader")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        classes = [
            candidate
            for _, candidate in inspect.getmembers(module, inspect.isclass)
            if issubclass(candidate, VQDFactor)
            and candidate is not VQDFactor
            and candidate.__module__ == module.__name__
        ]
        if class_name is not None:
            classes = [candidate for candidate in classes if candidate.__name__ == class_name]
        if not classes:
            detail = f" named '{class_name}'" if class_name else ""
            raise ValueError(f"No VQDFactor subclass{detail} was found")
        if len(classes) > 1:
            names = ", ".join(candidate.__name__ for candidate in classes)
            raise ValueError(f"Multiple VQDFactor subclasses found ({names}); specify a class")
        validate_factor_class(classes[0])
        return LoadedFactor(classes[0], source, source_fingerprint(source))
    except FactorLoadError:
        raise
    except Exception as exc:
        rendered = "".join(traceback_module.format_exception(type(exc), exc, exc.__traceback__))
        raise FactorLoadError(source, type(exc).__name__, str(exc), rendered) from exc
    finally:
        sys.modules.pop(module_name, None)
