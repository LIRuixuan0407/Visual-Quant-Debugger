from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.datasets import DatasetProvenance, DatasetRegistry
from app.factors.registry import factor_registry
from app.main import app
from app.market_data.models import MarketBar
from app.paper import CreatePaperSession, PaperSessionRepository, PaperSessionService
from app.sdk.registry import StrategyRegistry, strategy_registry

HISTORICAL_STRATEGY = """
from app.sdk import DataRequirements, StrategyMetadata, VQDStrategy

class HistoricalClockStrategy(VQDStrategy):
    metadata = StrategyMetadata(
        strategy_id="test.historical-clock",
        name="Historical Clock",
        version="1.0.0",
        description="Historical Paper causal clock contract.",
        data_requirements=DataRequirements(
            required_fields=("close", "volume"),
            symbols=("AAPL",),
            minimum_history=1,
        ),
    )

    def on_bar(self, context):
        return context.target_positions(
            {"AAPL": 1.0},
            reason="Historical clock test",
            signal="LONG",
            previous_state="CURRENT",
            next_state="LONG",
            target_state=1,
        )
"""


def _historical_dataset(registry: DatasetRegistry) -> tuple[str, tuple[MarketBar, ...]]:
    start = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    bars = tuple(
        MarketBar(
            symbol="AAPL",
            timeframe="1Min",
            event_time=start + timedelta(minutes=index),
            available_at=start + timedelta(minutes=index + 2),
            received_at=start + timedelta(minutes=index + 2, milliseconds=50),
            open=100.0 + index,
            high=100.5 + index,
            low=99.5 + index,
            close=100.0 + index,
            volume=10_000.0,
            provider="historical",
            feed="test",
            provider_event_id=f"historical:{index}",
        )
        for index in range(3)
    )
    definition = registry.commit_provider_bars(
        name="Delayed knowledge-time bars",
        bars=bars,
        provenance=DatasetProvenance(
            provider="historical",
            feed="test",
            requested_symbols=("AAPL",),
            requested_start=bars[0].event_time,
            requested_end=bars[-1].event_time,
            retrieved_at=bars[-1].available_at,
            market_timestamp_start=bars[0].event_time,
            market_timestamp_end=bars[-1].event_time,
        ),
    )
    return definition.dataset_id, bars


def test_historical_paper_step_uses_knowledge_time_not_event_time(tmp_path: Path) -> None:
    source = tmp_path / "historical_strategy.py"
    source.write_text(HISTORICAL_STRATEGY, encoding="utf-8")
    strategies = StrategyRegistry(tmp_path)
    strategies.add(source)
    datasets = DatasetRegistry(tmp_path)
    dataset_id, bars = _historical_dataset(datasets)
    service = PaperSessionService(PaperSessionRepository(tmp_path), registry=strategies)
    request = CreatePaperSession(
        strategy_id="test.historical-clock",
        symbols=("AAPL",),
        clock_mode="HISTORICAL",
        dataset_id=dataset_id,
        simulation_start=bars[0].available_at,
        simulation_end=bars[-1].available_at,
        simulation_speed="MAX",
    )
    created = service.create(request)

    async def scenario() -> None:
        await service.start(created.session_id, launch_task=False)
        await service.pause(created.session_id)
        sped = await service.set_simulation_speed(created.session_id, "10X")
        assert sped.simulation_speed == "10X"
        stepped = await service.step_historical(created.session_id)
        assert stepped.status == "PAUSED"
        assert stepped.simulation_time == bars[0].available_at
        assert stepped.last_market_event == bars[0].event_time
        assert stepped.recent_market_events[-1].available_at == bars[0].available_at
        assert (
            stepped.recent_market_events[-1].available_at
            > stepped.recent_market_events[-1].event_time
        )
        assert service.health(created.session_id).stale_seconds == 0.0
        await service.shutdown()

    asyncio.run(scenario())


def test_demo_mode_blocks_api_mutations_but_keeps_reads(monkeypatch) -> None:
    monkeypatch.setenv("VQD_DEMO_MODE", "true")
    with TestClient(app) as client:
        assert client.get("/api/strategies").status_code == 200
        blocked = client.post("/api/paper-accounts", json={"name": "demo", "initial_cash": 1000})
        assert blocked.status_code == 403
        assert "read-only demo mode" in blocked.json()["detail"]


FACTOR_UPLOAD_SOURCE = r"""from app.factor_sdk import (
    FactorContext,
    FactorMetadata,
    FactorResult,
    VQDFactor,
    factor_parameter,
)

class UploadedMomentum(VQDFactor):
    metadata = FactorMetadata(
        factor_id="test-uploaded-momentum",
        name="Uploaded Momentum",
        version="1.0.0",
        description="Upload endpoint contract.",
        formula="close(t) / close(t-lookback) - 1",
        required_fields=("close",),
        lookback=2,
        data_source="MARKET",
    )
    lookback = factor_parameter(
        default=2,
        minimum=2,
        maximum=10,
        step=1,
        description="Window",
        unit="bars",
    )

    def compute(self, context: FactorContext, symbol: str) -> FactorResult:
        closes = context.history(symbol, "close", int(self.lookback) + 1)
        value = None if len(closes) < int(self.lookback) + 1 else closes[-1] / closes[0] - 1
        return context.result(value, inputs=(closes,), formula=self.metadata.formula)
"""


def test_python_upload_endpoints_persist_sources_and_register(monkeypatch, tmp_path: Path) -> None:
    strategy_root = tmp_path / "strategy-workspace"
    factor_root = tmp_path / "factor-workspace"
    monkeypatch.setattr(strategy_registry, "workspace_root", strategy_root)
    monkeypatch.setattr(
        strategy_registry, "registry_path", strategy_root / ".vqd" / "strategies.json"
    )
    monkeypatch.setattr(factor_registry, "workspace_root", factor_root)
    monkeypatch.setattr(factor_registry, "registry_path", factor_root / ".vqd" / "factors.json")
    factor_registry._loaded.clear()
    factor_registry._definitions.clear()

    with TestClient(app) as client:
        strategy_response = client.post(
            "/api/strategies/upload",
            files={"file": ("historical_clock.py", HISTORICAL_STRATEGY.encode(), "text/x-python")},
        )
        assert strategy_response.status_code == 201, strategy_response.text
        assert strategy_response.json()["strategy_id"] == "test.historical-clock"

        factor_response = client.post(
            "/api/factors/upload",
            files={
                "file": ("uploaded_momentum.py", FACTOR_UPLOAD_SOURCE.encode(), "text/x-python")
            },
        )
        assert factor_response.status_code == 201, factor_response.text
        assert factor_response.json()["factor"]["factor_id"] == "test-uploaded-momentum"

    strategy_files = tuple((strategy_root / ".vqd" / "user-code" / "strategies").glob("*.py"))
    factor_files = tuple((factor_root / ".vqd" / "user-code" / "factors").glob("*.py"))
    assert len(strategy_files) == 1
    assert len(factor_files) == 1
