from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.api.discovery import engine as discovery_engine
from app.datasets import dataset_registry
from app.discovery import CreateHypothesis, hypothesis_repository
from app.factor_relationships import (
    CreateFactorRelationship,
    FactorRelationshipEngine,
    factor_relationship_repository,
)
from app.factors import FactorResearchEngine, factor_research_repository
from app.factors.models import CreateFactorResearch, FactorResearchRecord, ResearchStage
from app.factors.registry import factor_registry
from app.fundamentals import fundamental_repository
from app.research_ledger import research_ledger
from app.runs import run_ledger


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify persisted VQD research using real provider-backed data."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    relationships = commands.add_parser(
        "relationships",
        help="Verify Factor Relationship research on real market and fundamental data.",
    )
    relationships.add_argument("momentum_research_id")
    relationships.add_argument("volatility_research_id")
    relationships.add_argument("roe_research_id")
    relationships.add_argument("fundamental_dataset_id")
    relationships.add_argument(
        "--stage",
        choices=("RESEARCH", "VALIDATION", "HOLDOUT"),
        default="RESEARCH",
        help="Evidence boundary to use. HOLDOUT is never selected implicitly.",
    )
    relationships.add_argument("--horizon", type=int, choices=(1, 5, 20), default=20)
    relationships.add_argument("--rolling-window", type=int, default=60)
    relationships.add_argument("--top-percent", type=float, default=20.0)

    discovery = commands.add_parser(
        "discovery",
        help="Verify Strategy Discovery from persisted research evidence through backtest/replay.",
    )
    discovery.add_argument(
        "factor_research_ids",
        nargs="+",
        help="Existing real Factor research ids sharing one immutable research contract.",
    )
    discovery.add_argument("--title", default="Strategy Discovery verification hypothesis")
    discovery.add_argument(
        "--description",
        default="Test a fixed multi-Factor long-only candidate using existing research evidence.",
    )
    discovery.add_argument(
        "--expected-relationship",
        default=(
            "The selected Factors may provide complementary evidence without optimized weights."
        ),
    )
    discovery.add_argument("--holding-horizon", default="20 trading days")
    discovery.add_argument(
        "--rebalance",
        choices=("DAILY", "WEEKLY", "MONTHLY"),
        default="MONTHLY",
    )
    discovery.add_argument(
        "--reveal-holdout",
        action="store_true",
        help=(
            "Explicitly authorize Holdout reveal. Without this flag verification stops after "
            "Validation."
        ),
    )
    return parser.parse_args()


def _factor_record(research_id: str, expected_factor_id: str) -> FactorResearchRecord:
    record = factor_research_repository.get(research_id)
    if record is None:
        raise KeyError(f"Factor research '{research_id}' was not found")
    if record.factor.factor_id != expected_factor_id:
        raise ValueError(
            f"Expected '{expected_factor_id}' for {research_id}; found '{record.factor.factor_id}'"
        )
    return record


def _reveal_to(
    record: FactorResearchRecord,
    factor_engine: FactorResearchEngine,
    stage: ResearchStage,
) -> FactorResearchRecord:
    if stage in {"VALIDATION", "HOLDOUT"} and record.revealed_stage == "RESEARCH":
        record = factor_engine.reveal(record, "VALIDATION")
    if stage == "HOLDOUT" and record.revealed_stage == "VALIDATION":
        record = factor_engine.reveal(record, "HOLDOUT")
    return factor_research_repository.save(record)


def _matching_factor_research(
    *,
    base: FactorResearchRecord,
    factor_id: str,
    parameters: dict[str, int | float],
    fundamental_dataset_id: str | None,
) -> FactorResearchRecord | None:
    for summary in factor_research_repository.list():
        if summary.factor_id != factor_id or summary.dataset_id != base.dataset_id:
            continue
        record = factor_research_repository.get(summary.research_id)
        if (
            record is not None
            and record.periods == base.periods
            and record.universe == base.universe
            and record.parameters == parameters
            and record.fundamental_dataset_id == fundamental_dataset_id
        ):
            return record
    return None


def _ensure_factor_research(
    *,
    base: FactorResearchRecord,
    factor_engine: FactorResearchEngine,
    factor_id: str,
    name: str,
    parameters: dict[str, int | float],
    stage: ResearchStage,
    fundamental_dataset_id: str | None = None,
) -> FactorResearchRecord:
    existing = _matching_factor_research(
        base=base,
        factor_id=factor_id,
        parameters=parameters,
        fundamental_dataset_id=fundamental_dataset_id,
    )
    if existing is not None:
        return _reveal_to(existing, factor_engine, stage)
    record = factor_engine.create(
        CreateFactorResearch(
            name=name,
            dataset_id=base.dataset_id,
            factor_id=factor_id,
            parameters=parameters,
            periods=base.periods,
            universe=base.universe,
            universe_id=base.universe_id,
            fundamental_dataset_id=fundamental_dataset_id,
        )
    )
    return _reveal_to(record, factor_engine, stage)


def _verify_relationship_inputs(
    base: FactorResearchRecord,
    fundamental_dataset_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    dataset = dataset_registry.get(base.dataset_id)
    if dataset is None:
        raise KeyError(base.dataset_id)
    if dataset.source_type != "PROVIDER" or dataset.provenance is None:
        raise ValueError(
            "Factor Relationship verification requires a provider-backed market dataset"
        )
    if len(dataset.symbols) < 5:
        raise ValueError("Factor Relationship verification requires at least five securities")
    if dataset.provenance.provider.lower() != "alpaca":
        raise ValueError("Factor Relationship verification requires persisted Alpaca provenance")

    fundamental = fundamental_repository.get(fundamental_dataset_id)
    if fundamental is None:
        raise KeyError(fundamental_dataset_id)
    if fundamental.provider != "sec-companyfacts":
        raise ValueError("Factor Relationship verification requires SEC Company Facts fundamentals")
    if not fundamental.point_in_time_safe:
        raise ValueError("SEC fundamental dataset is not marked point-in-time safe")
    if len(fundamental.symbols) < 5:
        raise ValueError("Factor Relationship verification requires fundamentals for five stocks")

    market_info: dict[str, object] = {
        "dataset_id": dataset.dataset_id,
        "provider": dataset.provenance.provider,
        "feed": dataset.provenance.feed,
        "symbols": dataset.symbols,
        "start": dataset.start_time.isoformat(),
        "end": dataset.end_time.isoformat(),
        "fingerprint": dataset.content_fingerprint,
    }
    fundamental_info: dict[str, object] = {
        "fundamental_dataset_id": fundamental.fundamental_dataset_id,
        "provider": fundamental.provider,
        "symbols": fundamental.symbols,
        "fields": fundamental.fields,
        "point_in_time_safe": fundamental.point_in_time_safe,
        "restatement_safe": fundamental.restatement_safe,
        "fingerprint": fundamental.content_fingerprint,
    }
    return market_info, fundamental_info


def verify_relationships(args: argparse.Namespace) -> None:
    stage: ResearchStage = args.stage
    custom_source = (
        Path(__file__).parents[1] / "examples" / "custom_factors" / "volume_confirmed_momentum.py"
    ).resolve()
    if factor_registry.get_registration("volume-confirmed-momentum") is None:
        factor_registry.add(custom_source, "VolumeConfirmedMomentum")

    factor_engine = FactorResearchEngine(dataset_registry, factors=factor_registry)
    momentum = _factor_record(args.momentum_research_id, "momentum")
    volatility = _factor_record(args.volatility_research_id, "volatility")
    roe = _factor_record(args.roe_research_id, "roe")
    market_info, fundamental_info = _verify_relationship_inputs(
        momentum,
        args.fundamental_dataset_id,
    )
    for record in (volatility, roe):
        if (
            record.dataset_id != momentum.dataset_id
            or record.periods != momentum.periods
            or record.universe != momentum.universe
        ):
            raise ValueError("The supplied Factor studies do not share one research contract")
    if roe.fundamental_dataset_id != args.fundamental_dataset_id:
        raise ValueError("The supplied ROE study uses a different fundamental dataset")

    momentum = _reveal_to(momentum, factor_engine, stage)
    volatility = _reveal_to(volatility, factor_engine, stage)
    roe = _reveal_to(roe, factor_engine, stage)
    reversal = _ensure_factor_research(
        base=momentum,
        factor_engine=factor_engine,
        factor_id="reversal",
        name="Verification Reversal 20",
        parameters={"lookback": 20},
        stage=stage,
    )
    book_to_price = _ensure_factor_research(
        base=momentum,
        factor_engine=factor_engine,
        factor_id="book-to-price",
        name="Verification SEC PIT Book-to-Price",
        parameters={"max_age_days": 550},
        stage=stage,
        fundamental_dataset_id=args.fundamental_dataset_id,
    )
    custom = _ensure_factor_research(
        base=momentum,
        factor_engine=factor_engine,
        factor_id="volume-confirmed-momentum",
        name="Verification Custom Volume-Confirmed Momentum",
        parameters={"lookback": 20},
        stage=stage,
    )
    if custom.factor.origin != "CUSTOM":
        raise ValueError("Verification custom Factor did not load as CUSTOM origin")
    records = (momentum, reversal, volatility, roe, book_to_price, custom)

    relationship_engine = FactorRelationshipEngine(
        dataset_registry,
        factor_research_repository,
        factor_engine,
        research_ledger,
    )
    relationship = relationship_engine.create(
        CreateFactorRelationship(
            name="Six-Factor relationship verification",
            factor_research_ids=tuple(item.research_id for item in records),
            stage=stage,
            horizon=args.horizon,
            rolling_window=args.rolling_window,
            top_percent=args.top_percent,
            redundancy_threshold=0.75,
            overlap_threshold=0.60,
        )
    )
    factor_relationship_repository.save(relationship)

    print(
        json.dumps(
            {
                "verification": "FACTOR_RELATIONSHIP_COMPLETE",
                "market_data": market_info,
                "fundamentals": fundamental_info,
                "relationship_id": relationship.relationship_id,
                "stage": relationship.stage,
                "factor_research_ids": relationship.factor_research_ids,
                "factor_ids": relationship.factor_ids,
                "factor_revisions": relationship.factor_revisions,
                "value_correlations": [
                    item.model_dump(mode="json") for item in relationship.value_correlations
                ],
                "rank_correlations": [
                    item.model_dump(mode="json") for item in relationship.rank_correlations
                ],
                "return_correlations": [
                    item.model_dump(mode="json") for item in relationship.return_correlations
                ],
                "rolling_correlations": [
                    {
                        **item.model_dump(mode="json", exclude={"points"}),
                        "point_count": len(item.points),
                        "latest": (
                            None if not item.points else item.points[-1].model_dump(mode="json")
                        ),
                    }
                    for item in relationship.rolling_correlations
                ],
                "redundancy": [item.model_dump(mode="json") for item in relationship.redundancy],
                "exposure_overlap": [
                    item.model_dump(mode="json", exclude={"points"})
                    for item in relationship.exposure_overlap
                ],
                "incremental_information": [
                    item.model_dump(mode="json") for item in relationship.incremental_information
                ],
                "clusters": [item.model_dump(mode="json") for item in relationship.clusters],
                "disclosures": {
                    "correlation": relationship.correlation_methodology,
                    "incremental": relationship.incremental_disclosure,
                    "crowding": relationship.crowding_disclosure,
                },
            },
            indent=2,
        )
    )


def _verify_discovery_source_contract(research_ids: tuple[str, ...]) -> dict[str, object]:
    if len(research_ids) < 2:
        raise ValueError("Strategy Discovery verification requires at least two existing Factors")
    records = []
    for research_id in research_ids:
        record = factor_research_repository.get(research_id)
        if record is None:
            raise KeyError(f"Factor research '{research_id}' was not found")
        records.append(record)
    first = records[0]
    if any(
        record.dataset_id != first.dataset_id
        or record.dataset_revision != first.dataset_revision
        or record.universe != first.universe
        or record.periods != first.periods
        for record in records[1:]
    ):
        raise ValueError("Selected Factors do not share one immutable research contract")
    dataset = dataset_registry.get(first.dataset_id)
    if dataset is None:
        raise KeyError(first.dataset_id)
    if dataset.source_type != "PROVIDER" or dataset.provenance is None:
        raise ValueError("Strategy Discovery verification requires provider-backed historical data")
    if len(dataset.symbols) < 5:
        raise ValueError("Strategy Discovery verification requires at least five securities")
    if dataset.provenance.provider.lower() != "alpaca":
        raise ValueError("Strategy Discovery verification requires persisted Alpaca provenance")
    return {
        "dataset_id": dataset.dataset_id,
        "dataset_fingerprint": dataset.content_fingerprint,
        "provider": dataset.provenance.provider,
        "feed": dataset.provenance.feed,
        "symbols": dataset.symbols,
        "start": dataset.start_time.isoformat(),
        "end": dataset.end_time.isoformat(),
        "factor_ids": tuple(record.factor.factor_id for record in records),
        "factor_revisions": tuple(
            record.factor.source_fingerprint or record.factor.version for record in records
        ),
        "source_revealed_stages": {record.research_id: record.revealed_stage for record in records},
    }


def verify_discovery(args: argparse.Namespace) -> None:
    research_ids = tuple(args.factor_research_ids)
    source_contract = _verify_discovery_source_contract(research_ids)
    hypothesis = discovery_engine.create(
        CreateHypothesis(
            title=args.title,
            description=args.description,
            factor_research_ids=research_ids,
            expected_relationship=args.expected_relationship,
            holding_horizon=args.holding_horizon,
            rebalance_idea=args.rebalance,
            risk_assumptions=(
                "Long-only research candidate",
                "No automatic weight or parameter optimization",
            ),
        )
    )
    if not hypothesis.lineage.relationship_ids:
        raise ValueError("No Factor Relationship evidence is linked to this hypothesis")
    if not hypothesis.lineage.walk_forward_ids:
        raise ValueError("No Walk-Forward evidence is linked to this hypothesis")

    hypothesis = discovery_engine.build_candidate(hypothesis)
    hypothesis = discovery_engine.validate(hypothesis)
    if not args.reveal_holdout:
        hypothesis_repository.save(hypothesis)
        print(
            json.dumps(
                {
                    "verification": "STRATEGY_DISCOVERY_VALIDATION_COMPLETE_HOLDOUT_SEALED",
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "revision": hypothesis.revision,
                    "status": hypothesis.status,
                    "outcome": hypothesis.outcome,
                    "portfolio_research_id": hypothesis.lineage.portfolio_research_id,
                    "message": (
                        "Re-run discovery with --reveal-holdout only when you explicitly choose "
                        "to reveal the Holdout for this hypothesis."
                    ),
                },
                indent=2,
            )
        )
        return

    hypothesis = discovery_engine.reveal_holdout(hypothesis)
    hypothesis = discovery_engine.create_strategy(hypothesis)
    if hypothesis.lineage.strategy_id is None:
        raise RuntimeError("Native Strategy was not created")
    persisted = run_ledger.create(
        strategy_id=hypothesis.lineage.strategy_id,
        dataset_id=hypothesis.dataset_id,
        parameters={},
        research_cutoff=None,
    )
    if persisted.manifest.status != "COMPLETED":
        raise RuntimeError(f"Backtest did not complete: {persisted.manifest.status}")
    trace_id = persisted.manifest.trace_id
    if trace_id is None:
        raise RuntimeError("Backtest did not persist a Trace")
    trace = run_ledger.repository.load_trace(trace_id)
    if trace is None:
        raise RuntimeError("Replay verification could not load the persisted Trace")
    hypothesis = discovery_engine.attach_run(hypothesis, persisted.manifest.run_id, trace_id)

    metrics = persisted.manifest.metrics
    supporting = [item for item in hypothesis.evidence if item.stance == "SUPPORTING"]
    contradicting = [item for item in hypothesis.evidence if item.stance == "CONTRADICTING"]
    print(
        json.dumps(
            {
                "verification": "STRATEGY_DISCOVERY_COMPLETE",
                "source_contract": source_contract,
                "hypothesis_id": hypothesis.hypothesis_id,
                "family_id": hypothesis.family_id,
                "revision": hypothesis.revision,
                "created_with_known_stage": hypothesis.created_with_known_stage,
                "status": hypothesis.status,
                "outcome": hypothesis.outcome,
                "relationship_ids": hypothesis.lineage.relationship_ids,
                "walk_forward_ids": hypothesis.lineage.walk_forward_ids,
                "portfolio_research_id": hypothesis.lineage.portfolio_research_id,
                "strategy_id": hypothesis.lineage.strategy_id,
                "run_id": persisted.manifest.run_id,
                "trace_id": trace_id,
                "replay_events": len(trace.timeline),
                "supporting_evidence": [item.model_dump(mode="json") for item in supporting],
                "contradicting_evidence": [item.model_dump(mode="json") for item in contradicting],
                "backtest": None if metrics is None else metrics.model_dump(mode="json"),
                "disclosure": (
                    "A loss, mixed evidence, or NOT_SUPPORTED outcome is a valid verification "
                    "result. No result is optimized or modified for presentation."
                ),
            },
            indent=2,
        )
    )


def main() -> None:
    args = arguments()
    if args.command == "relationships":
        verify_relationships(args)
        return
    if args.command == "discovery":
        verify_discovery(args)
        return
    raise AssertionError(f"Unsupported verification command: {args.command}")


if __name__ == "__main__":
    main()
