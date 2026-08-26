from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.adapters.models import AdapterStrategyManifest
from app.adapters.registry import adapter_registry
from app.adapters.runner import FrameworkRunner
from app.sdk.loader import LoadedStrategy, StrategyLoadError, load_strategy, source_fingerprint
from app.sdk.strategy import VQDStrategy
from app.strategies.pairs import PairsTradingStrategy
from app.workspace import default_workspace_root


class StrategyRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    source_path: str
    class_name: str
    registered_at: datetime
    source_fingerprint: str
    runtime_kind: str = "native"
    adapter_id: str | None = None
    adapter_version: str | None = None
    framework_name: str | None = None
    framework_version: str | None = None
    adapter_manifest: AdapterStrategyManifest | None = None


class StrategyRegistry:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.registry_path = self.workspace_root / ".vqd" / "strategies.json"

    def _read(self) -> tuple[StrategyRegistration, ...]:
        if not self.registry_path.exists():
            return ()
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
            return tuple(StrategyRegistration.model_validate(item) for item in payload)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"Invalid strategy registry at {self.registry_path}: {exc}") from exc

    def _write(self, registrations: tuple[StrategyRegistration, ...]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump(mode="json") for item in registrations]
        temporary = self.registry_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.registry_path)

    def list(self) -> tuple[StrategyRegistration, ...]:
        return self._read()

    def add(
        self,
        path: str | Path,
        class_name: str | None = None,
        *,
        framework: str | None = None,
        entrypoint: str | None = None,
    ) -> StrategyRegistration:
        if framework is not None:
            normalized = adapter_registry.normalize_id(framework)
            selected_entrypoint = entrypoint or class_name
            if not selected_entrypoint:
                flag = "--class" if normalized == "backtesting.py" else "--entrypoint"
                raise ValueError(f"Framework registration requires {flag}")
            source_path = Path(path).expanduser().resolve()
            inspection = FrameworkRunner().inspect(
                normalized, str(source_path), selected_entrypoint
            )
            if inspection.manifest is None:
                raise ValueError("Framework adapter did not return a typed strategy manifest")
            strategy_id = inspection.manifest.strategy_id
            registrations = self._read()
            if strategy_id == PairsTradingStrategy.metadata.strategy_id or any(
                item.strategy_id == strategy_id for item in registrations
            ):
                raise ValueError(f"Strategy id '{strategy_id}' is already registered")
            registration = StrategyRegistration(
                strategy_id=strategy_id,
                source_path=str(source_path),
                class_name=selected_entrypoint,
                registered_at=datetime.now(UTC),
                source_fingerprint=source_fingerprint(source_path),
                runtime_kind="framework",
                adapter_id=inspection.adapter_id,
                adapter_version=inspection.adapter_version,
                framework_name=inspection.framework_name,
                framework_version=inspection.framework_version,
                adapter_manifest=inspection.manifest,
            )
            self._write((*registrations, registration))
            return registration
        loaded = load_strategy(path, class_name)
        strategy_id = loaded.strategy_class.metadata.strategy_id
        registrations = self._read()
        if strategy_id == PairsTradingStrategy.metadata.strategy_id or any(
            item.strategy_id == strategy_id for item in registrations
        ):
            raise ValueError(f"Strategy id '{strategy_id}' is already registered")
        registration = StrategyRegistration(
            strategy_id=strategy_id,
            source_path=str(loaded.source_path),
            class_name=loaded.strategy_class.__name__,
            registered_at=datetime.now(UTC),
            source_fingerprint=loaded.source_fingerprint,
        )
        self._write((*registrations, registration))
        return registration

    def get_registration(self, strategy_id: str) -> StrategyRegistration | None:
        return next((item for item in self._read() if item.strategy_id == strategy_id), None)

    def remove(self, strategy_id: str) -> StrategyRegistration:
        registrations = self._read()
        removed = next((item for item in registrations if item.strategy_id == strategy_id), None)
        if removed is None:
            raise KeyError(f"Strategy '{strategy_id}' is not registered")
        self._write(tuple(item for item in registrations if item.strategy_id != strategy_id))
        return removed

    def load(self, strategy_id: str) -> LoadedStrategy:
        if strategy_id == PairsTradingStrategy.metadata.strategy_id:
            source_path = Path(__file__).parents[1] / "strategies" / "pairs.py"
            from app.sdk.loader import source_fingerprint

            return LoadedStrategy(
                PairsTradingStrategy, source_path, source_fingerprint(source_path)
            )
        registration = next(
            (item for item in self._read() if item.strategy_id == strategy_id), None
        )
        if registration is None:
            raise KeyError(f"Strategy '{strategy_id}' is not registered")
        if registration.runtime_kind == "framework":
            raise StrategyLoadError(
                Path(registration.source_path),
                "FrameworkStrategy",
                "Framework strategies execute through FrameworkRunner, not VQDStrategy runtime",
                "",
            )
        loaded = load_strategy(registration.source_path, registration.class_name)
        if loaded.strategy_class.metadata.strategy_id != registration.strategy_id:
            raise StrategyLoadError(
                loaded.source_path,
                "StrategyIdChanged",
                f"Registered id is '{registration.strategy_id}', source now declares "
                f"'{loaded.strategy_class.metadata.strategy_id}'",
                "",
            )
        return loaded

    def instantiate(self, strategy_id: str) -> tuple[VQDStrategy, LoadedStrategy]:
        loaded = self.load(strategy_id)
        return loaded.strategy_class(), loaded


strategy_registry = StrategyRegistry()
