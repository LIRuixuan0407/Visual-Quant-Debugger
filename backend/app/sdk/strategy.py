from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal, cast, overload

from app.sdk.models import ParameterSpec, ParameterValue, StrategyMetadata, TargetPortfolioIntent

if TYPE_CHECKING:
    from app.sdk.context import StrategyContext


class Parameter[T: (int, float)]:
    def __init__(self, spec: ParameterSpec) -> None:
        self.spec = spec
        self.storage_name = ""

    def __set_name__(self, owner: type[object], name: str) -> None:
        self.storage_name = f"__vqd_parameter_{name}"
        if self.spec.name and self.spec.name != name:
            raise ValueError(f"Parameter name '{self.spec.name}' must match attribute '{name}'")
        if not self.spec.name:
            self.spec = ParameterSpec(
                name=name,
                value_type=self.spec.value_type,
                default=self.spec.default,
                minimum=self.spec.minimum,
                maximum=self.spec.maximum,
                step=self.spec.step,
                description=self.spec.description,
                label=self.spec.label or name.replace("_", " ").title(),
                unit=self.spec.unit,
            )

    @overload
    def __get__(self, instance: None, owner: type[object]) -> Parameter[T]: ...

    @overload
    def __get__(self, instance: object, owner: type[object]) -> T: ...

    def __get__(self, instance: object | None, owner: type[object]) -> T | Parameter[T]:
        if instance is None:
            return self
        return cast(T, getattr(instance, self.storage_name, self.spec.default))

    def __set__(self, instance: object, value: T) -> None:
        setattr(instance, self.storage_name, value)


def parameter[T: (int, float)](
    *,
    default: T,
    minimum: T,
    maximum: T | None = None,
    step: T,
    description: str,
    label: str | None = None,
    unit: str = "",
) -> Parameter[T]:
    value_type: Literal["integer", "number"] = (
        "integer" if isinstance(default, int) and not isinstance(default, bool) else "number"
    )
    if isinstance(default, bool) or not isinstance(default, (int, float)):
        raise TypeError("Native parameters currently support only int and float values")
    return Parameter(
        ParameterSpec(
            name="",
            value_type=value_type,
            default=default,
            minimum=minimum,
            maximum=maximum,
            step=step,
            description=description,
            label=label or "",
            unit=unit,
        )
    )


class VQDStrategy(ABC):
    metadata: StrategyMetadata

    def initialize(self, context: StrategyContext) -> None:
        """Initialize strategy-owned incremental state before the first bar."""
        return None

    @abstractmethod
    def on_bar(self, context: StrategyContext) -> TargetPortfolioIntent | None:
        """Process exactly one newly available synchronized market frame."""

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
            raise ValueError(f"Unknown strategy parameters: {', '.join(unknown)}")
        for name, spec in definitions.items():
            value = values.get(name, spec.default)
            if spec.value_type == "integer" and (
                not isinstance(value, int) or isinstance(value, bool)
            ):
                raise TypeError(f"Parameter '{name}' must be an integer")
            if spec.value_type == "number" and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                raise TypeError(f"Parameter '{name}' must be numeric")
            if value < spec.minimum:
                raise ValueError(f"Parameter '{name}' must be at least {spec.minimum}")
            if spec.maximum is not None and value > spec.maximum:
                raise ValueError(f"Parameter '{name}' must be at most {spec.maximum}")
            setattr(self, name, value)
