from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.datasets import DatasetProvenance, DatasetRegistry
from app.discovery import (
    CreateHypothesis,
    CreateHypothesisRevision,
    DiscoveryEngine,
    HypothesisRepository,
)
from app.factor_relationships import CreateFactorRelationship, FactorRelationshipEngine
from app.factor_relationships.repository import FactorRelationshipRepository
from app.factors import FactorResearchEngine
from app.factors.models import CreateFactorResearch, ResearchPeriod, ResearchPeriods
from app.factors.repository import FactorResearchRepository
from app.main import app
from app.market_data.models import MarketBar
from app.portfolio_lab import PortfolioResearchEngine, PortfolioStrategyFactory
from app.portfolio_lab.repository import PortfolioResearchRepository
from app.research_ledger import ResearchLedgerRepository
from app.runs import RunLedger
from app.sdk.registry import StrategyRegistry
from app.walk_forward import CreateWalkForwardResearch, WalkForwardConfig, WalkForwardEngine
from app.walk_forward.repository import WalkForwardRepository

SYMBOLS = ("AAPL", "MSFT", "AMZN", "NVDA", "META", "GOOGL")


def _dataset(registry: DatasetRegistry) -> str:
    start = datetime(2022, 1, 3, tzinfo=UTC)
    bars: list[MarketBar] = []
    for day in range(220):
        timestamp = start + timedelta(days=day)
        for rank, symbol in enumerate(SYMBOLS, start=1):
            trend = day * (0.035 + rank * 0.014)
            cycle = ((day % 15) - 7) * rank * 0.04
            close = 65 + rank * 15 + trend + cycle
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
                    volume=900_000 + rank * 125_000 + day * 300,
                    provider="alpaca",
                    feed="iex",
                    provider_event_id=f"discovery:{symbol}:{day}",
                )
            )
    end = start + timedelta(days=219)
    return registry.commit_provider_bars(
        name="Discovery provider-backed contract",
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
        research=ResearchPeriod(start=start, end=start + timedelta(days=99)),
        validation=ResearchPeriod(
            start=start + timedelta(days=100),
            end=start + timedelta(days=159),
        ),
        holdout=ResearchPeriod(
            start=start + timedelta(days=160),
            end=start + timedelta(days=219),
        ),
    )


def _assets(
    tmp_path: Path,
) -> tuple[
    DiscoveryEngine,
    FactorResearchEngine,
    FactorResearchRepository,
    HypothesisRepository,
    ResearchLedgerRepository,
    DatasetRegistry,
    StrategyRegistry,
    tuple[str, ...],
]:
    datasets = DatasetRegistry(tmp_path)
    dataset_id = _dataset(datasets)
    factor_engine = FactorResearchEngine(datasets)
    factors = FactorResearchRepository(tmp_path)
    research_ids: list[str] = []
    for name, factor_id, parameters in (
        ("Momentum", "momentum", {"lookback": 10}),
        ("Reversal", "reversal", {"lookback": 10}),
        ("Low Volatility", "volatility", {"lookback": 10}),
    ):
        record = factor_engine.create(
            CreateFactorResearch(
                name=name,
                dataset_id=dataset_id,
                factor_id=factor_id,
                parameters=parameters,
                periods=_periods(),
            )
        )
        factors.save(record)
        research_ids.append(record.research_id)

    ledger = ResearchLedgerRepository(tmp_path)
    relationships = FactorRelationshipRepository(tmp_path)
    relationship_engine = FactorRelationshipEngine(
        datasets,
        factors,
        factor_engine,
        ledger,
    )
    relationships.save(
        relationship_engine.create(
            CreateFactorRelationship(
                name="Discovery source relationship",
                factor_research_ids=tuple(research_ids),
                stage="RESEARCH",
                horizon=5,
                rolling_window=10,
                top_percent=25,
            )
        )
    )

    walk_forward = WalkForwardRepository(tmp_path)
    walk_engine = WalkForwardEngine(
        datasets,
        factors,
        factor_engine,
        StrategyRegistry(tmp_path),
        RunLedger(),
        ledger,
    )
    walk_forward.save(
        walk_engine.create(
            CreateWalkForwardResearch(
                name="Discovery source walk-forward",
                factor_research_id=research_ids[0],
                config=WalkForwardConfig(
                    research_months=1,
                    validation_months=1,
                    forward_months=1,
                    step_months=1,
                ),
                horizon=5,
            )
        )
    )

    portfolios = PortfolioResearchRepository(tmp_path)
    hypotheses = HypothesisRepository(tmp_path)
    strategies = StrategyRegistry(tmp_path)
    portfolio_engine = PortfolioResearchEngine(datasets, factors, factor_engine, ledger)
    discovery = DiscoveryEngine(
        datasets,
        factors,
        relationships,
        walk_forward,
        portfolios,
        hypotheses,
        portfolio_engine,
        PortfolioStrategyFactory(strategies, factors, tmp_path),
        ledger,
    )
    return (
        discovery,
        factor_engine,
        factors,
        hypotheses,
        ledger,
        datasets,
        strategies,
        tuple(research_ids),
    )


def _request(ids: tuple[str, ...]) -> CreateHypothesis:
    return CreateHypothesis(
        title="Quality of diversified price signals",
        description=(
            "Test whether Momentum, Reversal and Low Volatility form a more stable "
            "long-only research portfolio than a single signal."
        ),
        universe=SYMBOLS,
        factor_research_ids=ids,
        expected_relationship="Low redundancy may improve stability without optimizing weights.",
        holding_horizon="5 trading days",
        rebalance_idea="MONTHLY",
        risk_assumptions=("Long-only", "No leverage assumption beyond configured gross notional"),
    )


def test_discovery_router_is_registered_in_native_api() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/hypotheses" in paths
    assert "/api/hypotheses/suggestions" in paths
    assert "/api/hypotheses/{hypothesis_id}/candidate" in paths
    assert "/api/hypotheses/{hypothesis_id}/reveal-holdout" in paths
    assert "/api/hypotheses/{hypothesis_id}/revisions" in paths


def test_discovery_complete_hypothesis_to_native_run_with_revision_discipline(
    tmp_path: Path,
) -> None:
    (
        engine,
        factor_engine,
        factors,
        hypotheses,
        ledger,
        datasets,
        strategies,
        ids,
    ) = _assets(tmp_path)

    hypothesis = engine.create(_request(ids))
    source_relationship_ids = hypothesis.lineage.relationship_ids
    assert hypothesis.status == "DRAFT"
    assert hypothesis.created_with_known_stage == "RESEARCH"
    assert hypothesis.outcome == "INSUFFICIENT_EVIDENCE"
    assert {item.source_type for item in hypothesis.evidence} >= {
        "FACTOR",
        "RELATIONSHIP",
        "WALK_FORWARD",
    }
    assert hypothesis.lineage.relationship_ids
    assert hypothesis.lineage.walk_forward_ids
    assert "cannot calculate quantitative metrics" in hypothesis.ai_boundary

    existing_relationship = engine.relationships.list()[0]
    engine.relationships.save(
        existing_relationship.model_copy(
            update={"relationship_id": "factor-relationship-created-after-hypothesis"}
        )
    )
    hypothesis = engine.build_candidate(hypothesis)
    assert hypothesis.status == "RESEARCHED"
    assert hypothesis.lineage.relationship_ids == source_relationship_ids
    assert hypothesis.candidate.combination == "RANK_AVERAGE"
    assert hypothesis.candidate.selection == "TOP_PERCENT"
    assert hypothesis.candidate.weighting == "EQUAL_WEIGHT"
    assert hypothesis.candidate.long_only is True
    assert hypothesis.lineage.portfolio_research_id is not None
    assert any(item.source_type == "PORTFOLIO" for item in hypothesis.evidence)
    with pytest.raises(ValueError, match="Validation must be completed"):
        engine.reveal_holdout(hypothesis)

    for research_id in ids:
        record = factors.get(research_id)
        assert record is not None
        factors.save(factor_engine.reveal(record, "VALIDATION"))
    hypothesis = engine.validate(hypothesis)
    assert hypothesis.status == "VALIDATED"
    assert not any(
        item.source_type == "FACTOR" and item.stage == "VALIDATION" for item in hypothesis.evidence
    )
    assert hypothesis.outcome in {
        "SUPPORTED",
        "MIXED",
        "NOT_SUPPORTED",
        "INSUFFICIENT_EVIDENCE",
    }
    assert any(item.stage == "VALIDATION" for item in hypothesis.evidence)

    revision = engine.create_revision(
        hypothesis,
        CreateHypothesisRevision(
            holding_horizon="20 trading days",
            revision_reason="Test a different holding horizon as a separate experiment.",
        ),
    )
    assert revision.family_id == hypothesis.family_id
    assert revision.parent_hypothesis_id == hypothesis.hypothesis_id
    assert revision.revision == hypothesis.revision + 1
    assert revision.status == "DRAFT"
    assert revision.lineage.portfolio_research_id is None
    assert hypotheses.get(hypothesis.hypothesis_id) == hypothesis

    for research_id in ids:
        record = factors.get(research_id)
        assert record is not None
        factors.save(factor_engine.reveal(record, "HOLDOUT"))
    hypothesis = engine.reveal_holdout(hypothesis)
    assert hypothesis.status == "HOLDOUT_REVEALED"
    assert any(
        item.source_type == "PORTFOLIO" and item.stage == "HOLDOUT" for item in hypothesis.evidence
    )
    assert not any(
        item.source_type == "FACTOR" and item.stage == "HOLDOUT" for item in hypothesis.evidence
    )

    hypothesis = engine.create_strategy(hypothesis)
    assert hypothesis.status == "STRATEGY_CREATED"
    assert hypothesis.lineage.strategy_id is not None
    run = RunLedger().create(
        strategy_id=hypothesis.lineage.strategy_id,
        dataset_id=hypothesis.dataset_id,
        parameters={},
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    assert run.manifest.status == "COMPLETED"
    assert run.manifest.trace_id is not None
    assert run.trace is not None
    with pytest.raises(ValueError, match="Run and Trace"):
        engine.attach_run(hypothesis, run.manifest.run_id, "trace-not-from-this-run")
    hypothesis = engine.attach_run(
        hypothesis,
        run.manifest.run_id,
        run.manifest.trace_id,
    )
    assert hypothesis.lineage.run_ids == (run.manifest.run_id,)
    assert hypothesis.lineage.trace_ids == (run.manifest.trace_id,)

    hypothesis_entries = [item for item in ledger.list() if item.kind == "HYPOTHESIS"]
    events = {item.metadata.get("event") for item in hypothesis_entries}
    assert {
        "CREATE_HYPOTHESIS",
        "CREATE_CANDIDATE",
        "VALIDATE",
        "REVEAL_HOLDOUT",
        "CREATE_NATIVE_STRATEGY",
        "ATTACH_RUN",
        "CREATE_REVISION",
    } <= events
    assert all(item.dataset_fingerprints for item in hypothesis_entries)
    assert any(
        item.known_evidence == ("RESEARCH", "VALIDATION", "HOLDOUT") for item in hypothesis_entries
    )


def test_discovery_suggestions_never_use_holdout_relationships(tmp_path: Path) -> None:
    engine, _, _, _, _, _, _, _ = _assets(tmp_path)
    assert all(item.label == "RESEARCH IDEA" for item in engine.suggestions())
    assert all("recommendation" in item.rationale for item in engine.suggestions())
