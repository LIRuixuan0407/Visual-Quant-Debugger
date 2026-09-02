from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.datasets import DatasetProvenance, DatasetRegistry
from app.factor_relationships import CreateFactorRelationship, FactorRelationshipEngine
from app.factors import FactorResearchEngine
from app.factors.models import CreateFactorResearch, ResearchPeriod, ResearchPeriods
from app.factors.repository import FactorResearchRepository
from app.main import app
from app.market_data.models import MarketBar
from app.research_ledger import ResearchLedgerRepository

SYMBOLS = ("AAPL", "MSFT", "AMZN", "NVDA", "META", "GOOGL")


def _dataset(registry: DatasetRegistry) -> str:
    start = datetime(2022, 1, 3, tzinfo=UTC)
    bars: list[MarketBar] = []
    for day in range(180):
        timestamp = start + timedelta(days=day)
        for rank, symbol in enumerate(SYMBOLS, start=1):
            trend = day * (0.025 + rank * 0.011)
            cycle = ((day % 17) - 8) * rank * 0.045
            close = 70 + rank * 13 + trend + cycle
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
                    volume=800_000 + rank * 120_000 + day * 350,
                    provider="alpaca",
                    feed="iex",
                    provider_event_id=f"factor-relationships:{symbol}:{day}",
                )
            )
    end = start + timedelta(days=179)
    return registry.commit_provider_bars(
        name="Factor relationships provider-backed contract",
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
        security_names={symbol: symbol for symbol in SYMBOLS},
    ).dataset_id


def _periods() -> ResearchPeriods:
    start = datetime(2022, 1, 3, tzinfo=UTC)
    return ResearchPeriods(
        research=ResearchPeriod(start=start, end=start + timedelta(days=79)),
        validation=ResearchPeriod(
            start=start + timedelta(days=80),
            end=start + timedelta(days=129),
        ),
        holdout=ResearchPeriod(
            start=start + timedelta(days=130),
            end=start + timedelta(days=179),
        ),
    )


def _assets(
    tmp_path: Path,
    *,
    reveal: bool = True,
) -> tuple[FactorRelationshipEngine, ResearchLedgerRepository, tuple[str, ...]]:
    datasets = DatasetRegistry(tmp_path)
    dataset_id = _dataset(datasets)
    factor_engine = FactorResearchEngine(datasets)
    factors = FactorResearchRepository(tmp_path)
    definitions = (
        ("Momentum A", "momentum", {"lookback": 10}),
        ("Momentum B", "momentum", {"lookback": 10}),
        ("Reversal", "reversal", {"lookback": 10}),
        ("Volatility", "volatility", {"lookback": 10}),
    )
    research_ids: list[str] = []
    for name, factor_id, parameters in definitions:
        record = factor_engine.create(
            CreateFactorResearch(
                name=name,
                dataset_id=dataset_id,
                factor_id=factor_id,
                parameters=parameters,
                periods=_periods(),
            )
        )
        if reveal:
            record = factor_engine.reveal(record, "VALIDATION")
            record = factor_engine.reveal(record, "HOLDOUT")
        factors.save(record)
        research_ids.append(record.research_id)
    ledger = ResearchLedgerRepository(tmp_path)
    return (
        FactorRelationshipEngine(datasets, factors, factor_engine, ledger),
        ledger,
        tuple(research_ids),
    )


def _request(research_ids: tuple[str, ...]) -> CreateFactorRelationship:
    return CreateFactorRelationship(
        name="Factor relationships contract",
        factor_research_ids=research_ids,
        stage="HOLDOUT",
        horizon=5,
        rolling_window=10,
        top_percent=25,
        redundancy_threshold=0.75,
        overlap_threshold=0.60,
    )


def _cell(record: object, field: str, left_id: str, right_id: str) -> object:
    cells = getattr(record, field)
    return next(
        item
        for item in cells
        if item.left_research_id == left_id and item.right_research_id == right_id
    )


def test_factor_relationships_router_is_registered_in_native_api() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/factor-relationships" in paths
    assert "/api/factor-relationships/{relationship_id}" in paths


def test_factor_relationships_separates_correlations_and_builds_rolling_series(
    tmp_path: Path,
) -> None:
    engine, _, research_ids = _assets(tmp_path)
    record = engine.create(_request(research_ids))

    assert len(record.value_correlations) == 16
    assert len(record.rank_correlations) == 16
    assert len(record.return_correlations) == 16
    assert {item.semantic for item in record.value_correlations} == {"FACTOR_VALUES"}
    assert {item.semantic for item in record.rank_correlations} == {"FACTOR_RANKS"}
    assert {item.semantic for item in record.return_correlations} == {"FACTOR_RETURNS"}

    momentum_id, duplicate_id, reversal_id, _ = research_ids
    duplicate_rank = _cell(record, "rank_correlations", momentum_id, duplicate_id)
    reversal_value = _cell(record, "value_correlations", momentum_id, reversal_id)
    reversal_rank = _cell(record, "rank_correlations", momentum_id, reversal_id)
    reversal_return = _cell(record, "return_correlations", momentum_id, reversal_id)
    assert duplicate_rank.pearson == pytest.approx(1.0)
    assert duplicate_rank.spearman == pytest.approx(1.0)
    assert reversal_value.pearson == pytest.approx(-1.0)
    assert reversal_rank.pearson == pytest.approx(-1.0)
    assert reversal_return.pearson == pytest.approx(-1.0)

    series = next(
        item
        for item in record.rolling_correlations
        if item.left_research_id == momentum_id
        and item.right_research_id == reversal_id
        and item.semantic == "FACTOR_RETURNS"
    )
    assert series.window == 10
    assert series.points
    assert series.points[-1].timestamp <= record.period.end - timedelta(days=5)
    assert all(point.observations == 10 for point in series.points)


def test_factor_relationships_reports_overlap_redundancy_incremental_clusters_and_ledger(
    tmp_path: Path,
) -> None:
    engine, ledger, research_ids = _assets(tmp_path)
    record = engine.create(_request(research_ids))
    momentum_id, duplicate_id, reversal_id, volatility_id = research_ids

    duplicate_overlap = next(
        item
        for item in record.exposure_overlap
        if item.left_research_id == momentum_id and item.right_research_id == duplicate_id
    )
    assert duplicate_overlap.mean_overlap == pytest.approx(1.0)
    assert duplicate_overlap.mean_jaccard == pytest.approx(1.0)
    assert duplicate_overlap.points
    assert all(point.intersection_count == 2 for point in duplicate_overlap.points)
    assert all(point.union_count == 2 for point in duplicate_overlap.points)

    duplicate_redundancy = next(
        item
        for item in record.redundancy
        if item.left_research_id == momentum_id and item.right_research_id == duplicate_id
    )
    reversal_redundancy = next(
        item
        for item in record.redundancy
        if item.left_research_id == momentum_id and item.right_research_id == reversal_id
    )
    assert duplicate_redundancy.status == "HIGH_REDUNDANCY"
    assert "nothing is removed or reweighted" in duplicate_redundancy.reason
    assert reversal_redundancy.status == "RELATED"

    incremental = next(
        item
        for item in record.incremental_information
        if item.base_research_id == momentum_id and item.added_research_id == volatility_id
    )
    assert incremental.normalization == "DIRECTION_ADJUSTED_PERCENTILE_RANK_AVERAGE"
    assert incremental.composite_coverage <= incremental.base_coverage
    assert incremental.coverage_delta == pytest.approx(
        incremental.composite_coverage - incremental.base_coverage
    )
    assert set().union(*(set(item.factor_research_ids) for item in record.clusters)) == set(
        research_ids
    )
    assert record.pca is not None
    assert record.pca.status == "AVAILABLE"
    assert record.pca.observations >= 20
    assert record.pca.components
    assert record.pca.components[0].component == "PC1"
    assert (
        record.pca.components[0].explained_variance
        >= record.pca.components[-1].explained_variance
    )
    assert record.pca.components[-1].cumulative_explained_variance == pytest.approx(1.0)
    assert {item.factor_research_id for item in record.pca.components[0].loadings} == set(
        research_ids
    )
    assert "does not automatically delete" in record.pca.boundary_disclosure
    assert "not causal improvement" in record.incremental_disclosure
    assert "not evidence" in record.crowding_disclosure

    entries = ledger.list()
    assert len(entries) == 1
    assert entries[0].kind == "FACTOR_RELATIONSHIP"
    assert entries[0].factor_relationship_id == record.relationship_id
    assert entries[0].dataset_fingerprints == (record.dataset_fingerprint,)
    assert entries[0].factor_revisions == record.factor_revisions


def test_factor_relationships_keeps_unrevealed_stages_sealed(tmp_path: Path) -> None:
    engine, _, research_ids = _assets(tmp_path, reveal=False)
    with pytest.raises(ValueError, match="HOLDOUT is still sealed"):
        engine.create(_request(research_ids))
