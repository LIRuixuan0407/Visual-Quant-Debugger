from __future__ import annotations

from abc import ABC, abstractmethod

from app.sdk.models import ParameterSpec, ParameterValue
from app.sdk.strategy import Parameter, parameter

from .context import FactorContext
from .models import FactorMetadata, FactorResult

factor_parameter = parameter


class VQDFactor(ABC):
    metadata: FactorMetadata

    @abstractmethod
    def compute(self, context: FactorContext, symbol: str) -> FactorResult:
        """Compute one symbol at the context's current point in time."""

    @classmethod
    def parameter_definitions(cls) -> tuple[ParameterSpec, ...]:
        definitions: list[ParameterSpec] = []
        for owner in reversed(cls.mro()):
            for name, value in vars(owner).items():
                if isinstance(value, Parameter):
                    spec = value.spec
                    definitions.append(
                        ParameterSpec(
                            name=name,
                            value_type=spec.value_type,
                            default=spec.default,
                            minimum=spec.minimum,
                            maximum=spec.maximum,
                            step=spec.step,
                            description=spec.description,
                            label=spec.label or name.replace("_", " ").title(),
                            unit=spec.unit,
                        )
                    )
        return tuple(definitions)

    def configure(self, values: dict[str, ParameterValue]) -> None:
        definitions = {item.name: item for item in self.parameter_definitions()}
        unknown = sorted(set(values) - set(definitions))
        if unknown:
            raise ValueError(f"Unknown factor parameters: {', '.join(unknown)}")
        for name, spec in definitions.items():
            value = values.get(name, spec.default)
            if spec.value_type == "integer" and (
                not isinstance(value, int) or isinstance(value, bool)
            ):
                raise TypeError(f"Factor parameter '{name}' must be an integer")
            if spec.value_type == "number" and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                raise TypeError(f"Factor parameter '{name}' must be numeric")
            if value < spec.minimum or (spec.maximum is not None and value > spec.maximum):
                raise ValueError(f"Factor parameter '{name}' is outside its supported range")
            setattr(self, name, value)
