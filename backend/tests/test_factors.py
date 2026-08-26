from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.datasets import DatasetProvenance, DatasetRegistry
from app.factors import FactorResearchEngine, FactorStrategyFactory
from app.factors.models import (
    CreateFactorResearch,
    CreateFactorStrategy,
    ResearchPeriod,
    ResearchPeriods,
)
from app.market_data.models import MarketBar
from app.runs.engine import execute_open_run
from app.sdk.registry import StrategyRegistry


def _real_provider_dataset(registry: DatasetRegistry) -> str:
    start = datetime(2023, 1, 2, tzinfo=UTC)
    symbols = ("AAPL", "MSFT", "AMZN", "NVDA", "META")
    bars: list[MarketBar] = []
    for day in range(110):
        timestamp = start + timedelta(days=day)
        for rank, symbol in enumerate(symbols, start=1):
            close = 80 + rank * 12 + day * (0.08 + rank * 0.015) + (day % 7) * rank * 0.03
            bars.append(
                MarketBar(
                    symbol=symbol,
                    timeframe="1Day",
                    event_time=timestamp,
                    available_at=timestamp,
                    received_at=timestamp,
                    open=close - 0.2,
                    high=close + 0.8,
                    low=close - 0.9,
                    close=close,
                    volume=1_000_000 + rank * 50_000 + day * 100,
                    provider="alpaca",
                    feed="iex",
                    provider_event_id=f"test:{symbol}:{day}",
                )
            )
    end = start + timedelta(days=109)
    definition = registry.commit_provider_bars(
        name="Real provider contract dataset",
        bars=tuple(bars),
        provenance=DatasetProvenance(
            provider="alpaca",
            feed="iex",
            requested_symbols=symbols,
            requested_start=start,
            requested_end=end,
            retrieved_at=end,
            market_timestamp_start=start,
            market_timestamp_end=end,
        ),
        security_names={symbol: f"{symbol} Inc." for symbol in symbols},
    )
    return definition.dataset_id


def _request(dataset_id: str) -> CreateFactorResearch:
    start = datetime(2023, 1, 2, tzinfo=UTC)
    return CreateFactorResearch(
        name="Momentum contract",
        dataset_id=dataset_id,
        factor_id="momentum",
        parameters={"lookback": 10},
        periods=ResearchPeriods(
            research=ResearchPeriod(start=start, end=start + timedelta(days=49)),
            validation=ResearchPeriod(
                start=start + timedelta(days=50), end=start + timedelta(days=79)
            ),
            holdout=ResearchPeriod(
                start=start + timedelta(days=80), end=start + timedelta(days=109)
            ),
        ),
    )


def test_factor_research_is_point_in_time_safe_and_stage_gated(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path)
    dataset_id = _real_provider_dataset(registry)
    engine = FactorResearchEngine(registry)

    record = engine.create(_request(dataset_id))
    assert record.revealed_stage == "RESEARCH"
    assert [item.stage for item in record.evaluations] == ["RESEARCH"]
    assert record.factor_observation_count == 500
    inspection = engine.inspect(record, "AAPL", datetime(2023, 2, 15, tzinfo=UTC))
    assert inspection.point_in_time_status == "SAFE"
    assert inspection.observation.future_data_used is False
    assert all(
        dependency.source_timestamp <= inspection.observation.timestamp
        and dependency.available_at <= dependency.used_at
        for dependency in inspection.observation.dependencies
    )

    validated = engine.reveal(record, "VALIDATION")
    assert [item.stage for item in validated.evaluations] == ["RESEARCH", "VALIDATION"]
    revealed = engine.reveal(validated, "HOLDOUT")
    assert revealed.revealed_stage == "HOLDOUT"
    assert len(revealed.evaluations) == 3
    with pytest.raises(ValueError, match="Cannot reveal"):
        engine.reveal(record, "HOLDOUT")


def test_factor_period_excludes_forward_returns_that_end_outside_window(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path)
    dataset_id = _real_provider_dataset(registry)
    engine = FactorResearchEngine(registry)

    record = engine.create(_request(dataset_id))
    research = record.evaluations[0]
    by_horizon = {item.horizon: item for item in research.horizons}

    assert by_horizon[1].observation_count == 39 * 5
    assert by_horizon[5].observation_count == 35 * 5
    assert by_horizon[20].observation_count == 20 * 5
    assert max(point.timestamp for point in by_horizon[20].timeline) == (
        research.period.end - timedelta(days=20)
    )

    observations = engine.observations(record)
    assert all(
        endpoint is None or endpoint > item.timestamp
        for item in observations
        for endpoint in item.future_return_timestamps.values()
    )


def test_historical_market_and_factor_strategy_use_existing_runtime(tmp_path: Path) -> None:
    datasets = DatasetRegistry(tmp_path)
    dataset_id = _real_provider_dataset(datasets)
    engine = FactorResearchEngine(datasets)
    record = engine.create(_request(dataset_id))
    view = engine.historical_market(dataset_id, datetime(2023, 3, 1, tzinfo=UTC), "MSFT")
    assert view.selected_symbol == "MSFT"
    assert len(view.cross_section) == 5
    assert view.cross_section[0].company.endswith("Inc.")
    assert view.survivorship_bias_free is False

    strategies = StrategyRegistry(tmp_path)
    artifact = FactorStrategyFactory(strategies, tmp_path).create(
        record,
        CreateFactorStrategy(long_percent=20, rebalance_bars=5, gross_notional=10_000),
    )
    result = execute_open_run(
        strategy_id=artifact.strategy_id,
        dataset_id=dataset_id,
        parameters={},
        strategy_registry=strategies,
        dataset_registry=datasets,
    )
    assert result.status == "COMPLETED"
    assert result.trace is not None
    assert result.trace.strategy.strategy_id == artifact.strategy_id
    assert any(event.feature_snapshots for event in result.trace.timeline)
