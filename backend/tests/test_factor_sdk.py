from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.datasets import DatasetProvenance, DatasetRegistry
from app.factor_sdk import FactorContext, FactorPoint
from app.factor_sdk.loader import FactorLoadError
from app.factors import FactorResearchEngine
from app.factors.models import CreateFactorResearch, ResearchPeriod, ResearchPeriods
from app.factors.registry import FactorRegistry
from app.market_data.models import MarketBar
from app.trace.models import DataDependency

FACTOR_SOURCE = """from app.factor_sdk import (
    FactorContext,
    FactorMetadata,
    FactorResult,
    VQDFactor,
    factor_parameter,
)


class LocalMomentum(VQDFactor):
    metadata = FactorMetadata(
        factor_id="local-momentum",
        name="Local Momentum",
        version="1.0.0",
        description="Repository-external momentum contract.",
        formula="close(t) / close(t-lookback) - 1",
        required_fields=("close",),
        lookback=5,
        data_source="MARKET",
    )
    lookback = factor_parameter(
        default=5, minimum=2, maximum=20, step=1,
        description="Trailing close window", unit="bars",
    )

    def compute(self, context: FactorContext, symbol: str) -> FactorResult:
        closes = context.history(symbol, "close", int(self.lookback) + 1)
        value = None if len(closes) < int(self.lookback) + 1 else closes[-1] / closes[0] - 1
        return context.result(value, inputs=(closes,), formula=self.metadata.formula)
"""


def _provider_dataset(registry: DatasetRegistry) -> str:
    start = datetime(2024, 1, 2, tzinfo=UTC)
    symbols = ("AAPL", "MSFT", "AMZN", "NVDA", "META")
    bars = tuple(
        MarketBar(
            symbol=symbol,
            timeframe="1Day",
            event_time=start + timedelta(days=day),
            available_at=start + timedelta(days=day),
            received_at=start + timedelta(days=day),
            open=100 + rank + day,
            high=101 + rank + day * 1.5,
            low=99 + rank + day * 0.5,
            close=100 + rank + day * (1 + rank / 10),
            volume=1_000_000,
            provider="alpaca",
            feed="iex",
            provider_event_id=f"phase19:{symbol}:{day}",
        )
        for day in range(45)
        for rank, symbol in enumerate(symbols)
    )
    return registry.commit_provider_bars(
        name="Phase 19 provider contract",
        bars=bars,
        provenance=DatasetProvenance(
            provider="alpaca",
            feed="iex",
            requested_symbols=symbols,
            requested_start=start,
            requested_end=start + timedelta(days=44),
            retrieved_at=start + timedelta(days=44),
            market_timestamp_start=start,
            market_timestamp_end=start + timedelta(days=44),
        ),
    ).dataset_id


def test_custom_factor_registry_persists_and_research_uses_existing_engine(
    tmp_path: Path,
) -> None:
    source = tmp_path.parent / f"external-{tmp_path.name}.py"
    source.write_text(FACTOR_SOURCE, encoding="utf-8")
    factors = FactorRegistry(tmp_path)
    registration = factors.add(source)
    assert registration.factor_id == "local-momentum"
    assert registration.source_fingerprint.startswith("sha256:")

    restarted = FactorRegistry(tmp_path)
    definition = restarted.definition("local-momentum")
    assert definition.origin == "CUSTOM"
    assert definition.source_path == str(source.resolve())
    datasets = DatasetRegistry(tmp_path)
    dataset_id = _provider_dataset(datasets)
    start = datetime(2024, 1, 2, tzinfo=UTC)
    record = FactorResearchEngine(datasets, factors=restarted).create(
        CreateFactorResearch(
            name="Custom SDK research",
            dataset_id=dataset_id,
            factor_id="local-momentum",
            parameters={"lookback": 5},
            periods=ResearchPeriods(
                research=ResearchPeriod(start=start, end=start + timedelta(days=19)),
                validation=ResearchPeriod(
                    start=start + timedelta(days=20), end=start + timedelta(days=31)
                ),
                holdout=ResearchPeriod(
                    start=start + timedelta(days=32), end=start + timedelta(days=44)
                ),
            ),
        )
    )
    assert record.factor.origin == "CUSTOM"
    assert record.sample_observations
    assert all(
        dependency.available_at <= dependency.used_at
        for sample in record.sample_observations
        for dependency in sample.dependencies
    )

    source.write_text(FACTOR_SOURCE + "\n# changed\n", encoding="utf-8")
    with pytest.raises(FactorLoadError, match="FactorSourceChanged"):
        FactorRegistry(tmp_path).load("local-momentum")


def test_factor_context_rejects_future_available_input() -> None:
    used_at = datetime(2024, 1, 2, tzinfo=UTC)
    dependency = DataDependency(
        dependency_id="future",
        source="market_data",
        field="close",
        symbol="AAPL",
        value=100.0,
        source_timestamp=used_at,
        available_at=used_at + timedelta(seconds=1),
        used_at=used_at + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="not available"):
        FactorPoint(
            value=100.0,
            source_timestamp=used_at,
            available_at=dependency.available_at,
            used_at=used_at,
            dependency=dependency,
        )

    # The SDK never exposes frames/DataFrames; all access is through these readers.
    assert not hasattr(FactorContext, "dataframe")
