from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.datasets import DatasetProvenance, DatasetRegistry
from app.factors import FactorResearchEngine
from app.factors.models import CreateFactorResearch, ResearchPeriod, ResearchPeriods
from app.factors.repository import FactorResearchRepository
from app.fundamentals import FundamentalObservation, FundamentalRepository
from app.main import app
from app.market_data.models import MarketBar
from app.portfolio_lab import PortfolioResearchEngine, PortfolioStrategyFactory
from app.portfolio_lab.models import CreatePortfolioResearch, PortfolioFactorRef, PortfolioFilters
from app.research_ledger import ResearchLedgerRepository
from app.runs.engine import execute_open_run
from app.sdk.registry import StrategyRegistry

SYMBOLS = ("AAPL", "MSFT", "AMZN", "NVDA", "META", "GOOGL")


def _market_dataset(registry: DatasetRegistry) -> str:
    start = datetime(2022, 1, 3, tzinfo=UTC)
    bars: list[MarketBar] = []
    for day in range(180):
        timestamp = start + timedelta(days=day)
        for rank, symbol in enumerate(SYMBOLS, start=1):
            trend = (0.05 + rank * 0.018) * day
            cycle = ((day % 11) - 5) * rank * 0.025
            close = 70 + rank * 14 + trend + cycle
            bars.append(
                MarketBar(
                    symbol=symbol,
                    timeframe="1Day",
                    event_time=timestamp,
                    available_at=timestamp,
                    received_at=timestamp,
                    open=close - 0.25,
                    high=close + 0.75,
                    low=close - 0.85,
                    close=close,
                    volume=850_000 + rank * 120_000 + day * 500,
                    provider="alpaca",
                    feed="iex",
                    provider_event_id=f"portfolio-lab:{symbol}:{day}",
                )
            )
    end = start + timedelta(days=179)
    return registry.commit_provider_bars(
        name="Portfolio Lab six-stock provider contract",
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


def _fundamental_dataset(repository: FundamentalRepository) -> str:
    observations: list[FundamentalObservation] = []
    # GOOGL exists in the SEC dataset but is filed after the Research window.
    # That creates a genuine point-in-time missing value without violating the
    # Factor Engine contract.
    for rank, symbol in enumerate(SYMBOLS, start=1):
        filed = (
            datetime(2022, 4, 20, tzinfo=UTC)
            if symbol == "GOOGL"
            else datetime(2022, 1, 20, tzinfo=UTC)
        )
        for field, value in (
            ("net_income", 5_000_000 + rank * 1_500_000),
            ("equity", 40_000_000 + rank * 2_500_000),
        ):
            observations.append(
                FundamentalObservation(
                    observation_id=f"portfolio-lab-{symbol}-{field}",
                    symbol=symbol,
                    field=field,
                    value=value,
                    unit="USD",
                    fiscal_period="2021FY",
                    period_type="ANNUAL" if field == "net_income" else "INSTANT",
                    period_start=(
                        datetime(2021, 1, 1, tzinfo=UTC) if field == "net_income" else None
                    ),
                    period_end=datetime(2021, 12, 31, tzinfo=UTC),
                    report_date=datetime(2021, 12, 31, tzinfo=UTC),
                    filed_at=filed,
                    available_at=filed,
                    retrieved_at=datetime(2022, 8, 1, tzinfo=UTC),
                    form="10-K",
                    accession=f"portfolio-lab-{symbol}-2021",
                    source="sec-companyfacts",
                    source_concepts=(f"PortfolioLab{field.title()}",),
                    is_restatement=False,
                )
            )
    return repository.create_dataset(
        name="Portfolio Lab SEC point-in-time contract",
        provider="sec-companyfacts",
        observations=tuple(observations),
        start=datetime(2021, 1, 1, tzinfo=UTC),
        end=datetime(2022, 12, 31, tzinfo=UTC),
        retrieved_at=datetime(2022, 8, 1, tzinfo=UTC),
        point_in_time_safe=True,
        restatement_safe=False,
        disclosure="NOT RESTATEMENT-SAFE: deterministic Portfolio Lab contract fixture.",
    ).fundamental_dataset_id


def _periods() -> ResearchPeriods:
    start = datetime(2022, 1, 3, tzinfo=UTC)
    return ResearchPeriods(
        research=ResearchPeriod(start=start, end=start + timedelta(days=89)),
        validation=ResearchPeriod(
            start=start + timedelta(days=90), end=start + timedelta(days=134)
        ),
        holdout=ResearchPeriod(start=start + timedelta(days=135), end=start + timedelta(days=179)),
    )


def _factor_assets(
    tmp_path: Path,
) -> tuple[
    DatasetRegistry,
    FactorResearchRepository,
    FactorResearchEngine,
    tuple[str, str, str],
]:
    datasets = DatasetRegistry(tmp_path)
    dataset_id = _market_dataset(datasets)
    fundamentals = FundamentalRepository(tmp_path)
    fundamental_id = _fundamental_dataset(fundamentals)
    factors = FactorResearchEngine(datasets, fundamentals)
    repository = FactorResearchRepository(tmp_path)

    requests = (
        CreateFactorResearch(
            name="Momentum 20",
            dataset_id=dataset_id,
            factor_id="momentum",
            parameters={"lookback": 20},
            periods=_periods(),
        ),
        CreateFactorResearch(
            name="Low volatility 20",
            dataset_id=dataset_id,
            factor_id="volatility",
            parameters={"lookback": 20},
            periods=_periods(),
        ),
        CreateFactorResearch(
            name="Point-in-time ROE",
            dataset_id=dataset_id,
            factor_id="roe",
            parameters={"max_age_days": 550},
            fundamental_dataset_id=fundamental_id,
            periods=_periods(),
        ),
    )
    ids: list[str] = []
    for request in requests:
        record = factors.create(request)
        record = factors.reveal(record, "VALIDATION")
        repository.save(record)
        ids.append(record.research_id)
    return datasets, repository, factors, (ids[0], ids[1], ids[2])


def _portfolio_engine(
    tmp_path: Path,
) -> tuple[
    PortfolioResearchEngine,
    DatasetRegistry,
    FactorResearchRepository,
    tuple[str, str, str],
]:
    datasets, repository, factors, ids = _factor_assets(tmp_path)
    engine = PortfolioResearchEngine(
        datasets,
        repository,
        factors,
        ResearchLedgerRepository(tmp_path),
    )
    return engine, datasets, repository, ids


def test_portfolio_lab_router_is_registered_in_the_native_api() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/portfolio-research" in paths
    assert "/api/portfolio-research/{research_id}/strategy" in paths


def test_portfolio_lab_validates_explicit_weights_and_symbol_filters() -> None:
    with pytest.raises(ValidationError, match="sum to 1.0"):
        CreatePortfolioResearch(
            name="invalid weights",
            factors=(
                PortfolioFactorRef(research_id="a", weight=0.7),
                PortfolioFactorRef(research_id="b", weight=0.4),
            ),
            combination="USER_DEFINED_WEIGHT",
        )
    filters = PortfolioFilters(include_symbols=("aapl", " msft "), exclude_symbols=("nvda",))
    assert filters.include_symbols == ("AAPL", "MSFT")
    with pytest.raises(ValidationError, match="overlap"):
        PortfolioFilters(include_symbols=("AAPL",), exclude_symbols=("aapl",))


def test_portfolio_lab_combines_market_and_sec_factors_with_backend_lineage(tmp_path: Path) -> None:
    engine, _, _, ids = _portfolio_engine(tmp_path)
    request = CreatePortfolioResearch(
        name="Quality momentum portfolio",
        factors=(
            PortfolioFactorRef(research_id=ids[0], weight=0.45),
            PortfolioFactorRef(research_id=ids[1], weight=0.20),
            PortfolioFactorRef(research_id=ids[2], weight=0.35),
        ),
        combination="USER_DEFINED_WEIGHT",
        filters=PortfolioFilters(
            require_factor_availability=False,
            include_symbols=tuple(symbol for symbol in SYMBOLS if symbol != "META"),
            exclude_symbols=("META",),
            minimum_liquidity=10_000_000,
            maximum_volatility=0.05,
        ),
        construction={
            "selection": "TOP_PERCENT",
            "top_percent": 50,
            "top_n": 5,
            "weighting": "SCORE_WEIGHTED",
            "max_single_position_weight": 0.30,
        },
        rebalance="WEEKLY",
        gross_notional=25_000,
        initial_cash=100_000,
        fee_bps=5,
        slippage_bps=7,
    )
    record = engine.create(request)

    assert record.factor_ids == ("momentum", "volatility", "roe")
    assert record.combination == "USER_DEFINED_WEIGHT"
    assert record.construction.selection == "TOP_PERCENT"
    assert record.rebalance == "WEEKLY"
    research = record.stages[0]
    assert len(research.factor_checks) == 3
    roe_check = next(item for item in research.factor_checks if item.factor_id == "roe")
    assert roe_check.data_source == "FUNDAMENTAL"
    assert roe_check.direction == "HIGH"
    assert roe_check.coverage < 1.0
    assert roe_check.missing_observations > 0
    assert research.cost_preview.rebalance_count == len(research.snapshots)
    assert research.cost_preview.fees >= 0
    assert research.cost_preview.slippage >= 0
    risk = research.risk_decomposition
    assert risk is not None
    assert risk.status == "AVAILABLE"
    assert risk.annualization_factor == 252
    assert risk.volatility_basis == "ANNUALIZED"
    assert risk.portfolio_volatility is not None and risk.portfolio_volatility >= 0.0
    assert risk.expected_shortfall_95 is not None
    assert risk.historical_var_95 is not None
    assert risk.expected_shortfall_95 >= risk.historical_var_95
    assert risk.correlation is not None and risk.covariance is not None
    assert risk.correlation.symbols == risk.covariance.symbols
    assert sum(item.component_risk_share for item in risk.contributions) == pytest.approx(1.0)
    assert sum(
        item.component_contribution_to_volatility for item in risk.contributions
    ) == pytest.approx(risk.portfolio_volatility)
    assert "not a forecast" in risk.boundary_disclosure

    snapshots_with_googl = [
        snapshot
        for snapshot in research.snapshots
        if any(position.symbol == "GOOGL" for position in snapshot.positions)
    ]
    assert snapshots_with_googl
    googl = next(
        position for position in snapshots_with_googl[-1].positions if position.symbol == "GOOGL"
    )
    roe_evidence = next(item for item in googl.factors if item.factor_id == "roe")
    assert roe_evidence.available is False
    assert roe_evidence.rank is None
    assert googl.composite_score is not None
    assert all(
        position.target_weight <= 0.30 + 1e-12
        for snapshot in research.snapshots
        for position in snapshot.positions
        if position.selected
    )


def test_portfolio_lab_all_four_combination_methods_are_backend_computed(tmp_path: Path) -> None:
    engine, _, _, ids = _portfolio_engine(tmp_path)
    for combination in (
        "EQUAL_WEIGHT",
        "USER_DEFINED_WEIGHT",
        "RANK_AVERAGE",
        "Z_SCORE_COMPOSITE",
    ):
        weights = (0.5, 0.3, 0.2) if combination == "USER_DEFINED_WEIGHT" else (1 / 3,) * 3
        record = engine.create(
            CreatePortfolioResearch(
                name=f"{combination} portfolio",
                factors=tuple(
                    PortfolioFactorRef(research_id=research_id, weight=weight)
                    for research_id, weight in zip(ids, weights, strict=True)
                ),
                combination=combination,
                construction={
                    "selection": "TOP_N",
                    "top_n": 3,
                    "top_percent": 50,
                    "weighting": "EQUAL_WEIGHT",
                    "max_single_position_weight": 0.25,
                },
                rebalance="MONTHLY",
            )
        )
        assert record.stages[0].snapshots
        assert all(
            position.target_weight <= 0.25 + 1e-12
            for snapshot in record.stages[0].snapshots
            for position in snapshot.positions
            if position.selected
        )
        # Three positions at a 25% cap intentionally leave cash instead of violating the cap.
        snapshot = record.stages[0].snapshots[-1]
        assert sum(position.target_weight for position in snapshot.positions) <= 0.75 + 1e-9


def test_portfolio_lab_creates_native_strategy_and_uses_existing_execution_runtime(
    tmp_path: Path,
) -> None:
    engine, datasets, factor_repository, ids = _portfolio_engine(tmp_path)
    record = engine.create(
        CreatePortfolioResearch(
            name="Native portfolio",
            factors=tuple(
                PortfolioFactorRef(research_id=research_id, weight=1 / 3) for research_id in ids
            ),
            combination="RANK_AVERAGE",
            filters=PortfolioFilters(require_factor_availability=True),
            construction={
                "selection": "TOP_N",
                "top_n": 2,
                "top_percent": 20,
                "weighting": "EQUAL_WEIGHT",
                "max_single_position_weight": 0.40,
            },
            rebalance="MONTHLY",
            gross_notional=20_000,
            initial_cash=100_000,
            fee_bps=5,
            slippage_bps=5,
        )
    )
    strategy_registry = StrategyRegistry(tmp_path)
    artifact = PortfolioStrategyFactory(
        strategy_registry,
        factor_repository,
        tmp_path,
    ).create(record)
    result = execute_open_run(
        strategy_id=artifact.strategy_id,
        dataset_id=record.dataset_id,
        parameters={},
        strategy_registry=strategy_registry,
        dataset_registry=datasets,
    )

    assert result.status == "COMPLETED"
    assert result.trace is not None
    assert result.trace.strategy.strategy_id == artifact.strategy_id
    assert any(event.execution_events for event in result.trace.timeline)
    factor_dependencies = [
        dependency for event in result.trace.timeline for dependency in event.data_dependencies
    ]
    assert factor_dependencies
    assert all(item.available_at <= item.used_at for item in factor_dependencies)


def test_portfolio_lab_long_short_targets_are_dollar_neutral_and_runtime_executable(
    tmp_path: Path,
) -> None:
    engine, datasets, factor_repository, ids = _portfolio_engine(tmp_path)
    record = engine.create(
        CreatePortfolioResearch(
            name="Long short portfolio",
            factors=tuple(
                PortfolioFactorRef(research_id=research_id, weight=1 / 3) for research_id in ids
            ),
            combination="RANK_AVERAGE",
            construction={
                "mode": "LONG_SHORT",
                "selection": "TOP_N",
                "top_n": 2,
                "top_percent": 20,
                "weighting": "EQUAL_WEIGHT",
                "max_single_position_weight": 1.0,
            },
            rebalance="MONTHLY",
            gross_notional=20_000,
        )
    )
    snapshot = record.stages[0].snapshots[-1]
    weights = [item.target_weight for item in snapshot.positions if item.selected]
    assert any(value > 0 for value in weights)
    assert any(value < 0 for value in weights)
    assert sum(weights) == pytest.approx(0.0, abs=1e-12)
    assert sum(abs(value) for value in weights) == pytest.approx(1.0, abs=1e-12)

    strategies = StrategyRegistry(tmp_path)
    artifact = PortfolioStrategyFactory(strategies, factor_repository, tmp_path).create(record)
    result = execute_open_run(
        strategy_id=artifact.strategy_id,
        dataset_id=record.dataset_id,
        parameters={},
        strategy_registry=strategies,
        dataset_registry=datasets,
    )
    assert result.status == "COMPLETED"
    assert result.trace is not None
    execution_sides = [
        execution.side for event in result.trace.timeline for execution in event.execution_events
    ]
    assert execution_sides
    assert "BUY" in execution_sides
    assert "SELL" in execution_sides
