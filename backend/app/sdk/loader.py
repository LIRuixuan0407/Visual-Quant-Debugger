from __future__ import annotations

import hashlib
import importlib.util
import inspect
import re
import sys
import traceback as traceback_module
from dataclasses import dataclass
from pathlib import Path

from app.sdk.strategy import VQDStrategy

STRATEGY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class LoadedStrategy:
    strategy_class: type[VQDStrategy]
    source_path: Path
    source_fingerprint: str


class StrategyLoadError(ValueError):
    def __init__(self, path: Path, exception_type: str, message: str, traceback: str) -> None:
        super().__init__(f"{path}: {exception_type}: {message}")
        self.path = path
        self.exception_type = exception_type
        self.message = message
        self.traceback = traceback


def source_fingerprint(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def validate_strategy_class(strategy_class: type[VQDStrategy], path: Path) -> None:
    metadata = getattr(strategy_class, "metadata", None)
    if metadata is None:
        raise ValueError("Strategy class must declare StrategyMetadata as 'metadata'")
    if not STRATEGY_ID_PATTERN.fullmatch(metadata.strategy_id):
        raise ValueError(
            "strategy_id must start with a lowercase letter and contain only lowercase "
            "letters, digits, '.', '_' or '-' separators"
        )
    definitions = strategy_class.parameter_definitions()
    if len({item.name for item in definitions}) != len(definitions):
        raise ValueError("Strategy parameter names must be unique")
    strategy_class()


def load_strategy(path: str | Path, class_name: str | None = None) -> LoadedStrategy:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise StrategyLoadError(source, "FileNotFoundError", "Strategy file was not found", "")
    module_name = f"vqd_user_{hashlib.sha256(str(source).encode()).hexdigest()[:16]}"
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
            if issubclass(candidate, VQDStrategy)
            and candidate is not VQDStrategy
            and candidate.__module__ == module.__name__
        ]
        if class_name is not None:
            classes = [candidate for candidate in classes if candidate.__name__ == class_name]
        if not classes:
            detail = f" named '{class_name}'" if class_name else ""
            raise ValueError(f"No VQDStrategy subclass{detail} was found")
        if len(classes) > 1:
            names = ", ".join(candidate.__name__ for candidate in classes)
            raise ValueError(f"Multiple VQDStrategy subclasses found ({names}); specify a class")
        validate_strategy_class(classes[0], source)
        return LoadedStrategy(classes[0], source, source_fingerprint(source))
    except StrategyLoadError:
        raise
    except Exception as exc:
        rendered = "".join(traceback_module.format_exception(type(exc), exc, exc.__traceback__))
        raise StrategyLoadError(source, type(exc).__name__, str(exc), rendered) from exc
    finally:
        sys.modules.pop(module_name, None)
