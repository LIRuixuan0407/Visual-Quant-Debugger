from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from pathlib import Path

from app.sdk.registry import StrategyRegistry
from app.workspace import default_workspace_root

from .models import (
    CreateFactorStrategy,
    FactorResearchRecord,
    FactorStrategyArtifact,
)
from .registry import FactorRegistry, factor_registry


class FactorStrategyFactory:
    def __init__(
        self,
        strategy_registry: StrategyRegistry,
        workspace_root: str | Path | None = None,
        factors: FactorRegistry | None = None,
    ) -> None:
        self.strategy_registry = strategy_registry
        self.factors = factors or factor_registry
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "generated-strategies"

    def _source(
        self,
        record: FactorResearchRecord,
        request: CreateFactorStrategy,
        strategy_id: str,
    ) -> str:
        class_name = "FactorStrategy_" + strategy_id.removeprefix("factor-").replace("-", "_")
        symbols = repr(record.universe)
        component_definitions = [
            self.factors.definition(item.factor_id) for item in record.components
        ]
        required_fields = repr(
            tuple(
                sorted(
                    {
                        "close",
                        *record.factor.required_fields,
                        *(
                            field
                            for item in component_definitions
                            for field in item.required_fields
                        ),
                    }
                )
            )
        )
        lookback = int(record.parameters.get("lookback", record.factor.lookback or 2))
        component_lookbacks = [
            int(item.parameters.get("lookback", definition.lookback))
            for item, definition in zip(record.components, component_definitions, strict=True)
        ]
        minimum_history = max([lookback + 1, *(value + 1 for value in component_lookbacks), 2])
        top_count = max(1, math.ceil(len(record.universe) * request.long_percent / 100))
        filter_value = repr(request.max_volatility)
        components = repr(tuple(item.model_dump(mode="python") for item in record.components))
        factor_setup = (
            f"""factor_map = compute_runtime_mixed_factors(
            context,
            components={components},
            research_id={record.research_id!r},
            fundamental_dataset_id={record.fundamental_dataset_id!r},
        )
        factors = [factor_map[symbol] for symbol in context.symbols]"""
            if record.factor.factor_id == "mixed"
            else f"""factors = [
            compute_runtime_factor(
                context,
                factor_id={record.factor.factor_id!r},
                symbol=symbol,
                lookback=self.lookback,
                fundamental_dataset_id={record.fundamental_dataset_id!r},
                max_age_days={int(record.parameters.get("max_age_days", 550))},
                parameters={record.parameters!r},
            )
            for symbol in context.symbols
        ]"""
        )
        return f"""from __future__ import annotations

from app.factors.runtime import (
    compute_runtime_factor,
    compute_runtime_mixed_factors,
    runtime_volatility,
)
from app.sdk import DataRequirements, StrategyContext, StrategyMetadata, VQDStrategy, parameter


class {class_name}(VQDStrategy):
    metadata = StrategyMetadata(
        strategy_id={strategy_id!r},
        name={("Factor Strategy · " + record.factor.name)!r},
        version="1.0",
        description=(
            {("Generated from " + record.research_id + "; long-only cross-sectional rank.")!r}
        ),
        data_requirements=DataRequirements(
            required_fields={required_fields},
            symbols={symbols},
            minimum_history={minimum_history},
        ),
    )
    lookback = parameter(
        default={lookback}, minimum=2, maximum=252, step=1,
        description="Trailing factor lookback", label="Lookback", unit="bars",
    )
    rebalance_bars = parameter(
        default={request.rebalance_bars}, minimum=1, maximum=63, step=1,
        description="Bars between portfolio refreshes", label="Rebalance", unit="bars",
    )
    gross_notional = parameter(
        default={request.gross_notional!r}, minimum=100.0, maximum=None, step=100.0,
        description="Capital allocated across selected stocks", label="Gross notional", unit="USD",
    )

    def initialize(self, context: StrategyContext) -> None:
        self._bar_count = 0

    def on_bar(self, context: StrategyContext):
        self._bar_count += 1
        {factor_setup}
        if self._bar_count % self.rebalance_bars != 0 or any(
            item.value is None for item in factors
        ):
            return None
        candidates = list(zip(context.symbols, factors, strict=True))
        max_volatility = {filter_value}
        if max_volatility is not None:
            filtered = []
            for symbol, factor in candidates:
                volatility = runtime_volatility(context, symbol=symbol, lookback=20)
                if volatility.value is not None and volatility.value < max_volatility:
                    filtered.append((symbol, factor))
            candidates = filtered
        ranked = sorted(
            candidates,
            key=lambda item: (float(item[1].value), item[0]),
            reverse={record.factor.direction == "HIGH"},
        )
        selected = ranked[:{top_count}]
        selected_symbols = {{symbol for symbol, _ in selected}}
        weights = {{
            symbol: (
                1.0 / len(selected)
                if symbol in selected_symbols and selected
                else 0.0
            )
            for symbol in context.symbols
        }}
        return context.target_weights(
            weights,
            gross_notional=self.gross_notional,
            reason="Cross-sectional {record.factor.name} rank; top {request.long_percent:g}%",
            dependencies=tuple(factor for _, factor in selected),
            signal="FACTOR_REBALANCE",
            previous_state="CURRENT_PORTFOLIO",
            next_state="FACTOR_TOP_BUCKET",
            target_state=1 if selected else 0,
        )
"""

    def create(
        self, record: FactorResearchRecord, request: CreateFactorStrategy
    ) -> FactorStrategyArtifact:
        identity = hashlib.sha256(
            (record.research_id + request.model_dump_json() + record.dataset_revision).encode()
        ).hexdigest()[:16]
        strategy_id = f"factor-{identity}"
        existing = self.strategy_registry.get_registration(strategy_id)
        if existing is None:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{strategy_id}.py"
            source = self._source(record, request, strategy_id)
            temporary = path.with_suffix(".py.tmp")
            temporary.write_text(source, encoding="utf-8")
            temporary.replace(path)
            class_name = "FactorStrategy_" + identity.replace("-", "_")
            registration = self.strategy_registry.add(path, class_name)
        else:
            registration = existing
        return FactorStrategyArtifact(
            strategy_id=strategy_id,
            research_id=record.research_id,
            dataset_id=record.dataset_id,
            source_fingerprint=registration.source_fingerprint,
            created_at=datetime.now(UTC),
        )
