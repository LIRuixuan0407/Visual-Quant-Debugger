from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.datasets import DatasetProvenance, DatasetRegistry
from app.factors import FactorResearchEngine, FactorStrategyFactory
from app.factors.models import (
    CreateFactorResearch,
    CreateFactorStrategy,
    FactorComponent,
    ResearchPeriod,
    ResearchPeriods,
)
from app.fundamentals import FundamentalObservation, FundamentalRepository
from app.fundamentals.sec import SecCompanyFactsProvider
from app.market_data.models import MarketBar
from app.runs.engine import execute_open_run
from app.sdk.registry import StrategyRegistry
from app.universes import UniverseRepository

SYMBOLS = ("AAPL", "MSFT", "AMZN", "NVDA", "META")


def _real_provider_dataset(registry: DatasetRegistry) -> str:
    start = datetime(2023, 1, 2, tzinfo=UTC)
    bars: list[MarketBar] = []
    for day in range(110):
        timestamp = start + timedelta(days=day)
        for rank, symbol in enumerate(SYMBOLS, start=1):
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
    return registry.commit_provider_bars(
        name="Real provider contract dataset",
        bars=tuple(bars),
        provenance=DatasetProvenance(
            provider="alpaca",
            feed="iex",
            requested_symbols=SYMBOLS,
            requested_start=start,
            requested_end=end,
            retrieved_at=end,
            market_timestamp_start=start,
            market_timestamp_end=end,
        ),
        security_names={symbol: f"{symbol} Inc." for symbol in SYMBOLS},
    ).dataset_id


def _observation(
    symbol: str,
    field: str,
    value: float,
    *,
    fiscal_year: int,
    filed: datetime,
    accession_suffix: str = "original",
    amended: bool = False,
) -> FundamentalObservation:
    period_end = datetime(fiscal_year, 12, 31, tzinfo=UTC)
    accession = f"{symbol}-{fiscal_year}-{accession_suffix}"
    return FundamentalObservation(
        observation_id=f"fund-{symbol}-{field}-{accession}",
        symbol=symbol,
        field=field,
        value=value,
        unit="USD" if field != "shares_outstanding" else "shares",
        fiscal_period=f"{fiscal_year}FY",
        period_type="INSTANT" if field in {"equity", "shares_outstanding"} else "ANNUAL",
        period_start=(
            None
            if field in {"equity", "shares_outstanding"}
            else datetime(fiscal_year, 1, 1, tzinfo=UTC)
        ),
        period_end=period_end,
        report_date=period_end,
        filed_at=filed,
        available_at=filed,
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
        form="10-K/A" if amended else "10-K",
        accession=accession,
        source="sec-companyfacts",
        source_concepts=(f"Test{field.title()}",),
        is_restatement=amended,
    )


def _fundamental_dataset(repository: FundamentalRepository) -> str:
    observations: list[FundamentalObservation] = []
    filed_current = datetime(2023, 1, 20, tzinfo=UTC)
    filed_prior = datetime(2022, 1, 20, tzinfo=UTC)
    for rank, symbol in enumerate(SYMBOLS, start=1):
        observations.extend(
            (
                _observation(
                    symbol,
                    "net_income",
                    8_000_000 + rank * 2_000_000,
                    fiscal_year=2022,
                    filed=filed_current,
                ),
                _observation(
                    symbol,
                    "net_income",
                    7_000_000 + rank * 1_000_000,
                    fiscal_year=2021,
                    filed=filed_prior,
                ),
                _observation(
                    symbol,
                    "equity",
                    50_000_000 + rank * 3_000_000,
                    fiscal_year=2022,
                    filed=filed_current,
                ),
                _observation(
                    symbol,
                    "revenue",
                    100_000_000 + rank * 8_000_000,
                    fiscal_year=2022,
                    filed=filed_current,
                ),
                _observation(
                    symbol,
                    "revenue",
                    90_000_000 + rank * 5_000_000,
                    fiscal_year=2021,
                    filed=filed_prior,
                ),
            )
        )
    dataset = repository.create_dataset(
        name="SEC point-in-time contract",
        provider="sec-companyfacts",
        observations=tuple(observations),
        start=datetime(2021, 1, 1, tzinfo=UTC),
        end=datetime(2023, 12, 31, tzinfo=UTC),
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
        point_in_time_safe=True,
        restatement_safe=False,
        disclosure="NOT RESTATEMENT-SAFE: contract fixture mirrors provider limitation.",
    )
    return dataset.fundamental_dataset_id


def _periods() -> ResearchPeriods:
    start = datetime(2023, 1, 2, tzinfo=UTC)
    return ResearchPeriods(
        research=ResearchPeriod(start=start, end=start + timedelta(days=49)),
        validation=ResearchPeriod(start=start + timedelta(days=50), end=start + timedelta(days=79)),
        holdout=ResearchPeriod(start=start + timedelta(days=80), end=start + timedelta(days=109)),
    )


def test_fundamental_factor_never_uses_a_late_filing_early(tmp_path: Path) -> None:
    datasets = DatasetRegistry(tmp_path)
    market_dataset_id = _real_provider_dataset(datasets)
    fundamentals = FundamentalRepository(tmp_path)
    fundamental_dataset_id = _fundamental_dataset(fundamentals)
    engine = FactorResearchEngine(datasets, fundamentals)

    record = engine.create(
        CreateFactorResearch(
            name="Point-in-time ROE",
            dataset_id=market_dataset_id,
            factor_id="roe",
            parameters={"max_age_days": 550},
            fundamental_dataset_id=fundamental_dataset_id,
            periods=_periods(),
        )
    )

    assert record.factor.data_source == "FUNDAMENTAL"
    assert record.restatement_safe is False
    assert record.factor_observation_count == 5 * (110 - 18)
    inspection = engine.inspect(record, "AAPL", datetime(2023, 1, 25, tzinfo=UTC))
    assert inspection.restatement_status == "NOT_RESTATEMENT_SAFE"
    assert inspection.observation.fundamental_inputs
    assert all(
        item.available_at is not None
        and item.available_at <= inspection.observation.timestamp
        and item.used_at == inspection.observation.timestamp
        for item in inspection.observation.fundamental_inputs
    )
    assert all(
        dependency.available_at <= dependency.used_at
        for dependency in inspection.observation.dependencies
    )


def test_restatement_only_mutates_snapshots_after_its_filed_date(tmp_path: Path) -> None:
    repository = FundamentalRepository(tmp_path)
    original = _observation(
        "AAPL",
        "net_income",
        10.0,
        fiscal_year=2022,
        filed=datetime(2023, 3, 1, tzinfo=UTC),
    )
    amended = _observation(
        "AAPL",
        "net_income",
        14.0,
        fiscal_year=2022,
        filed=datetime(2023, 6, 1, tzinfo=UTC),
        accession_suffix="amended",
        amended=True,
    )
    dataset = repository.create_dataset(
        name="Restatement contract",
        provider="sec-companyfacts",
        observations=(original, amended),
        start=datetime(2023, 1, 1, tzinfo=UTC),
        end=datetime(2023, 12, 31, tzinfo=UTC),
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
        point_in_time_safe=True,
        restatement_safe=False,
        disclosure="NOT RESTATEMENT-SAFE",
    )

    before_filing = repository.snapshot(
        dataset, symbol="AAPL", used_at=datetime(2023, 2, 1, tzinfo=UTC)
    )
    before_amendment = repository.snapshot(
        dataset, symbol="AAPL", used_at=datetime(2023, 5, 1, tzinfo=UTC)
    )
    after_amendment = repository.snapshot(
        dataset, symbol="AAPL", used_at=datetime(2023, 7, 1, tzinfo=UTC)
    )
    net_before = next(item for item in before_filing.fields if item.field == "net_income")
    net_original = next(item for item in before_amendment.fields if item.field == "net_income")
    net_amended = next(item for item in after_amendment.fields if item.field == "net_income")
    assert net_before.status == "NOT_YET_REPORTED"
    assert net_original.value == 10.0 and net_original.status == "AVAILABLE"
    assert net_amended.value == 14.0 and net_amended.status == "RESTATED"


def test_user_defined_mixed_factor_and_generated_strategy_preserve_lineage(
    tmp_path: Path,
) -> None:
    datasets = DatasetRegistry(tmp_path)
    market_dataset_id = _real_provider_dataset(datasets)
    fundamentals = FundamentalRepository(tmp_path)
    fundamental_dataset_id = _fundamental_dataset(fundamentals)
    engine = FactorResearchEngine(datasets, fundamentals)
    record = engine.create(
        CreateFactorResearch(
            name="Explicit price and quality mix",
            dataset_id=market_dataset_id,
            factor_id="mixed",
            fundamental_dataset_id=fundamental_dataset_id,
            components=(
                FactorComponent(factor_id="momentum", weight=0.6, parameters={"lookback": 10}),
                FactorComponent(factor_id="roe", weight=0.4, parameters={"max_age_days": 550}),
            ),
            periods=_periods(),
        )
    )
    inspection = engine.inspect(record, "MSFT", datetime(2023, 2, 15, tzinfo=UTC))
    assert record.factor.data_source == "MIXED"
    assert record.components[0].weight == 0.6
    assert {item.source for item in inspection.observation.dependencies} >= {
        "market_data",
        "sec-companyfacts",
    }

    strategies = StrategyRegistry(tmp_path)
    artifact = FactorStrategyFactory(strategies, tmp_path).create(
        record,
        CreateFactorStrategy(long_percent=20, rebalance_bars=5, gross_notional=10_000),
    )
    result = execute_open_run(
        strategy_id=artifact.strategy_id,
        dataset_id=market_dataset_id,
        parameters={},
        strategy_registry=strategies,
        dataset_registry=datasets,
    )
    assert result.status == "COMPLETED"
    assert result.trace is not None
    dependencies = [
        dependency for event in result.trace.timeline for dependency in event.data_dependencies
    ]
    assert any(item.source == "sec-companyfacts" for item in dependencies)
    assert all(item.available_at <= item.used_at for item in dependencies)


def test_sec_boundary_standardizes_tags_and_uses_filed_date_as_availability() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2023-03-01",
                                "start": "2022-01-01",
                                "end": "2022-12-31",
                                "accn": "0001-23-000001",
                                "val": 42,
                            }
                        ]
                    }
                }
            }
        }
    }
    rows = SecCompanyFactsProvider._standardize_symbol(
        "AAPL",
        payload,
        start=datetime(2023, 1, 1, tzinfo=UTC),
        end=datetime(2023, 12, 31, tzinfo=UTC),
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert len(rows) == 1
    assert rows[0].field == "net_income"
    assert rows[0].report_date.date().isoformat() == "2022-12-31"
    assert rows[0].available_at.date().isoformat() == "2023-03-01"
    assert rows[0].source_concepts == ("NetIncomeLoss",)


def test_static_universe_is_explicitly_not_survivorship_bias_free(tmp_path: Path) -> None:
    datasets = DatasetRegistry(tmp_path)
    market_dataset_id = _real_provider_dataset(datasets)
    dataset = datasets.get(market_dataset_id)
    assert dataset is not None
    universe = UniverseRepository(tmp_path).static_for_dataset(dataset)
    assert universe.mode == "STATIC"
    assert universe.survivorship_bias_free is False
    assert universe.snapshots[0].membership_provenance
    assert "not survivorship-bias free" in universe.disclosure
