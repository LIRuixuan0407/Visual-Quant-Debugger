from __future__ import annotations

from .models import AdapterDataset, AdapterStrategyManifest


def validate_dataset(dataset: AdapterDataset, manifest: AdapterStrategyManifest) -> None:
    requirements = manifest.data_requirements
    missing = sorted(set(requirements.required_fields) - set(dataset.fields))
    if missing:
        raise ValueError("Dataset is missing required framework fields: " + ", ".join(missing))
    if requirements.symbol_count is not None and len(dataset.symbols) != requirements.symbol_count:
        raise ValueError(
            f"Framework strategy requires exactly {requirements.symbol_count} symbol(s); "
            f"dataset provides {len(dataset.symbols)}"
        )
    if len(dataset.points) < requirements.minimum_history:
        raise ValueError(
            f"Framework strategy requires at least {requirements.minimum_history} bars; "
            f"dataset provides {len(dataset.points)}"
        )


def validate_parameters(
    supplied: dict[str, int | float], manifest: AdapterStrategyManifest
) -> dict[str, int | float]:
    definitions = {item.name: item for item in manifest.parameters}
    unknown = sorted(set(supplied) - set(definitions))
    if unknown:
        raise ValueError("Unknown framework strategy parameters: " + ", ".join(unknown))
    values = {item.name: item.default for item in manifest.parameters}
    values.update(supplied)
    for name, value in values.items():
        definition = definitions[name]
        if definition.value_type == "integer" and not isinstance(value, int):
            raise ValueError(f"Parameter '{name}' must be an integer")
        if value < definition.minimum:
            raise ValueError(f"Parameter '{name}' must be at least {definition.minimum}")
        if definition.maximum is not None and value > definition.maximum:
            raise ValueError(f"Parameter '{name}' must be at most {definition.maximum}")
    return values
