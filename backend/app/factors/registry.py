from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.factor_sdk.factor import VQDFactor
from app.factor_sdk.loader import FactorLoadError, LoadedFactor, load_factor, source_fingerprint
from app.workspace import default_workspace_root

from .catalog import FACTOR_CATALOG
from .models import FactorDefinition, FactorParameter


class FactorRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    factor_id: str
    source_path: str
    class_name: str
    registered_at: datetime
    source_fingerprint: str


class FactorRegistry:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.registry_path = self.workspace_root / ".vqd" / "factors.json"
        self.catalog_path = Path(__file__).parents[1] / "factors" / "catalog.py"
        self._loaded: dict[str, LoadedFactor] = {}
        self._definitions: dict[str, FactorDefinition] = {}

    def _read(self) -> tuple[FactorRegistration, ...]:
        if not self.registry_path.exists():
            return ()
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
            return tuple(FactorRegistration.model_validate(item) for item in payload)
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid factor registry at {self.registry_path}: {exc}") from exc

    def _write(self, registrations: tuple[FactorRegistration, ...]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump(mode="json") for item in registrations]
        temporary = self.registry_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.registry_path)

    @staticmethod
    def _definition(loaded: LoadedFactor) -> FactorDefinition:
        metadata = loaded.factor_class.metadata
        parameters = tuple(
            FactorParameter(
                key=item.name,
                label=item.label,
                description=item.description,
                default_value=item.default,
                minimum=item.minimum,
                maximum=item.maximum,
                step=item.step,
                unit=item.unit,
            )
            for item in loaded.factor_class.parameter_definitions()
        )
        return FactorDefinition(
            factor_id=metadata.factor_id,
            name=metadata.name,
            version=metadata.version,
            formula=metadata.formula,
            description=metadata.description,
            parameters=parameters,
            required_fields=metadata.required_fields,
            required_fundamental_fields=metadata.required_fundamental_fields,
            lookback=metadata.lookback,
            availability=metadata.availability,
            direction=metadata.direction,
            category=metadata.category,
            data_source=metadata.data_source,
            origin="CUSTOM",
            source_path=str(loaded.source_path),
            source_fingerprint=loaded.source_fingerprint,
        )

    def list_registrations(self) -> tuple[FactorRegistration, ...]:
        return self._read()

    def list_definitions(self) -> tuple[FactorDefinition, ...]:
        catalog_fingerprint = source_fingerprint(self.catalog_path)
        built_ins = tuple(
            item.model_copy(
                update={
                    "origin": "BUILT_IN",
                    "source_path": str(self.catalog_path),
                    "source_fingerprint": catalog_fingerprint,
                }
            )
            for item in FACTOR_CATALOG
        )
        return (*built_ins, *(self.definition(item.factor_id) for item in self._read()))

    def add(self, path: str | Path, class_name: str | None = None) -> FactorRegistration:
        loaded = load_factor(path, class_name)
        factor_id = loaded.factor_class.metadata.factor_id
        registrations = self._read()
        if any(item.factor_id == factor_id for item in FACTOR_CATALOG) or any(
            item.factor_id == factor_id for item in registrations
        ):
            raise ValueError(f"Factor id '{factor_id}' is already registered")
        registration = FactorRegistration(
            factor_id=factor_id,
            source_path=str(loaded.source_path),
            class_name=loaded.factor_class.__name__,
            registered_at=datetime.now(UTC),
            source_fingerprint=loaded.source_fingerprint,
        )
        self._write((*registrations, registration))
        self._loaded[factor_id] = loaded
        self._definitions[factor_id] = self._definition(loaded)
        return registration

    def get_registration(self, factor_id: str) -> FactorRegistration | None:
        return next((item for item in self._read() if item.factor_id == factor_id), None)

    def load(self, factor_id: str) -> LoadedFactor:
        cached = self._loaded.get(factor_id)
        if cached is not None:
            return cached
        registration = self.get_registration(factor_id)
        if registration is None:
            raise KeyError(f"Custom factor '{factor_id}' is not registered")
        source = Path(registration.source_path)
        current_fingerprint = source_fingerprint(source)
        if current_fingerprint != registration.source_fingerprint:
            raise FactorLoadError(
                source,
                "FactorSourceChanged",
                "The source changed after registration; import it as a new version",
                "",
            )
        loaded = load_factor(registration.source_path, registration.class_name)
        if loaded.factor_class.metadata.factor_id != registration.factor_id:
            raise FactorLoadError(
                loaded.source_path,
                "FactorIdChanged",
                f"Registered id is '{registration.factor_id}', source now declares "
                f"'{loaded.factor_class.metadata.factor_id}'",
                "",
            )
        self._loaded[factor_id] = loaded
        return loaded

    def definition(self, factor_id: str) -> FactorDefinition:
        cached = self._definitions.get(factor_id)
        if cached is not None:
            return cached
        built_in = next((item for item in FACTOR_CATALOG if item.factor_id == factor_id), None)
        if built_in is not None:
            definition = built_in.model_copy(
                update={
                    "origin": "BUILT_IN",
                    "source_path": str(self.catalog_path),
                    "source_fingerprint": source_fingerprint(self.catalog_path),
                }
            )
        else:
            definition = self._definition(self.load(factor_id))
        self._definitions[factor_id] = definition
        return definition

    def instantiate(
        self, factor_id: str, parameters: dict[str, int | float]
    ) -> tuple[VQDFactor, LoadedFactor]:
        loaded = self.load(factor_id)
        factor = loaded.factor_class()
        factor.configure(parameters)
        return factor, loaded


factor_registry = FactorRegistry()
