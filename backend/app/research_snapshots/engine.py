from __future__ import annotations

import json
import platform
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version

from pydantic import BaseModel

from app.corporate_actions.repository import CorporateActionRepository
from app.datasets import DatasetRegistry
from app.discovery.repository import HypothesisRepository
from app.factor_relationships.repository import FactorRelationshipRepository
from app.factors.models import FactorResearchRecord
from app.factors.repository import FactorResearchRepository
from app.portfolio_lab.models import PortfolioResearchRecord
from app.portfolio_lab.repository import PortfolioResearchRepository
from app.research_ledger import ResearchLedgerEntry, ResearchLedgerRepository
from app.runs import RunRepository
from app.sdk.registry import StrategyRegistry
from app.trace.serialization import trace_to_json
from app.universes.repository import UniverseRepository
from app.walk_forward.repository import WalkForwardRepository

from .models import (
    ArtifactKind,
    CreateResearchSnapshot,
    EnvironmentDependency,
    FrozenArtifact,
    ResearchSnapshot,
    SnapshotEnvironment,
    SnapshotLineage,
    SnapshotParameterSet,
    SnapshotParameterValue,
    SnapshotPeriod,
    SnapshotScalar,
    SnapshotTimeBoundaries,
    sha256_text,
    snapshot_content_fingerprint,
)
from .repository import ResearchSnapshotRepository


def _canonical_json(value: BaseModel | dict[str, object]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _artifact(
    kind: ArtifactKind,
    artifact_id: str,
    value: BaseModel | dict[str, object],
    source_revision: str | None = None,
) -> FrozenArtifact:
    payload = _canonical_json(value)
    payload_sha256 = sha256_text(payload)
    return FrozenArtifact(
        kind=kind,
        artifact_id=artifact_id,
        source_revision=source_revision or payload_sha256,
        payload_sha256=payload_sha256,
        payload_json=payload,
    )


def _values(values: Mapping[str, SnapshotScalar]) -> tuple[SnapshotParameterValue, ...]:
    return tuple(
        SnapshotParameterValue(key=key, value=value) for key, value in sorted(values.items())
    )


class ResearchSnapshotEngine:
    def __init__(
        self,
        datasets: DatasetRegistry,
        factors: FactorResearchRepository,
        relationships: FactorRelationshipRepository,
        walk_forward: WalkForwardRepository,
        hypotheses: HypothesisRepository,
        portfolios: PortfolioResearchRepository,
        strategies: StrategyRegistry,
        runs: RunRepository,
        snapshots: ResearchSnapshotRepository,
        ledger: ResearchLedgerRepository,
        universes: UniverseRepository | None = None,
        corporate_actions: CorporateActionRepository | None = None,
    ) -> None:
        self.datasets = datasets
        self.factors = factors
        self.relationships = relationships
        self.walk_forward = walk_forward
        self.hypotheses = hypotheses
        self.portfolios = portfolios
        self.strategies = strategies
        self.runs = runs
        self.snapshots = snapshots
        self.ledger = ledger
        self.universes = universes or UniverseRepository(datasets.workspace_root)
        self.corporate_actions = corporate_actions or CorporateActionRepository(
            datasets.workspace_root
        )

    @staticmethod
    def _dependency(name: str) -> EnvironmentDependency:
        try:
            installed = version(name)
        except PackageNotFoundError:
            installed = "not-installed"
        return EnvironmentDependency(name=name, version=installed)

    @classmethod
    def _environment(cls) -> SnapshotEnvironment:
        return SnapshotEnvironment(
            python_version=platform.python_version(),
            python_implementation=platform.python_implementation(),
            platform=platform.platform(),
            machine=platform.machine(),
            vqd_version=cls._dependency("visual-quant-debugger-backend").version,
            dependencies=tuple(
                cls._dependency(name) for name in ("fastapi", "numpy", "pydantic", "uvicorn")
            ),
        )

    def _factor_records(
        self, research_ids: tuple[str, ...], dataset_id: str, dataset_revision: str
    ) -> tuple[FactorResearchRecord, ...]:
        records: list[FactorResearchRecord] = []
        for research_id in research_ids:
            record = self.factors.get(research_id)
            if record is None:
                raise KeyError(f"Factor research '{research_id}' was not found")
            if record.dataset_id != dataset_id or record.dataset_revision != dataset_revision:
                raise ValueError(
                    f"Factor research '{research_id}' no longer matches the Hypothesis dataset"
                )
            records.append(record)
        return tuple(records)

    @staticmethod
    def _portfolio_parameters(record: PortfolioResearchRecord) -> dict[str, SnapshotScalar]:
        return {
            "combination": record.combination,
            "construction.max_single_position_weight": (
                record.construction.max_single_position_weight
            ),
            "construction.selection": record.construction.selection,
            "construction.top_n": record.construction.top_n,
            "construction.top_percent": record.construction.top_percent,
            "construction.weighting": record.construction.weighting,
            "fee_bps": record.fee_bps,
            "filters.maximum_volatility": record.filters.maximum_volatility,
            "filters.minimum_liquidity": record.filters.minimum_liquidity,
            "filters.require_factor_availability": record.filters.require_factor_availability,
            "gross_notional": record.gross_notional,
            "initial_cash": record.initial_cash,
            "rebalance": record.rebalance,
            "slippage_bps": record.slippage_bps,
        }

    def create(self, request: CreateResearchSnapshot) -> ResearchSnapshot:
        hypothesis = self.hypotheses.get(request.hypothesis_id)
        if hypothesis is None:
            raise KeyError(f"Hypothesis '{request.hypothesis_id}' was not found")
        lineage = hypothesis.lineage
        if (
            lineage.portfolio_research_id is None
            or lineage.strategy_id is None
            or not lineage.run_ids
            or len(lineage.run_ids) != len(lineage.trace_ids)
        ):
            raise ValueError(
                "Research Snapshot requires a Hypothesis with Portfolio, Strategy, and matched "
                "Run / Trace lineage"
            )

        dataset = self.datasets.get(hypothesis.dataset_id)
        if dataset is None:
            raise KeyError(f"Dataset '{hypothesis.dataset_id}' was not found")
        if dataset.content_fingerprint != hypothesis.dataset_fingerprint:
            raise ValueError("Dataset revision no longer matches the Hypothesis")

        factors = self._factor_records(
            lineage.factor_research_ids,
            hypothesis.dataset_id,
            hypothesis.dataset_fingerprint,
        )
        universe_ids = tuple(
            sorted({item.universe_id for item in factors if item.universe_id is not None})
        )
        corporate_action_dataset_ids = tuple(
            sorted(
                {
                    item.corporate_action_dataset_id
                    for item in factors
                    if item.corporate_action_dataset_id is not None
                }
            )
        )
        universe_records = []
        for universe_id in universe_ids:
            universe = self.universes.get(universe_id)
            if universe is None:
                raise KeyError(f"Universe '{universe_id}' was not found")
            universe_records.append(universe)
        corporate_action_records = []
        for corporate_action_dataset_id in corporate_action_dataset_ids:
            corporate_action_dataset = self.corporate_actions.get(corporate_action_dataset_id)
            if corporate_action_dataset is None:
                raise KeyError(
                    f"Corporate Action dataset '{corporate_action_dataset_id}' was not found"
                )
            corporate_action_records.append(corporate_action_dataset)
        portfolio = self.portfolios.get(lineage.portfolio_research_id)
        if portfolio is None:
            raise KeyError(f"Portfolio research '{lineage.portfolio_research_id}' was not found")
        if (
            portfolio.dataset_id != hypothesis.dataset_id
            or portfolio.dataset_fingerprint != hypothesis.dataset_fingerprint
            or portfolio.strategy is None
            or portfolio.strategy.strategy_id != lineage.strategy_id
        ):
            raise ValueError("Portfolio or Strategy revision no longer matches the Hypothesis")
        strategy_revision = portfolio.strategy.source_fingerprint

        relationship_records = []
        for relationship_id in lineage.relationship_ids:
            relationship_record = self.relationships.get(relationship_id)
            if relationship_record is None:
                raise KeyError(f"Factor relationship '{relationship_id}' was not found")
            if (
                relationship_record.dataset_id != hypothesis.dataset_id
                or relationship_record.dataset_fingerprint != hypothesis.dataset_fingerprint
            ):
                raise ValueError(
                    f"Factor relationship '{relationship_id}' uses another dataset revision"
                )
            relationship_records.append(relationship_record)

        walk_forward_records = []
        for walk_forward_id in lineage.walk_forward_ids:
            walk_forward_record = self.walk_forward.get(walk_forward_id)
            if walk_forward_record is None:
                raise KeyError(f"Walk-Forward research '{walk_forward_id}' was not found")
            if (
                walk_forward_record.dataset_id != hypothesis.dataset_id
                or walk_forward_record.dataset_fingerprint != hypothesis.dataset_fingerprint
            ):
                raise ValueError(
                    f"Walk-Forward research '{walk_forward_id}' uses another dataset revision"
                )
            walk_forward_records.append(walk_forward_record)

        manifests = []
        traces = []
        strategy_source: str | None = None
        strategy_source_path: str | None = None
        strategy_class_name: str | None = None
        for run_id, trace_id in zip(lineage.run_ids, lineage.trace_ids, strict=True):
            manifest = self.runs.get_manifest(run_id)
            if manifest.trace_id != trace_id:
                raise ValueError(f"Run '{run_id}' does not own Trace '{trace_id}'")
            if (
                manifest.strategy.strategy_id != lineage.strategy_id
                or manifest.strategy.source_fingerprint != strategy_revision
            ):
                raise ValueError(f"Run '{run_id}' uses another Strategy revision")
            if (
                manifest.dataset.dataset_id != hypothesis.dataset_id
                or manifest.dataset.content_fingerprint != hypothesis.dataset_fingerprint
            ):
                raise ValueError(f"Run '{run_id}' uses another Dataset revision")
            source = self.runs.strategy_source(run_id)
            if source.sha256 != strategy_revision:
                raise ValueError(f"Run '{run_id}' Strategy source revision is inconsistent")
            if strategy_source is not None and strategy_source != source.source:
                raise ValueError("Attached Runs contain different Strategy source bytes")
            strategy_source = source.source
            strategy_source_path = manifest.strategy.original_source_path
            strategy_class_name = manifest.strategy.class_name
            trace = self.runs.load_trace_for_run(run_id)
            manifests.append(manifest)
            traces.append(trace)

        if strategy_source is None:
            raise ValueError("Research Snapshot could not resolve immutable Strategy source")

        registration = self.strategies.get_registration(lineage.strategy_id)
        strategy_payload: dict[str, object] = {
            "strategy_id": lineage.strategy_id,
            "source_fingerprint": strategy_revision,
            "source_path_at_run": strategy_source_path or "",
            "class_name": strategy_class_name or "",
            "source": strategy_source,
            "registration": (
                None if registration is None else registration.model_dump(mode="json")
            ),
        }

        factor_artifacts = tuple(
            _artifact(
                "FACTOR_RESEARCH",
                item.research_id,
                item,
                item.factor.source_fingerprint or item.factor.version,
            )
            for item in factors
        )
        relationship_artifacts = tuple(
            _artifact("FACTOR_RELATIONSHIP", item.relationship_id, item)
            for item in relationship_records
        )
        walk_forward_artifacts = tuple(
            _artifact(
                "WALK_FORWARD",
                item.walk_forward_id,
                item,
                item.factor_revision,
            )
            for item in walk_forward_records
        )
        run_artifacts = tuple(
            _artifact("RUN_MANIFEST", item.run_id, item, item.run_fingerprint) for item in manifests
        )
        trace_artifacts = tuple(
            _artifact(
                "TRACE",
                trace_id,
                {"trace": json.loads(trace_to_json(item))},
                manifest.artifacts.trace_sha256,
            )
            for item, manifest, trace_id in zip(traces, manifests, lineage.trace_ids, strict=True)
        )
        universe_artifacts = tuple(
            _artifact("UNIVERSE", item.universe_id, item) for item in universe_records
        )
        corporate_action_artifacts = tuple(
            _artifact(
                "CORPORATE_ACTION_DATASET",
                item.corporate_action_dataset_id,
                item,
                item.content_fingerprint,
            )
            for item in corporate_action_records
        )

        parameters: list[SnapshotParameterSet] = [
            SnapshotParameterSet(
                owner_type="HYPOTHESIS",
                owner_id=hypothesis.hypothesis_id,
                values=_values(
                    {
                        "holding_horizon": hypothesis.holding_horizon,
                        "rebalance_idea": hypothesis.rebalance_idea,
                        "revision": hypothesis.revision,
                    }
                ),
            )
        ]
        parameters.extend(
            SnapshotParameterSet(
                owner_type="FACTOR",
                owner_id=item.research_id,
                values=_values(item.parameters),
            )
            for item in factors
        )
        parameters.append(
            SnapshotParameterSet(
                owner_type="PORTFOLIO",
                owner_id=portfolio.portfolio_research_id,
                values=_values(self._portfolio_parameters(portfolio)),
            )
        )
        parameters.append(
            SnapshotParameterSet(
                owner_type="STRATEGY",
                owner_id=lineage.strategy_id,
                values=_values({"source_fingerprint": strategy_revision}),
            )
        )
        parameters.extend(
            SnapshotParameterSet(
                owner_type="RUN",
                owner_id=item.run_id,
                values=_values(item.parameters),
            )
            for item in manifests
        )

        periods = factors[0].periods
        time_boundaries = SnapshotTimeBoundaries(
            research=SnapshotPeriod(
                label="RESEARCH",
                source_id=factors[0].research_id,
                start=periods.research.start,
                end=periods.research.end,
            ),
            validation=SnapshotPeriod(
                label="VALIDATION",
                source_id=factors[0].research_id,
                start=periods.validation.start,
                end=periods.validation.end,
            ),
            holdout=SnapshotPeriod(
                label="HOLDOUT",
                source_id=factors[0].research_id,
                start=periods.holdout.start,
                end=periods.holdout.end,
            ),
            runs=tuple(
                SnapshotPeriod(
                    label=item.run_type,
                    source_id=item.run_id,
                    start=item.period.start,
                    end=item.period.end,
                    cutoff=item.period.cutoff,
                )
                for item in manifests
            ),
        )

        snapshot = ResearchSnapshot(
            snapshot_id=f"research-snapshot-{secrets.token_hex(12)}",
            name=request.name.strip(),
            created_at=datetime.now(UTC),
            content_fingerprint="sha256:" + "0" * 64,
            lineage=SnapshotLineage(
                dataset_id=hypothesis.dataset_id,
                universe_ids=universe_ids,
                corporate_action_dataset_ids=corporate_action_dataset_ids,
                factor_research_ids=lineage.factor_research_ids,
                factor_ids=lineage.factor_ids,
                relationship_ids=lineage.relationship_ids,
                walk_forward_ids=lineage.walk_forward_ids,
                hypothesis_id=hypothesis.hypothesis_id,
                hypothesis_revision=hypothesis.revision,
                portfolio_research_id=lineage.portfolio_research_id,
                strategy_id=lineage.strategy_id,
                run_ids=lineage.run_ids,
                trace_ids=lineage.trace_ids,
            ),
            dataset=_artifact(
                "DATASET",
                dataset.dataset_id,
                dataset,
                dataset.content_fingerprint,
            ),
            universes=universe_artifacts,
            corporate_actions=corporate_action_artifacts,
            factors=factor_artifacts,
            relationships=relationship_artifacts,
            walk_forward=walk_forward_artifacts,
            hypothesis=_artifact(
                "HYPOTHESIS",
                hypothesis.hypothesis_id,
                hypothesis,
                f"revision:{hypothesis.revision}",
            ),
            portfolio=_artifact(
                "PORTFOLIO_RESEARCH",
                portfolio.portfolio_research_id,
                portfolio,
            ),
            strategy=_artifact(
                "STRATEGY_SOURCE",
                lineage.strategy_id,
                strategy_payload,
                strategy_revision,
            ),
            runs=run_artifacts,
            traces=trace_artifacts,
            parameters=tuple(parameters),
            time_boundaries=time_boundaries,
            environment=self._environment(),
        )
        snapshot = snapshot.model_copy(
            update={"content_fingerprint": snapshot_content_fingerprint(snapshot)}
        )
        saved = self.snapshots.save(snapshot)
        self.ledger.save(
            ResearchLedgerEntry.new(
                entry_id=f"ledger-{secrets.token_hex(10)}",
                kind="SNAPSHOT",
                artifact_id=saved.snapshot_id,
                revision=hypothesis.revision,
                dataset_ids=(hypothesis.dataset_id,),
                dataset_fingerprints=(hypothesis.dataset_fingerprint,),
                factor_ids=lineage.factor_ids,
                factor_revisions=tuple(item.source_revision for item in factor_artifacts),
                strategy_id=lineage.strategy_id,
                strategy_revision=strategy_revision,
                known_evidence=tuple(hypothesis.source_revealed_stages.values()),
                result_refs=(
                    f"hypothesis:{hypothesis.hypothesis_id}",
                    f"portfolio:{portfolio.portfolio_research_id}",
                    f"strategy:{lineage.strategy_id}",
                    *(f"run:{item}" for item in lineage.run_ids),
                    *(f"trace:{item}" for item in lineage.trace_ids),
                ),
                metadata={
                    "event": "CREATE_RESEARCH_SNAPSHOT",
                    "content_fingerprint": saved.content_fingerprint,
                    "artifact_count": (
                        4
                        + len(saved.universes)
                        + len(saved.corporate_actions)
                        + len(saved.factors)
                        + len(saved.relationships)
                        + len(saved.walk_forward)
                        + len(saved.runs)
                        + len(saved.traces)
                    ),
                },
                hypothesis_id=hypothesis.hypothesis_id,
                portfolio_research_id=portfolio.portfolio_research_id,
                research_snapshot_id=saved.snapshot_id,
            )
        )
        return saved
