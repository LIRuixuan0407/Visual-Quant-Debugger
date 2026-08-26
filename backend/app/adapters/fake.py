from __future__ import annotations

from .models import (
    AdapterEquityPoint,
    AdapterInspection,
    AdapterPositionPoint,
    AdapterRunRequest,
    AdapterRunResult,
    TraceCapabilitySet,
    derive_trace_fidelity,
)


class FakeFrameworkAdapter:
    """Dependency-free contract adapter used by core persistence and fidelity tests."""

    adapter_id = "fake"
    adapter_version = "1"
    framework_name = "Fake Framework"
    distribution_name = "visual-quant-debugger-backend"

    def inspect(self, source_path: str, entrypoint: str) -> AdapterInspection:
        raise NotImplementedError("Fake adapter inspection is configured directly by tests")

    def execute(self, request: AdapterRunRequest) -> AdapterRunResult:
        capabilities = TraceCapabilitySet(
            market_timeline="AVAILABLE",
            positions="AVAILABLE",
            equity="AVAILABLE",
            pnl="AVAILABLE",
            drawdowns="AVAILABLE",
        )
        first_close = {
            symbol: request.dataset.points[0].values[symbol]["close"]
            for symbol in request.dataset.symbols
        }
        return AdapterRunResult(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            framework_name=self.framework_name,
            framework_version="1.0",
            execution_owner=self.framework_name,
            strategy_id=request.manifest.strategy_id,
            strategy_name=request.manifest.name,
            parameters=request.parameters,
            dataset_revision=request.dataset.revision,
            execution_semantics={"mode": "fake-contract"},
            initial_equity=100_000.0,
            market_timeline=request.dataset.points,
            positions=tuple(
                AdapterPositionPoint(
                    timestamp=point.timestamp,
                    quantities={symbol: 0.0 for symbol in request.dataset.symbols},
                    market_values={symbol: 0.0 for symbol in request.dataset.symbols},
                )
                for point in request.dataset.points
            ),
            equity=tuple(
                AdapterEquityPoint(
                    timestamp=point.timestamp,
                    equity=100_000.0
                    + sum(
                        point.values[symbol]["close"] - first_close[symbol]
                        for symbol in request.dataset.symbols
                    ),
                )
                for point in request.dataset.points
            ),
            capabilities=capabilities,
            fidelity=derive_trace_fidelity(capabilities),
            determinism="DETERMINISTIC",
        )
