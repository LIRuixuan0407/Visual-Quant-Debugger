from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.datasets import DatasetProvenance, DatasetRegistry
from app.factors import FactorResearchEngine, FactorStrategyFactory
from app.factors.models import (
    CreateFactorResearch,
    CreateFactorStrategy,
    ResearchPeriod,
    ResearchPeriods,
)
from app.factors.repository import FactorResearchRepository
from app.main import app
from app.market_data.models import MarketBar
from app.research_ledger import ResearchLedgerRepository
from app.runs import RunLedger
from app.sdk.registry import StrategyRegistry
from app.walk_forward import (
    CreateWalkForwardResearch,
    FactorWindowMetrics,
    StrategyWindowMetrics,
    WalkForwardConfig,
    WalkForwardEngine,
    WalkForwardWindowDefinition,
    WalkForwardWindowResult,
)

SYMBOLS = ("AAPL", "MSFT", "AMZN", "NVDA", "META", "GOOGL")


def _dataset(registry: DatasetRegistry) -> str:
    start = datetime(2022, 1, 3, tzinfo=UTC)
    bars: list[MarketBar] = []
    for day in range(220):
        timestamp = start + timedelta(days=day)
        for rank, symbol in enumerate(SYMBOLS, start=1):
            trend = day * (0.04 + rank * 0.012)
            cycle = ((day % 13) - 6) * rank * 0.035
            close = 75 + rank * 11 + trend + cycle
            bars.append(
                MarketBar(
                    symbol=symbol,
                    timeframe="1Day",
                    event_time=timestamp,
                    available_at=timestamp,
                    received_at=timestamp,
                    open=close - 0.2,
                    high=close + 0.7,
                    low=close - 0.8,
                    close=close,
                    volume=900_000 + rank * 100_000 + day * 250,
                    provider="alpaca",
                    feed="iex",
                    provider_event_id=f"phase21:{symbol}:{day}",
                )
            )
    end = start + timedelta(days=219)
    return registry.commit_provider_bars(
        name="Phase 21 provider-backed contract",
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


def _assets(
    tmp_path: Path,
) -> tuple[
    WalkForwardEngine,
    FactorResearchRepository,
    ResearchLedgerRepository,
    str,
    str,
]:
    datasets = DatasetRegistry(tmp_path)
    dataset_id = _dataset(datasets)
    factor_engine = FactorResearchEngine(datasets)
    factors = FactorResearchRepository(tmp_path)
    start = datetime(2022, 1, 3, tzinfo=UTC)
    factor = factor_engine.create(
        CreateFactorResearch(
            name="Phase 21 momentum",
            dataset_id=dataset_id,
            factor_id="momentum",
            parameters={"lookback": 10},
            periods=ResearchPeriods(
                research=ResearchPeriod(start=start, end=start + timedelta(days=89)),
                validation=ResearchPeriod(
                    start=start + timedelta(days=90), end=start + timedelta(days=149)
                ),
                holdout=ResearchPeriod(
                    start=start + timedelta(days=150), end=start + timedelta(days=219)
                ),
            ),
        )
    )
    factors.save(factor)
    strategies = StrategyRegistry(tmp_path)
    strategy = FactorStrategyFactory(strategies, tmp_path).create(
        factor,
        CreateFactorStrategy(
            long_percent=35,
            rebalance_bars=5,
            gross_notional=20_000,
        ),
    )
    ledger = ResearchLedgerRepository(tmp_path)
    engine = WalkForwardEngine(
        datasets,
        factors,
        factor_engine,
        strategies,
        RunLedger(),
        ledger,
    )
    return engine, factors, ledger, factor.research_id, strategy.strategy_id


def test_phase21_router_is_registered_in_native_api() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/walk-forward" in paths
    assert "/api/walk-forward/{walk_forward_id}" in paths


def test_phase21_builds_pit_safe_windows_and_slices_one_native_trace(tmp_path: Path) -> None:
    engine, _, ledger, factor_id, strategy_id = _assets(tmp_path)
    record = engine.create(
        CreateWalkForwardResearch(
            name="Phase 21 stability contract",
            factor_research_id=factor_id,
            strategy_id=strategy_id,
            config=WalkForwardConfig(
                research_months=1,
                validation_months=1,
                forward_months=1,
                step_months=1,
            ),
            horizon=5,
            initial_cash=100_000,
            fee_bps=4,
            slippage_bps=6,
        )
    )

    assert len(record.windows) >= 3
    assert record.run_id is not None
    assert record.trace_id is not None
    assert record.strategy_revision is not None
    assert all(window.forward_strategy is not None for window in record.windows)
    assert all(window.forward.observation_count > 0 for window in record.windows)
    assert all(len(window.forward.quantile_returns) == 5 for window in record.windows)
    assert all(
        window.definition.research.end
        < window.definition.validation.start
        < window.definition.validation.end
        < window.definition.forward.start
        < window.definition.forward.end
        for window in record.windows
    )
    assert record.stability.rank_ic_distribution.count == len(record.windows)
    assert record.stability.strategy_return_distribution is not None
    assert record.stability.strategy_return_distribution.count == len(record.windows)

    entries = ledger.list()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind == "WALK_FORWARD"
    assert entry.artifact_id == record.walk_forward_id
    assert entry.dataset_fingerprints == (record.dataset_fingerprint,)
    assert entry.factor_revisions == (record.factor_revision,)
    assert entry.strategy_revision == record.strategy_revision
    assert f"run:{record.run_id}" in entry.result_refs
    assert entry.walk_forward_id == record.walk_forward_id
    assert len(entry.window_definitions) == len(record.windows)
    assert len(entry.research_results) == len(record.windows)
    assert len(entry.validation_results) == len(record.windows)
    assert len(entry.forward_results) == len(record.windows)
    assert len(entry.strategy_results) == len(record.windows)
    assert entry.forward_results[0]["q5"] is not None


def _factor_metrics(*, rank_ic: float, monotonic: bool, turnover: float) -> FactorWindowMetrics:
    return FactorWindowMetrics(
        observation_count=50,
        cross_section_count=10,
        ic=rank_ic,
        rank_ic=rank_ic,
        quantile_returns=(0.0, 0.01, 0.02, 0.03, 0.04),
        spread=0.04,
        coverage=1.0,
        turnover=turnover,
        monotonic=monotonic,
    )


def test_phase21_first_degradation_is_deterministic_and_replayable() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    definitions = tuple(
        WalkForwardWindowDefinition(
            index=index,
            research=ResearchPeriod(
                start=start + timedelta(days=(index - 1) * 90),
                end=start + timedelta(days=(index - 1) * 90 + 29),
            ),
            validation=ResearchPeriod(
                start=start + timedelta(days=(index - 1) * 90 + 30),
                end=start + timedelta(days=(index - 1) * 90 + 59),
            ),
            forward=ResearchPeriod(
                start=start + timedelta(days=(index - 1) * 90 + 60),
                end=start + timedelta(days=(index - 1) * 90 + 89),
            ),
        )
        for index in (1, 2)
    )
    first = _factor_metrics(rank_ic=0.12, monotonic=True, turnover=0.10)
    degraded = _factor_metrics(rank_ic=-0.08, monotonic=False, turnover=0.30)
    strategy = StrategyWindowMetrics(
        total_return=-0.08,
        sharpe=-1.0,
        max_drawdown=-0.12,
        trades=4,
        fees=12,
        slippage=10,
        net_costs=22,
    )
    windows = (
        WalkForwardWindowResult(
            definition=definitions[0],
            research=first,
            validation=first,
            forward=first,
            forward_strategy=strategy.model_copy(update={"max_drawdown": -0.02}),
        ),
        WalkForwardWindowResult(
            definition=definitions[1],
            research=degraded,
            validation=degraded,
            forward=degraded,
            forward_strategy=strategy,
        ),
    )

    result = WalkForwardEngine._first_degradation(
        windows,
        factor_research_id="factor-research-test",
        dataset_id="dataset-test",
        strategy_id="strategy-test",
        run_id="run-test",
        trace_id="trace-test",
    )

    assert result is not None
    assert result.window_index == 2
    assert result.reasons == (
        "FORWARD_RANK_IC_NEGATIVE",
        "QUANTILE_MONOTONICITY_DISAPPEARED",
        "MAX_DRAWDOWN_EXPANDED",
        "FACTOR_TURNOVER_WORSENED",
    )
    assert "factor_research_id=factor-research-test" in result.factor_lab_path
    assert result.replay_path is not None
    assert "run_id=run-test" in result.replay_path
