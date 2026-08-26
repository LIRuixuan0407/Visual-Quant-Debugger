from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from app.factors.repository import FactorResearchRepository
from app.sdk.registry import StrategyRegistry
from app.workspace import default_workspace_root

from .models import PortfolioResearchRecord, PortfolioStrategyArtifact


class PortfolioStrategyFactory:
    def __init__(
        self,
        strategy_registry: StrategyRegistry,
        factor_repository: FactorResearchRepository,
        workspace_root: str | Path | None = None,
    ) -> None:
        self.strategy_registry = strategy_registry
        self.factor_repository = factor_repository
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "generated-strategies"

    def _factor_specs(self, record: PortfolioResearchRecord) -> tuple[dict[str, object], ...]:
        specs: list[dict[str, object]] = []
        for reference in record.factor_refs:
            factor_record = self.factor_repository.get(reference.research_id)
            if factor_record is None:
                raise KeyError(reference.research_id)
            specs.append(
                {
                    "research_id": factor_record.research_id,
                    "factor_id": factor_record.factor.factor_id,
                    "direction": reference.direction_override or factor_record.factor.direction,
                    "weight": reference.weight,
                    "lookback": factor_record.factor.lookback,
                    "parameters": factor_record.parameters,
                    "components": tuple(
                        item.model_dump(mode="python") for item in factor_record.components
                    ),
                    "fundamental_dataset_id": factor_record.fundamental_dataset_id,
                }
            )
        return tuple(specs)

    def _source(self, record: PortfolioResearchRecord, strategy_id: str) -> str:
        specs = self._factor_specs(record)
        class_name = "PortfolioStrategy_" + strategy_id.removeprefix("portfolio-").replace("-", "_")
        factor_records = [
            self.factor_repository.get(item.research_id) for item in record.factor_refs
        ]
        if any(item is None for item in factor_records):
            raise KeyError("Portfolio factor research disappeared")
        records = [item for item in factor_records if item is not None]
        required_fields = tuple(
            sorted(
                {
                    "close",
                    "volume",
                    *(field for item in records for field in item.factor.required_fields),
                }
            )
        )
        lookbacks = [int(item.parameters.get("lookback", item.factor.lookback)) for item in records]
        minimum_history = max([21, *(value + 1 for value in lookbacks)])
        filters = record.filters.model_dump(mode="python")
        construction = record.construction.model_dump(mode="python")
        liquidity_guard = (
            "            if liquidity.value is None or liquidity.value < "
            f"{filters['minimum_liquidity']!r}:\n                continue\n"
            if filters["minimum_liquidity"] is not None
            else ""
        )
        volatility_guard = (
            "            if volatility.value is None or volatility.value > "
            f"{filters['maximum_volatility']!r}:\n                continue\n"
            if filters["maximum_volatility"] is not None
            else ""
        )
        return f"""from __future__ import annotations

import math

from app.factors.runtime import runtime_volatility
from app.portfolio_lab.runtime import compute_runtime_portfolio_scores, runtime_liquidity
from app.sdk import DataRequirements, StrategyContext, StrategyMetadata, VQDStrategy, parameter


class {class_name}(VQDStrategy):
    metadata = StrategyMetadata(
        strategy_id={strategy_id!r},
        name={("Portfolio · " + record.name)!r},
        version="1.0",
        description={
            (
                "Generated from "
                + record.portfolio_research_id
                + "; long-only, point-in-time multi-factor portfolio."
            )!r
        },
        data_requirements=DataRequirements(
            required_fields={required_fields!r},
            symbols={record.universe!r},
            minimum_history={minimum_history},
        ),
    )
    gross_notional = parameter(
        default={record.gross_notional!r}, minimum=100.0, maximum=None, step=100.0,
        description="Gross portfolio notional", label="Gross notional", unit="USD",
    )

    def initialize(self, context: StrategyContext) -> None:
        self._last_rebalance_key = None

    def _rebalance_key(self, context: StrategyContext):
        timestamp = context.current_time
        if {record.rebalance!r} == "DAILY":
            return (timestamp.year, timestamp.month, timestamp.day)
        if {record.rebalance!r} == "WEEKLY":
            year, week, _ = timestamp.isocalendar()
            return (year, week)
        return (timestamp.year, timestamp.month)

    @staticmethod
    def _cap(raw, cap):
        if not raw:
            return {{}}
        total = sum(max(value, 0.0) for value in raw.values())
        weights = (
            {{symbol: max(value, 0.0) / total for symbol, value in raw.items()}}
            if total
            else {{symbol: 1 / len(raw) for symbol in raw}}
        )
        active = set(weights)
        fixed = {{}}
        remaining = 1.0
        while active:
            active_total = sum(weights[symbol] for symbol in active)
            if active_total <= 0:
                break
            capped = False
            for symbol in tuple(active):
                proposed = remaining * weights[symbol] / active_total
                if proposed > cap + 1e-12:
                    fixed[symbol] = cap
                    remaining -= cap
                    active.remove(symbol)
                    capped = True
            if not capped:
                for symbol in active:
                    fixed[symbol] = remaining * weights[symbol] / active_total
                break
        return fixed

    def on_bar(self, context: StrategyContext):
        key = self._rebalance_key(context)
        if key == self._last_rebalance_key:
            return None
        scores = compute_runtime_portfolio_scores(
            context,
            factor_specs={specs!r},
            combination={record.combination!r},
            require_all_factors={record.filters.require_factor_availability!r},
        )
        candidates = []
        dependencies = []
        include = set({filters["include_symbols"]!r})
        exclude = set({filters["exclude_symbols"]!r})
        for symbol, score in scores.items():
            if include and symbol not in include or symbol in exclude or score.value is None:
                continue
            liquidity = runtime_liquidity(context, symbol=symbol, lookback=20)
            volatility = runtime_volatility(context, symbol=symbol, lookback=20)
            dependencies.extend((score, liquidity, volatility))
{liquidity_guard}{volatility_guard}            candidates.append((symbol, score))
        ranked = sorted(candidates, key=lambda item: (-float(item[1].value), item[0]))
        if {construction["selection"]!r} == "TOP_N":
            count = min({construction["top_n"]}, len(ranked))
        else:
            count = (
                max(1, math.ceil(len(ranked) * {construction["top_percent"]!r} / 100))
                if ranked
                else 0
            )
        selected = ranked[:count]
        if {construction["weighting"]!r} == "SCORE_WEIGHTED" and selected:
            floor = min(float(score.value) for _, score in selected)
            raw = {{
                symbol: max(float(score.value) - floor, 0.0) + 1e-9
                for symbol, score in selected
            }}
        else:
            raw = {{symbol: 1.0 for symbol, _ in selected}}
        if not selected:
            self._last_rebalance_key = key
            return context.target_positions(
                {{symbol: 0.0 for symbol in context.symbols}},
                reason={("Portfolio Lab " + record.combination + " ranking")!r},
                dependencies=tuple(dependencies),
                signal="PORTFOLIO_REBALANCE",
                previous_state="CURRENT_PORTFOLIO",
                next_state="CASH",
                target_state=0,
            )
        capital_weights = self._cap(
            raw, {construction["max_single_position_weight"]!r}
        )
        deployed_fraction = sum(capital_weights.values())
        quantity_weights = {{
            symbol: capital_weights.get(symbol, 0.0) / float(context.current(symbol, "close"))
            for symbol in context.symbols
        }}
        self._last_rebalance_key = key
        return context.target_weights(
            quantity_weights,
            gross_notional=self.gross_notional * deployed_fraction,
            reason={("Portfolio Lab " + record.combination + " ranking")!r},
            dependencies=tuple(dependencies),
            signal="PORTFOLIO_REBALANCE",
            previous_state="CURRENT_PORTFOLIO",
            next_state="MULTI_FACTOR_PORTFOLIO",
            target_state=1 if selected else 0,
        )
"""

    def create(self, record: PortfolioResearchRecord) -> PortfolioStrategyArtifact:
        identity = hashlib.sha256(
            (
                record.portfolio_research_id
                + record.model_dump_json(exclude={"strategy"})
                + record.dataset_fingerprint
            ).encode()
        ).hexdigest()[:16]
        strategy_id = f"portfolio-{identity}"
        existing = self.strategy_registry.get_registration(strategy_id)
        if existing is None:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{strategy_id}.py"
            source = self._source(record, strategy_id)
            temporary = path.with_suffix(".py.tmp")
            temporary.write_text(source, encoding="utf-8")
            temporary.replace(path)
            class_name = "PortfolioStrategy_" + identity.replace("-", "_")
            registration = self.strategy_registry.add(path, class_name)
        else:
            registration = existing
        return PortfolioStrategyArtifact(
            strategy_id=strategy_id,
            portfolio_research_id=record.portfolio_research_id,
            dataset_id=record.dataset_id,
            source_fingerprint=registration.source_fingerprint,
            created_at=datetime.now(UTC),
        )
