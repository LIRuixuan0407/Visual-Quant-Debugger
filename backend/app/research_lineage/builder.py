from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from urllib.parse import quote

from pydantic import BaseModel

from app.corporate_actions.models import CorporateActionDataset
from app.corporate_actions.repository import CorporateActionRepository
from app.datasets import DatasetRegistry
from app.discovery.models import ResearchHypothesis
from app.discovery.repository import HypothesisRepository
from app.factor_relationships.models import FactorRelationshipRecord
from app.factor_relationships.repository import FactorRelationshipRepository
from app.factors.models import FactorDefinition, FactorResearchRecord
from app.factors.repository import FactorResearchRepository
from app.portfolio_lab.models import PortfolioResearchRecord
from app.portfolio_lab.repository import PortfolioResearchRepository
from app.research_integrity import ResearchIntegrityEngine
from app.research_snapshots.models import FrozenArtifact, ResearchSnapshot
from app.research_snapshots.repository import ResearchSnapshotRepository
from app.runs import ArtifactIntegrityError, RunNotFoundError, RunRepository
from app.runs.models import RunManifest
from app.sdk.registry import StrategyRegistry
from app.strategy_drift.models import StrategyDriftReport
from app.strategy_drift.repository import StrategyDriftRepository
from app.universes.models import HistoricalUniverse
from app.universes.repository import UniverseRepository
from app.walk_forward.models import WalkForwardResearchRecord
from app.walk_forward.repository import WalkForwardRepository

from .models import (
    LineageEdge,
    LineageEdgeType,
    LineageNode,
    LineageNodeStatus,
    LineageNodeType,
    LineageScalar,
    ResearchLineageGraph,
)

NODE_TYPE_ORDER: tuple[LineageNodeType, ...] = (
    "DATASET",
    "UNIVERSE",
    "CORPORATE_ACTION_DATASET",
    "FACTOR",
    "FACTOR_RESEARCH",
    "FACTOR_RELATIONSHIP",
    "WALK_FORWARD",
    "PORTFOLIO_RESEARCH",
    "HYPOTHESIS",
    "STRATEGY",
    "RUN",
    "TRACE",
    "SNAPSHOT",
    "FORWARD_SESSION",
    "PAPER_SESSION",
    "DRIFT_REPORT",
)
_NODE_ORDER = {node_type: index for index, node_type in enumerate(NODE_TYPE_ORDER)}


def _canonical_fingerprint(value: BaseModel) -> str:
    payload = json.dumps(value.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def _edge_id(
    edge_type: LineageEdgeType,
    source_node_id: str,
    target_node_id: str,
    source_field: str,
) -> str:
    semantic = "\x1f".join((edge_type, source_node_id, target_node_id, source_field))
    return f"EDGE:{hashlib.sha256(semantic.encode()).hexdigest()[:24]}"


def _factor_revision(factor: FactorDefinition) -> str:
    return factor.source_fingerprint or factor.version


def _dataset_node_id(dataset_id: str, revision: str) -> str:
    return f"DATASET:{dataset_id}:{revision}"


def _universe_node_id(universe_id: str) -> str:
    return f"UNIVERSE:{universe_id}"


def _corporate_action_node_id(corporate_action_dataset_id: str) -> str:
    return f"CORPORATE_ACTION_DATASET:{corporate_action_dataset_id}"


def _factor_node_id(factor_id: str, revision: str) -> str:
    return f"FACTOR:{factor_id}:{revision}"


def _factor_research_node_id(research_id: str) -> str:
    return f"FACTOR_RESEARCH:{research_id}"


def _relationship_node_id(relationship_id: str, revision: str) -> str:
    return f"FACTOR_RELATIONSHIP:{relationship_id}:{revision}"


def _walk_forward_node_id(walk_forward_id: str, revision: str) -> str:
    return f"WALK_FORWARD:{walk_forward_id}:{revision}"


def _portfolio_node_id(portfolio_id: str, revision: str) -> str:
    return f"PORTFOLIO_RESEARCH:{portfolio_id}:{revision}"


def _hypothesis_node_id(hypothesis_id: str, revision: int) -> str:
    return f"HYPOTHESIS:{hypothesis_id}:r{revision}"


def _strategy_node_id(strategy_id: str, revision: str) -> str:
    return f"STRATEGY:{strategy_id}:{revision}"


def _run_node_id(run_id: str) -> str:
    return f"RUN:{run_id}"


def _trace_node_id(trace_id: str) -> str:
    return f"TRACE:{trace_id}"


def _snapshot_node_id(snapshot_id: str) -> str:
    return f"SNAPSHOT:{snapshot_id}"


def _forward_session_node_id(session_id: str) -> str:
    return f"FORWARD_SESSION:{session_id}"


def _paper_session_node_id(session_id: str) -> str:
    return f"PAPER_SESSION:{session_id}"


def _drift_report_node_id(report_id: str) -> str:
    return f"DRIFT_REPORT:{report_id}"


def _route(path: str, artifact_id: str, query_name: str | None = None) -> str:
    encoded = quote(artifact_id, safe="")
    return f"{path}/{encoded}" if query_name is None else f"{path}?{query_name}={encoded}"


def _payload(artifact: FrozenArtifact) -> dict[str, object]:
    value = json.loads(artifact.payload_json)
    return value if isinstance(value, dict) else {}


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _integer(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _created_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, LineageNode] = {}
        self.edges: dict[str, LineageEdge] = {}

    def add_node(self, node: LineageNode) -> str:
        existing = self.nodes.get(node.node_id)
        if existing is None:
            self.nodes[node.node_id] = node
            return node.node_id
        prefer_new = existing.status != "RESOLVED" and node.status == "RESOLVED"
        base = node if prefer_new else existing
        additional = existing if prefer_new else node
        metadata = dict(base.metadata)
        for key, value in additional.metadata.items():
            metadata.setdefault(key, value)
        self.nodes[node.node_id] = base.model_copy(update={"metadata": metadata})
        return node.node_id

    def resolve(self, node_id: str) -> None:
        node = self.nodes[node_id]
        if node.status == "ORPHAN":
            self.nodes[node_id] = node.model_copy(update={"status": "RESOLVED"})

    def update_metadata(self, node_id: str, **values: LineageScalar) -> None:
        node = self.nodes[node_id]
        self.nodes[node_id] = node.model_copy(update={"metadata": {**node.metadata, **values}})

    def add_edge(
        self,
        edge_type: LineageEdgeType,
        source_node_id: str,
        target_node_id: str,
        source_field: str,
        *,
        resolve: bool = True,
    ) -> None:
        edge = LineageEdge(
            edge_id=_edge_id(edge_type, source_node_id, target_node_id, source_field),
            edge_type=edge_type,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            source_field=source_field,
        )
        self.edges[edge.edge_id] = edge
        if resolve:
            self.resolve(source_node_id)
            self.resolve(target_node_id)

    def graph(self) -> ResearchLineageGraph:
        nodes = tuple(
            sorted(
                self.nodes.values(),
                key=lambda item: (_NODE_ORDER[item.node_type], item.node_id),
            )
        )
        node_rank = {item.node_id: index for index, item in enumerate(nodes)}
        edges = tuple(
            sorted(
                self.edges.values(),
                key=lambda item: (
                    node_rank[item.source_node_id],
                    node_rank[item.target_node_id],
                    item.edge_type,
                    item.edge_id,
                ),
            )
        )
        return ResearchLineageGraph(nodes=nodes, edges=edges)


class ResearchLineageBuilder:
    def __init__(
        self,
        datasets: DatasetRegistry,
        factors: FactorResearchRepository,
        relationships: FactorRelationshipRepository,
        walk_forward: WalkForwardRepository,
        portfolios: PortfolioResearchRepository,
        hypotheses: HypothesisRepository,
        strategies: StrategyRegistry,
        runs: RunRepository,
        snapshots: ResearchSnapshotRepository,
        integrity: ResearchIntegrityEngine | None = None,
        universes: UniverseRepository | None = None,
        corporate_actions: CorporateActionRepository | None = None,
        drift_reports: StrategyDriftRepository | None = None,
    ) -> None:
        self.datasets = datasets
        self.factors = factors
        self.relationships = relationships
        self.walk_forward = walk_forward
        self.portfolios = portfolios
        self.hypotheses = hypotheses
        self.strategies = strategies
        self.runs = runs
        self.snapshots = snapshots
        self.integrity = integrity
        self.universes = universes or UniverseRepository(datasets.workspace_root)
        self.corporate_actions = corporate_actions or CorporateActionRepository(
            datasets.workspace_root
        )
        self.drift_reports = drift_reports or StrategyDriftRepository(datasets.workspace_root)
        self._graph = _Graph()
        self._factor_records: dict[str, FactorResearchRecord] = {}
        self._relationship_nodes: dict[str, str] = {}
        self._walk_forward_nodes: dict[str, str] = {}
        self._portfolio_nodes: dict[str, str] = {}
        self._run_manifests: dict[str, RunManifest] = {}

    def _universe_node(self, universe_id: str) -> str:
        node_id = _universe_node_id(universe_id)
        universe = self.universes.get(universe_id)
        if universe is None:
            return self._missing_node(
                "UNIVERSE",
                universe_id,
                node_id,
                f"Missing Historical Universe · {universe_id}",
                _route("/data", universe_id, "universe_id"),
            )
        return self._add_universe(universe)

    def _add_universe(self, universe: HistoricalUniverse) -> str:
        return self._graph.add_node(
            LineageNode(
                node_id=_universe_node_id(universe.universe_id),
                node_type="UNIVERSE",
                artifact_id=universe.universe_id,
                revision=_canonical_fingerprint(universe),
                label=universe.name,
                created_at=universe.created_at,
                status="ORPHAN",
                route=_route("/data", universe.universe_id, "universe_id"),
                metadata={
                    "mode": universe.mode,
                    "source": universe.source,
                    "snapshot_count": len(universe.snapshots),
                    "survivorship_bias_free": universe.survivorship_bias_free,
                },
            )
        )

    def _corporate_action_node(self, corporate_action_dataset_id: str) -> str:
        node_id = _corporate_action_node_id(corporate_action_dataset_id)
        dataset = self.corporate_actions.get(corporate_action_dataset_id)
        if dataset is None:
            return self._missing_node(
                "CORPORATE_ACTION_DATASET",
                corporate_action_dataset_id,
                node_id,
                f"Missing Corporate Action Dataset · {corporate_action_dataset_id}",
                _route(
                    "/data",
                    corporate_action_dataset_id,
                    "corporate_action_dataset_id",
                ),
            )
        return self._add_corporate_action_dataset(dataset)

    def _add_corporate_action_dataset(self, dataset: CorporateActionDataset) -> str:
        return self._graph.add_node(
            LineageNode(
                node_id=_corporate_action_node_id(dataset.corporate_action_dataset_id),
                node_type="CORPORATE_ACTION_DATASET",
                artifact_id=dataset.corporate_action_dataset_id,
                revision=dataset.content_fingerprint,
                label=dataset.name,
                created_at=dataset.retrieved_at,
                status="ORPHAN",
                route=_route(
                    "/data",
                    dataset.corporate_action_dataset_id,
                    "corporate_action_dataset_id",
                ),
                metadata={
                    "provider": dataset.provider,
                    "symbols": len(dataset.symbols),
                    "action_count": len(dataset.actions),
                    "point_in_time_safe": dataset.point_in_time_safe,
                },
            )
        )

    def _missing_node(
        self,
        node_type: LineageNodeType,
        artifact_id: str,
        node_id: str,
        label: str,
        route: str | None,
        *,
        revision: str | int | None = None,
    ) -> str:
        return self._graph.add_node(
            LineageNode(
                node_id=node_id,
                node_type=node_type,
                artifact_id=artifact_id,
                revision=revision,
                label=label,
                created_at=None,
                status="MISSING_SOURCE",
                route=route,
                metadata={"missing_source": True},
            )
        )

    def _dataset_node(self, dataset_id: str, revision: str) -> str:
        node_id = _dataset_node_id(dataset_id, revision)
        dataset = self.datasets.get(dataset_id)
        if dataset is None or dataset.content_fingerprint != revision:
            return self._missing_node(
                "DATASET",
                dataset_id,
                node_id,
                f"Missing Dataset · {dataset_id}",
                _route("/data", dataset_id, "dataset_id"),
                revision=revision,
            )
        return self._graph.add_node(
            LineageNode(
                node_id=node_id,
                node_type="DATASET",
                artifact_id=dataset_id,
                revision=revision,
                label=dataset.name,
                created_at=dataset.created_at,
                status="ORPHAN",
                route=_route("/data", dataset_id, "dataset_id"),
                metadata={
                    "source_type": dataset.source_type,
                    "symbols": len(dataset.symbols),
                    "frequency": dataset.frequency,
                    "family_id": dataset.dataset_family_id,
                    "revision": dataset.revision,
                },
            )
        )

    def _factor_research_node(self, research_id: str) -> str:
        node_id = _factor_research_node_id(research_id)
        record = self._factor_records.get(research_id) or self.factors.get(research_id)
        if record is None:
            return self._missing_node(
                "FACTOR_RESEARCH",
                research_id,
                node_id,
                f"Missing Factor Research · {research_id}",
                _route("/factor-lab", research_id, "research_id"),
            )
        self._factor_records[research_id] = record
        revision = _factor_revision(record.factor)
        return self._graph.add_node(
            LineageNode(
                node_id=node_id,
                node_type="FACTOR_RESEARCH",
                artifact_id=record.research_id,
                revision=revision,
                label=record.name,
                created_at=record.created_at,
                status="ORPHAN",
                route=_route("/factor-lab", record.research_id, "research_id"),
                metadata={
                    "factor_id": record.factor.factor_id,
                    "dataset_id": record.dataset_id,
                    "revealed_stage": record.revealed_stage,
                },
            )
        )

    def _add_factor_research(self, record: FactorResearchRecord) -> None:
        self._factor_records[record.research_id] = record
        research_node = self._factor_research_node(record.research_id)
        dataset_node = self._dataset_node(record.dataset_id, record.dataset_revision)
        factor_revision = _factor_revision(record.factor)
        factor_node = self._graph.add_node(
            LineageNode(
                node_id=_factor_node_id(record.factor.factor_id, factor_revision),
                node_type="FACTOR",
                artifact_id=record.factor.factor_id,
                revision=factor_revision,
                label=record.factor.name,
                created_at=None,
                status="ORPHAN",
                route=_route("/factor-lab", record.research_id, "research_id"),
                metadata={
                    "origin": record.factor.origin,
                    "category": record.factor.category,
                    "data_source": record.factor.data_source,
                },
            )
        )
        self._graph.add_edge(
            "USES_DATASET",
            dataset_node,
            research_node,
            "FactorResearchRecord.dataset_id",
        )
        self._graph.add_edge(
            "RESEARCHES_FACTOR",
            factor_node,
            research_node,
            "FactorResearchRecord.factor",
        )
        if record.universe_id is not None:
            self._graph.add_edge(
                "USES_UNIVERSE",
                self._universe_node(record.universe_id),
                research_node,
                "FactorResearchRecord.universe_id",
            )
        if record.corporate_action_dataset_id is not None:
            self._graph.add_edge(
                "USES_CORPORATE_ACTIONS",
                self._corporate_action_node(record.corporate_action_dataset_id),
                research_node,
                "FactorResearchRecord.corporate_action_dataset_id",
            )

    def _add_relationship(self, record: FactorRelationshipRecord) -> None:
        revision = _canonical_fingerprint(record)
        node_id = self._graph.add_node(
            LineageNode(
                node_id=_relationship_node_id(record.relationship_id, revision),
                node_type="FACTOR_RELATIONSHIP",
                artifact_id=record.relationship_id,
                revision=revision,
                label=record.name,
                created_at=record.created_at,
                status="ORPHAN",
                route=_route("/factor-relationships", record.relationship_id, "relationship_id"),
                metadata={
                    "dataset_id": record.dataset_id,
                    "stage": record.stage,
                    "factor_count": len(record.factor_research_ids),
                },
            )
        )
        self._relationship_nodes[record.relationship_id] = node_id
        for research_id in record.factor_research_ids:
            self._graph.add_edge(
                "RELATES_FACTORS",
                self._factor_research_node(research_id),
                node_id,
                "FactorRelationshipRecord.factor_research_ids",
            )

    def _add_walk_forward(self, record: WalkForwardResearchRecord) -> None:
        node_id = self._graph.add_node(
            LineageNode(
                node_id=_walk_forward_node_id(record.walk_forward_id, record.factor_revision),
                node_type="WALK_FORWARD",
                artifact_id=record.walk_forward_id,
                revision=record.factor_revision,
                label=record.name,
                created_at=record.created_at,
                status="ORPHAN",
                route=_route("/walk-forward", record.walk_forward_id, "walk_forward_id"),
                metadata={
                    "dataset_id": record.dataset_id,
                    "factor_id": record.factor_id,
                    "window_count": len(record.windows),
                },
            )
        )
        self._walk_forward_nodes[record.walk_forward_id] = node_id
        self._graph.add_edge(
            "VALIDATES_FACTOR",
            self._factor_research_node(record.factor_research_id),
            node_id,
            "WalkForwardResearchRecord.factor_research_id",
        )

    def _add_portfolio(self, record: PortfolioResearchRecord) -> None:
        revision = _canonical_fingerprint(record)
        node_id = self._graph.add_node(
            LineageNode(
                node_id=_portfolio_node_id(record.portfolio_research_id, revision),
                node_type="PORTFOLIO_RESEARCH",
                artifact_id=record.portfolio_research_id,
                revision=revision,
                label=record.name,
                created_at=record.created_at,
                status="ORPHAN",
                route=_route(
                    "/portfolio-lab", record.portfolio_research_id, "portfolio_research_id"
                ),
                metadata={
                    "dataset_id": record.dataset_id,
                    "factor_count": len(record.factor_refs),
                    "combination": record.combination,
                    "revealed_stage": record.revealed_stage,
                },
            )
        )
        self._portfolio_nodes[record.portfolio_research_id] = node_id
        for factor_ref in record.factor_refs:
            self._graph.add_edge(
                "COMBINES_FACTORS",
                self._factor_research_node(factor_ref.research_id),
                node_id,
                "PortfolioResearchRecord.factor_refs",
            )

    def _strategy_node(self, strategy_id: str, revision: str | None = None) -> str:
        registration = self.strategies.get_registration(strategy_id)
        resolved_revision = revision or (
            None if registration is None else registration.source_fingerprint
        )
        if resolved_revision is None:
            return self._missing_node(
                "STRATEGY",
                strategy_id,
                _strategy_node_id(strategy_id, "MISSING"),
                f"Missing Strategy · {strategy_id}",
                _route("/strategy", strategy_id, "strategy_id"),
            )
        return self._graph.add_node(
            LineageNode(
                node_id=_strategy_node_id(strategy_id, resolved_revision),
                node_type="STRATEGY",
                artifact_id=strategy_id,
                revision=resolved_revision,
                label=strategy_id,
                created_at=None if registration is None else registration.registered_at,
                status="ORPHAN",
                route=_route("/strategy", strategy_id, "strategy_id"),
                metadata={
                    "runtime_kind": None if registration is None else registration.runtime_kind,
                },
            )
        )

    def _add_registered_strategies(self) -> None:
        for registration in self.strategies.list():
            self._strategy_node(registration.strategy_id, registration.source_fingerprint)

    def _add_runs(self) -> None:
        listing = self.runs.list_runs(limit=10_000)
        for item in listing.items:
            run_node = self._graph.add_node(
                LineageNode(
                    node_id=_run_node_id(item.run_id),
                    node_type="RUN",
                    artifact_id=item.run_id,
                    revision=item.run_fingerprint,
                    label=item.annotations.display_name or item.run_id,
                    created_at=item.created_at,
                    status="ORPHAN",
                    route=_route("/runs", item.run_id),
                    metadata={
                        "run_status": item.status,
                        "run_type": item.run_type,
                        "strategy_id": item.strategy_id,
                        "strategy_revision": item.strategy_fingerprint,
                        "dataset_id": item.dataset_id,
                    },
                )
            )
            try:
                manifest = self.runs.get_manifest(item.run_id)
            except (RunNotFoundError, ArtifactIntegrityError):
                self._graph.update_metadata(run_node, artifact_error=True)
                continue
            self._run_manifests[item.run_id] = manifest
            if manifest.trace_id is None:
                continue
            trace_status: LineageNodeStatus = (
                "RESOLVED" if manifest.artifacts.trace_sha256 is not None else "MISSING_SOURCE"
            )
            trace_node = self._graph.add_node(
                LineageNode(
                    node_id=_trace_node_id(manifest.trace_id),
                    node_type="TRACE",
                    artifact_id=manifest.trace_id,
                    revision=manifest.artifacts.trace_sha256,
                    label=manifest.trace_id,
                    created_at=manifest.completed_at or manifest.created_at,
                    status="ORPHAN" if trace_status == "RESOLVED" else trace_status,
                    route=_route("/replay", manifest.trace_id, "trace_id"),
                    metadata={"run_id": manifest.run_id},
                )
            )
            self._graph.add_edge(
                "PRODUCES_TRACE",
                run_node,
                trace_node,
                "RunManifest.trace_id",
                resolve=False,
            )

    def _hypothesis_strategy_revision(self, record: ResearchHypothesis) -> str | None:
        strategy_id = record.lineage.strategy_id
        if strategy_id is None:
            return None
        portfolio_id = record.lineage.portfolio_research_id
        portfolio = None if portfolio_id is None else self.portfolios.get(portfolio_id)
        if (
            portfolio is not None
            and portfolio.strategy is not None
            and portfolio.strategy.strategy_id == strategy_id
        ):
            return portfolio.strategy.source_fingerprint
        registration = self.strategies.get_registration(strategy_id)
        if registration is not None:
            return registration.source_fingerprint
        for run_id in record.lineage.run_ids:
            manifest = self._run_manifests.get(run_id)
            if manifest is not None and manifest.strategy.strategy_id == strategy_id:
                return manifest.strategy.source_fingerprint
        return None

    def _relationship_node(self, relationship_id: str) -> str:
        node_id = self._relationship_nodes.get(relationship_id)
        if node_id is not None:
            return node_id
        return self._missing_node(
            "FACTOR_RELATIONSHIP",
            relationship_id,
            _relationship_node_id(relationship_id, "MISSING"),
            f"Missing Factor Relationship · {relationship_id}",
            _route("/factor-relationships", relationship_id, "relationship_id"),
        )

    def _walk_node(self, walk_forward_id: str) -> str:
        node_id = self._walk_forward_nodes.get(walk_forward_id)
        if node_id is not None:
            return node_id
        return self._missing_node(
            "WALK_FORWARD",
            walk_forward_id,
            _walk_forward_node_id(walk_forward_id, "MISSING"),
            f"Missing Walk-Forward · {walk_forward_id}",
            _route("/walk-forward", walk_forward_id, "walk_forward_id"),
        )

    def _portfolio_node(self, portfolio_id: str) -> str:
        node_id = self._portfolio_nodes.get(portfolio_id)
        if node_id is not None:
            return node_id
        return self._missing_node(
            "PORTFOLIO_RESEARCH",
            portfolio_id,
            _portfolio_node_id(portfolio_id, "MISSING"),
            f"Missing Portfolio Research · {portfolio_id}",
            _route("/portfolio-lab", portfolio_id, "portfolio_research_id"),
        )

    def _run_node(self, run_id: str) -> str:
        node_id = _run_node_id(run_id)
        if node_id in self._graph.nodes:
            return node_id
        return self._missing_node(
            "RUN",
            run_id,
            node_id,
            f"Missing Run · {run_id}",
            _route("/runs", run_id),
        )

    def _add_hypothesis(self, record: ResearchHypothesis) -> None:
        metadata: dict[str, LineageScalar] = {
            "family_id": record.family_id,
            "lifecycle_status": record.status,
            "outcome": record.outcome,
            "dataset_id": record.dataset_id,
        }
        if self.integrity is not None:
            report = self.integrity.audit(record.hypothesis_id)
            metadata.update(
                integrity_status=report.overall_status,
                integrity_violations=report.violation_count,
                integrity_warnings=report.warning_count,
            )
        hypothesis_node = self._graph.add_node(
            LineageNode(
                node_id=_hypothesis_node_id(record.hypothesis_id, record.revision),
                node_type="HYPOTHESIS",
                artifact_id=record.hypothesis_id,
                revision=record.revision,
                label=record.title,
                created_at=record.created_at,
                status="ORPHAN",
                route=_route("/research-workspace", record.hypothesis_id),
                metadata=metadata,
            )
        )
        for research_id in record.factor_research_ids:
            self._graph.add_edge(
                "SUPPORTS_HYPOTHESIS",
                self._factor_research_node(research_id),
                hypothesis_node,
                "ResearchHypothesis.factor_research_ids",
            )
        for relationship_id in record.lineage.relationship_ids:
            self._graph.add_edge(
                "SUPPORTS_HYPOTHESIS",
                self._relationship_node(relationship_id),
                hypothesis_node,
                "ResearchHypothesis.lineage.relationship_ids",
            )
        for walk_forward_id in record.lineage.walk_forward_ids:
            self._graph.add_edge(
                "SUPPORTS_HYPOTHESIS",
                self._walk_node(walk_forward_id),
                hypothesis_node,
                "ResearchHypothesis.lineage.walk_forward_ids",
            )
        if record.lineage.portfolio_research_id is not None:
            self._graph.add_edge(
                "USES_PORTFOLIO",
                self._portfolio_node(record.lineage.portfolio_research_id),
                hypothesis_node,
                "ResearchHypothesis.lineage.portfolio_research_id",
            )
        if record.lineage.strategy_id is None:
            return
        strategy_node = self._strategy_node(
            record.lineage.strategy_id,
            self._hypothesis_strategy_revision(record),
        )
        self._graph.add_edge(
            "GENERATES_STRATEGY",
            hypothesis_node,
            strategy_node,
            "ResearchHypothesis.lineage.strategy_id",
        )
        for run_id in record.lineage.run_ids:
            run_node = self._run_node(run_id)
            manifest = self._run_manifests.get(run_id)
            mismatch = (
                manifest is not None and manifest.strategy.strategy_id != record.lineage.strategy_id
            )
            if mismatch:
                self._graph.update_metadata(
                    run_node,
                    integrity_mismatch=True,
                    expected_strategy_id=record.lineage.strategy_id,
                )
            self._graph.add_edge(
                "EXECUTES_STRATEGY",
                strategy_node,
                run_node,
                "ResearchHypothesis.lineage.run_ids",
            )
            if manifest is not None and manifest.trace_id is not None:
                trace_id = _trace_node_id(manifest.trace_id)
                if trace_id in self._graph.nodes:
                    self._graph.resolve(trace_id)

    def _frozen_dataset(self, artifact: FrozenArtifact, snapshot_id: str) -> str:
        payload = _payload(artifact)
        return self._graph.add_node(
            LineageNode(
                node_id=_dataset_node_id(artifact.artifact_id, artifact.source_revision),
                node_type="DATASET",
                artifact_id=artifact.artifact_id,
                revision=artifact.source_revision,
                label=_text(payload.get("name"), artifact.artifact_id),
                created_at=_created_at(payload.get("created_at")),
                status="ORPHAN",
                route=_route("/data", artifact.artifact_id, "dataset_id"),
                metadata={"frozen_in_snapshot": snapshot_id},
            )
        )

    def _frozen_factor_research(
        self, artifact: FrozenArtifact, snapshot_id: str
    ) -> tuple[str, str, dict[str, object]]:
        payload = _payload(artifact)
        research_node = self._graph.add_node(
            LineageNode(
                node_id=_factor_research_node_id(artifact.artifact_id),
                node_type="FACTOR_RESEARCH",
                artifact_id=artifact.artifact_id,
                revision=artifact.source_revision,
                label=_text(payload.get("name"), artifact.artifact_id),
                created_at=_created_at(payload.get("created_at")),
                status="ORPHAN",
                route=_route("/factor-lab", artifact.artifact_id, "research_id"),
                metadata={"frozen_in_snapshot": snapshot_id},
            )
        )
        factor = payload.get("factor")
        factor_payload = factor if isinstance(factor, dict) else {}
        factor_id = _text(factor_payload.get("factor_id"), "UNKNOWN")
        factor_revision = _text(factor_payload.get("source_fingerprint")) or _text(
            factor_payload.get("version"), artifact.source_revision
        )
        factor_node = self._graph.add_node(
            LineageNode(
                node_id=_factor_node_id(factor_id, factor_revision),
                node_type="FACTOR",
                artifact_id=factor_id,
                revision=factor_revision,
                label=_text(factor_payload.get("name"), factor_id),
                created_at=None,
                status="ORPHAN",
                route=_route("/factor-lab", artifact.artifact_id, "research_id"),
                metadata={"frozen_in_snapshot": snapshot_id},
            )
        )
        return research_node, factor_node, payload

    def _add_snapshot(self, snapshot: ResearchSnapshot) -> None:
        snapshot_node = self._graph.add_node(
            LineageNode(
                node_id=_snapshot_node_id(snapshot.snapshot_id),
                node_type="SNAPSHOT",
                artifact_id=snapshot.snapshot_id,
                revision=snapshot.content_fingerprint,
                label=snapshot.name,
                created_at=snapshot.created_at,
                status="ORPHAN",
                route=_route("/research-snapshots", snapshot.snapshot_id, "snapshot_id"),
                metadata={
                    "hypothesis_id": snapshot.lineage.hypothesis_id,
                    "hypothesis_revision": snapshot.lineage.hypothesis_revision,
                },
            )
        )
        dataset_node = self._frozen_dataset(snapshot.dataset, snapshot.snapshot_id)
        frozen_universe_nodes: dict[str, str] = {}
        for artifact in snapshot.universes:
            payload = _payload(artifact)
            frozen_universe_nodes[artifact.artifact_id] = self._graph.add_node(
                LineageNode(
                    node_id=_universe_node_id(artifact.artifact_id),
                    node_type="UNIVERSE",
                    artifact_id=artifact.artifact_id,
                    revision=artifact.source_revision,
                    label=_text(payload.get("name"), artifact.artifact_id),
                    created_at=_created_at(payload.get("created_at")),
                    status="ORPHAN",
                    route=_route("/data", artifact.artifact_id, "universe_id"),
                    metadata={"frozen_in_snapshot": snapshot.snapshot_id},
                )
            )
        frozen_corporate_action_nodes: dict[str, str] = {}
        for artifact in snapshot.corporate_actions:
            payload = _payload(artifact)
            frozen_corporate_action_nodes[artifact.artifact_id] = self._graph.add_node(
                LineageNode(
                    node_id=_corporate_action_node_id(artifact.artifact_id),
                    node_type="CORPORATE_ACTION_DATASET",
                    artifact_id=artifact.artifact_id,
                    revision=artifact.source_revision,
                    label=_text(payload.get("name"), artifact.artifact_id),
                    created_at=_created_at(payload.get("retrieved_at")),
                    status="ORPHAN",
                    route=_route(
                        "/data",
                        artifact.artifact_id,
                        "corporate_action_dataset_id",
                    ),
                    metadata={"frozen_in_snapshot": snapshot.snapshot_id},
                )
            )
        frozen_factor_nodes: dict[str, str] = {}
        for artifact in snapshot.factors:
            research_node, factor_node, payload = self._frozen_factor_research(
                artifact, snapshot.snapshot_id
            )
            frozen_factor_nodes[artifact.artifact_id] = research_node
            dataset_id = _text(payload.get("dataset_id"), snapshot.lineage.dataset_id)
            dataset_revision = _text(
                payload.get("dataset_revision"), snapshot.dataset.source_revision
            )
            source_dataset = (
                dataset_node
                if dataset_id == snapshot.dataset.artifact_id
                and dataset_revision == snapshot.dataset.source_revision
                else self._dataset_node(dataset_id, dataset_revision)
            )
            self._graph.add_edge(
                "USES_DATASET",
                source_dataset,
                research_node,
                "Frozen FactorResearchRecord.dataset_id",
            )
            self._graph.add_edge(
                "RESEARCHES_FACTOR",
                factor_node,
                research_node,
                "Frozen FactorResearchRecord.factor",
            )
            universe_id = _text(payload.get("universe_id"))
            if universe_id:
                self._graph.add_edge(
                    "USES_UNIVERSE",
                    frozen_universe_nodes.get(universe_id, self._universe_node(universe_id)),
                    research_node,
                    "Frozen FactorResearchRecord.universe_id",
                )
            corporate_action_dataset_id = _text(payload.get("corporate_action_dataset_id"))
            if corporate_action_dataset_id:
                self._graph.add_edge(
                    "USES_CORPORATE_ACTIONS",
                    frozen_corporate_action_nodes.get(
                        corporate_action_dataset_id,
                        self._corporate_action_node(corporate_action_dataset_id),
                    ),
                    research_node,
                    "Frozen FactorResearchRecord.corporate_action_dataset_id",
                )

        frozen_relationship_nodes: dict[str, str] = {}
        for artifact in snapshot.relationships:
            payload = _payload(artifact)
            node_id = self._graph.add_node(
                LineageNode(
                    node_id=_relationship_node_id(artifact.artifact_id, artifact.source_revision),
                    node_type="FACTOR_RELATIONSHIP",
                    artifact_id=artifact.artifact_id,
                    revision=artifact.source_revision,
                    label=_text(payload.get("name"), artifact.artifact_id),
                    created_at=_created_at(payload.get("created_at")),
                    status="ORPHAN",
                    route=_route("/factor-relationships", artifact.artifact_id, "relationship_id"),
                    metadata={"frozen_in_snapshot": snapshot.snapshot_id},
                )
            )
            frozen_relationship_nodes[artifact.artifact_id] = node_id
            ids = payload.get("factor_research_ids")
            for research_id in ids if isinstance(ids, list) else ():
                if isinstance(research_id, str):
                    self._graph.add_edge(
                        "RELATES_FACTORS",
                        frozen_factor_nodes.get(
                            research_id, self._factor_research_node(research_id)
                        ),
                        node_id,
                        "Frozen FactorRelationshipRecord.factor_research_ids",
                    )

        frozen_walk_nodes: dict[str, str] = {}
        for artifact in snapshot.walk_forward:
            payload = _payload(artifact)
            node_id = self._graph.add_node(
                LineageNode(
                    node_id=_walk_forward_node_id(artifact.artifact_id, artifact.source_revision),
                    node_type="WALK_FORWARD",
                    artifact_id=artifact.artifact_id,
                    revision=artifact.source_revision,
                    label=_text(payload.get("name"), artifact.artifact_id),
                    created_at=_created_at(payload.get("created_at")),
                    status="ORPHAN",
                    route=_route("/walk-forward", artifact.artifact_id, "walk_forward_id"),
                    metadata={"frozen_in_snapshot": snapshot.snapshot_id},
                )
            )
            frozen_walk_nodes[artifact.artifact_id] = node_id
            research_id = _text(payload.get("factor_research_id"))
            if research_id:
                self._graph.add_edge(
                    "VALIDATES_FACTOR",
                    frozen_factor_nodes.get(research_id, self._factor_research_node(research_id)),
                    node_id,
                    "Frozen WalkForwardResearchRecord.factor_research_id",
                )

        portfolio_payload = _payload(snapshot.portfolio)
        portfolio_node = self._graph.add_node(
            LineageNode(
                node_id=_portfolio_node_id(
                    snapshot.portfolio.artifact_id, snapshot.portfolio.source_revision
                ),
                node_type="PORTFOLIO_RESEARCH",
                artifact_id=snapshot.portfolio.artifact_id,
                revision=snapshot.portfolio.source_revision,
                label=_text(portfolio_payload.get("name"), snapshot.portfolio.artifact_id),
                created_at=_created_at(portfolio_payload.get("created_at")),
                status="ORPHAN",
                route=_route(
                    "/portfolio-lab",
                    snapshot.portfolio.artifact_id,
                    "portfolio_research_id",
                ),
                metadata={"frozen_in_snapshot": snapshot.snapshot_id},
            )
        )
        factor_refs = portfolio_payload.get("factor_refs")
        for factor_ref in factor_refs if isinstance(factor_refs, list) else ():
            if not isinstance(factor_ref, dict):
                continue
            research_id = _text(factor_ref.get("research_id"))
            if research_id:
                self._graph.add_edge(
                    "COMBINES_FACTORS",
                    frozen_factor_nodes.get(research_id, self._factor_research_node(research_id)),
                    portfolio_node,
                    "Frozen PortfolioResearchRecord.factor_refs",
                )

        hypothesis_payload = _payload(snapshot.hypothesis)
        hypothesis_node = self._graph.add_node(
            LineageNode(
                node_id=_hypothesis_node_id(
                    snapshot.lineage.hypothesis_id,
                    snapshot.lineage.hypothesis_revision,
                ),
                node_type="HYPOTHESIS",
                artifact_id=snapshot.lineage.hypothesis_id,
                revision=snapshot.lineage.hypothesis_revision,
                label=_text(hypothesis_payload.get("title"), snapshot.lineage.hypothesis_id),
                created_at=_created_at(hypothesis_payload.get("created_at")),
                status="ORPHAN",
                route=_route("/research-workspace", snapshot.lineage.hypothesis_id),
                metadata={
                    "frozen_in_snapshot": snapshot.snapshot_id,
                    "lifecycle_status": _text(hypothesis_payload.get("status")),
                },
            )
        )
        for research_id in snapshot.lineage.factor_research_ids:
            self._graph.add_edge(
                "SUPPORTS_HYPOTHESIS",
                frozen_factor_nodes.get(research_id, self._factor_research_node(research_id)),
                hypothesis_node,
                "ResearchSnapshot.lineage.factor_research_ids",
            )
        for relationship_id in snapshot.lineage.relationship_ids:
            self._graph.add_edge(
                "SUPPORTS_HYPOTHESIS",
                frozen_relationship_nodes.get(
                    relationship_id, self._relationship_node(relationship_id)
                ),
                hypothesis_node,
                "ResearchSnapshot.lineage.relationship_ids",
            )
        for walk_forward_id in snapshot.lineage.walk_forward_ids:
            self._graph.add_edge(
                "SUPPORTS_HYPOTHESIS",
                frozen_walk_nodes.get(walk_forward_id, self._walk_node(walk_forward_id)),
                hypothesis_node,
                "ResearchSnapshot.lineage.walk_forward_ids",
            )
        self._graph.add_edge(
            "USES_PORTFOLIO",
            portfolio_node,
            hypothesis_node,
            "ResearchSnapshot.lineage.portfolio_research_id",
        )

        strategy_payload = _payload(snapshot.strategy)
        strategy_node = self._graph.add_node(
            LineageNode(
                node_id=_strategy_node_id(
                    snapshot.strategy.artifact_id, snapshot.strategy.source_revision
                ),
                node_type="STRATEGY",
                artifact_id=snapshot.strategy.artifact_id,
                revision=snapshot.strategy.source_revision,
                label=snapshot.strategy.artifact_id,
                created_at=None,
                status="ORPHAN",
                route=_route("/strategy", snapshot.strategy.artifact_id, "strategy_id"),
                metadata={
                    "frozen_in_snapshot": snapshot.snapshot_id,
                    "class_name": _text(strategy_payload.get("class_name")),
                },
            )
        )
        self._graph.add_edge(
            "GENERATES_STRATEGY",
            hypothesis_node,
            strategy_node,
            "ResearchSnapshot.lineage.strategy_id",
        )

        run_artifacts = {item.artifact_id: item for item in snapshot.runs}
        trace_artifacts = {item.artifact_id: item for item in snapshot.traces}
        for run_id in snapshot.lineage.run_ids:
            frozen_run = run_artifacts.get(run_id)
            if frozen_run is None:
                manifest: RunManifest | None = None
                run_node = self._run_node(run_id)
            else:
                manifest = RunManifest.model_validate_json(frozen_run.payload_json)
                run_node = self._graph.add_node(
                    LineageNode(
                        node_id=_run_node_id(run_id),
                        node_type="RUN",
                        artifact_id=run_id,
                        revision=frozen_run.source_revision,
                        label=run_id,
                        created_at=manifest.created_at,
                        status="ORPHAN",
                        route=_route("/runs", run_id),
                        metadata={
                            "frozen_in_snapshot": snapshot.snapshot_id,
                            "strategy_id": manifest.strategy.strategy_id,
                            "run_status": manifest.status,
                        },
                    )
                )
            if (
                manifest is not None
                and manifest.strategy.strategy_id != snapshot.lineage.strategy_id
            ):
                self._graph.update_metadata(
                    run_node,
                    integrity_mismatch=True,
                    expected_strategy_id=snapshot.lineage.strategy_id,
                )
            self._graph.add_edge(
                "EXECUTES_STRATEGY",
                strategy_node,
                run_node,
                "ResearchSnapshot.lineage.run_ids",
            )
            if manifest is None or manifest.trace_id is None:
                continue
            trace_artifact = trace_artifacts.get(manifest.trace_id)
            trace_node = (
                self._missing_node(
                    "TRACE",
                    manifest.trace_id,
                    _trace_node_id(manifest.trace_id),
                    f"Missing Trace · {manifest.trace_id}",
                    _route("/replay", manifest.trace_id, "trace_id"),
                )
                if trace_artifact is None
                else self._graph.add_node(
                    LineageNode(
                        node_id=_trace_node_id(manifest.trace_id),
                        node_type="TRACE",
                        artifact_id=manifest.trace_id,
                        revision=trace_artifact.source_revision,
                        label=manifest.trace_id,
                        created_at=manifest.completed_at or manifest.created_at,
                        status="ORPHAN",
                        route=_route("/replay", manifest.trace_id, "trace_id"),
                        metadata={
                            "frozen_in_snapshot": snapshot.snapshot_id,
                            "run_id": run_id,
                        },
                    )
                )
            )
            self._graph.add_edge(
                "PRODUCES_TRACE",
                run_node,
                trace_node,
                "Frozen RunManifest.trace_id",
            )
            self._graph.add_edge(
                "FREEZES_RESEARCH",
                trace_node,
                snapshot_node,
                "ResearchSnapshot.lineage.trace_ids",
            )
        self._graph.add_edge(
            "FREEZES_RESEARCH",
            hypothesis_node,
            snapshot_node,
            "ResearchSnapshot.lineage.hypothesis_id",
        )

    def _records(self) -> Iterable[FactorResearchRecord]:
        for summary in self.factors.list():
            record = self.factors.get(summary.research_id)
            if record is not None:
                yield record

    def _drift_source_node(self, report: StrategyDriftReport, *, baseline: bool) -> str:
        source = report.baseline if baseline else report.observed
        if source.source_type in {"RUN", "PAPER_RUN"}:
            return self._run_node(source.resolved_run_id or source.source_id)
        if source.source_type == "SNAPSHOT":
            node_id = _snapshot_node_id(source.source_id)
            if node_id in self._graph.nodes:
                return node_id
            return self._missing_node(
                "SNAPSHOT",
                source.source_id,
                node_id,
                f"Missing Research Snapshot · {source.source_id}",
                _route("/research-snapshots", source.source_id, "snapshot_id"),
            )
        source_node_id = (
            _paper_session_node_id(source.source_id)
            if source.source_type == "PAPER_SESSION"
            else _forward_session_node_id(source.source_id)
        )
        source_node_type: LineageNodeType = (
            "PAPER_SESSION" if source.source_type == "PAPER_SESSION" else "FORWARD_SESSION"
        )
        source_route = (
            _route("/paper", source.source_id, "session_id")
            if source.source_type == "PAPER_SESSION"
            else _route("/forward", source.source_id, "session_id")
        )
        return self._graph.add_node(
            LineageNode(
                node_id=source_node_id,
                node_type=source_node_type,
                artifact_id=source.source_id,
                revision=source.strategy_fingerprint,
                label=source.source_id,
                created_at=source.observed_until,
                status="RESOLVED",
                route=source_route,
                metadata={
                    "strategy_id": source.strategy_id,
                    "dataset_id": source.dataset_id,
                    "sample_size": source.sample_size,
                    "captured_in_report": report.drift_report_id,
                },
            )
        )

    def _add_drift_report(self, report: StrategyDriftReport) -> None:
        report_node = self._graph.add_node(
            LineageNode(
                node_id=_drift_report_node_id(report.drift_report_id),
                node_type="DRIFT_REPORT",
                artifact_id=report.drift_report_id,
                revision=report.drift_rule_version,
                label=f"Strategy Drift · {report.baseline_id} → {report.observed_id}",
                created_at=report.created_at,
                status="ORPHAN",
                route=_route("/strategy-drift", report.drift_report_id, "report_id"),
                metadata={
                    "overall_status": report.overall_status,
                    "comparability": report.comparability,
                    "first_drift_dimension": report.first_drift_dimension,
                    "first_drift_event_id": report.first_drift_event_id,
                },
            )
        )
        self._graph.add_edge(
            "BASELINE_FOR_DRIFT",
            self._drift_source_node(report, baseline=True),
            report_node,
            "StrategyDriftReport.baseline_id",
        )
        self._graph.add_edge(
            "OBSERVED_BY_DRIFT",
            self._drift_source_node(report, baseline=False),
            report_node,
            "StrategyDriftReport.observed_id",
        )

    def build(self) -> ResearchLineageGraph:
        self._graph = _Graph()
        self._factor_records = {}
        self._relationship_nodes = {}
        self._walk_forward_nodes = {}
        self._portfolio_nodes = {}
        self._run_manifests = {}
        for dataset in self.datasets.list():
            self._dataset_node(dataset.dataset_id, dataset.content_fingerprint)
        for universe in self.universes.list():
            self._add_universe(universe)
        for corporate_action_dataset in self.corporate_actions.list():
            self._add_corporate_action_dataset(corporate_action_dataset)
        for factor_record in self._records():
            self._add_factor_research(factor_record)
        for relationship_record in self.relationships.list():
            self._add_relationship(relationship_record)
        for walk_record in self.walk_forward.list():
            self._add_walk_forward(walk_record)
        for portfolio_summary in self.portfolios.list():
            portfolio_record = self.portfolios.get(portfolio_summary.portfolio_research_id)
            if portfolio_record is not None:
                self._add_portfolio(portfolio_record)
        self._add_registered_strategies()
        self._add_runs()
        for hypothesis_record in self.hypotheses.list():
            self._add_hypothesis(hypothesis_record)
        for snapshot_summary in self.snapshots.list():
            snapshot = self.snapshots.get(snapshot_summary.snapshot_id)
            if snapshot is not None:
                self._add_snapshot(snapshot)
        for drift_summary in self.drift_reports.list():
            drift_report = self.drift_reports.get(drift_summary.drift_report_id)
            if drift_report is not None:
                self._add_drift_report(drift_report)
        return self._graph.graph()
