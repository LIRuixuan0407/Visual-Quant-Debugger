from __future__ import annotations

from pathlib import Path

from test_phase23_discovery import _assets, _request

from app.discovery import CreateHypothesisRevision
from app.main import app
from app.research_lineage import (
    LineageEdge,
    LineageNode,
    ResearchLineageBuilder,
    ResearchLineageGraph,
    ResearchLineageService,
)
from app.research_snapshots import (
    CreateResearchSnapshot,
    ResearchSnapshotEngine,
    ResearchSnapshotRepository,
)
from app.runs import RunLedger, run_store


def _service(assets: tuple, snapshots: ResearchSnapshotRepository) -> ResearchLineageService:
    discovery, _, factors, hypotheses, _, datasets, strategies, _ = assets
    return ResearchLineageService(
        ResearchLineageBuilder(
            datasets,
            factors,
            discovery.relationships,
            discovery.walk_forward,
            discovery.portfolios,
            hypotheses,
            strategies,
            run_store.repository,
            snapshots,
        )
    )


def _node(
    graph: ResearchLineageGraph,
    node_type: str,
    artifact_id: str,
) -> LineageNode:
    return next(
        item
        for item in graph.nodes
        if item.node_type == node_type and item.artifact_id == artifact_id
    )


def _incoming(graph: ResearchLineageGraph, target: LineageNode) -> tuple[LineageEdge, ...]:
    return tuple(item for item in graph.edges if item.target_node_id == target.node_id)


def _complete_chain(tmp_path: Path):
    assets = _assets(tmp_path)
    (
        discovery,
        factor_engine,
        factors,
        hypotheses,
        ledger,
        datasets,
        strategies,
        research_ids,
    ) = assets
    hypothesis = discovery.build_candidate(discovery.create(_request(research_ids)))
    for research_id in research_ids:
        factor = factors.get(research_id)
        assert factor is not None
        factors.save(factor_engine.reveal(factor, "VALIDATION"))
    hypothesis = discovery.validate(hypothesis)
    for research_id in research_ids:
        factor = factors.get(research_id)
        assert factor is not None
        factors.save(factor_engine.reveal(factor, "HOLDOUT"))
    hypothesis = discovery.create_strategy(discovery.reveal_holdout(hypothesis))
    assert hypothesis.lineage.strategy_id is not None
    attached = RunLedger().create(
        strategy_id=hypothesis.lineage.strategy_id,
        dataset_id=hypothesis.dataset_id,
        parameters={},
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    assert attached.manifest.trace_id is not None
    hypothesis = discovery.attach_run(
        hypothesis,
        attached.manifest.run_id,
        attached.manifest.trace_id,
    )
    snapshots = ResearchSnapshotRepository(tmp_path)
    snapshot_engine = ResearchSnapshotEngine(
        datasets,
        factors,
        discovery.relationships,
        discovery.walk_forward,
        hypotheses,
        discovery.portfolios,
        strategies,
        run_store.repository,
        snapshots,
        ledger,
    )
    snapshot = snapshot_engine.create(
        CreateResearchSnapshot(name="Frozen revision one", hypothesis_id=hypothesis.hypothesis_id)
    )
    unattached = RunLedger().create(
        strategy_id=hypothesis.lineage.strategy_id,
        dataset_id=hypothesis.dataset_id,
        parameters={},
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    revision = discovery.create_revision(
        hypothesis,
        CreateHypothesisRevision(revision_reason="Keep the frozen first revision distinct."),
    )
    return assets, snapshots, hypothesis, revision, snapshot, attached, unattached


def test_research_lineage_api_is_registered() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/research-lineage" in paths
    assert "/api/research-lineage/summary" in paths


def test_builder_uses_only_explicit_factor_relationship_walk_and_portfolio_fields(
    tmp_path: Path,
) -> None:
    assets = _assets(tmp_path)
    discovery, _, factors, _, _, _, _, research_ids = assets
    hypothesis = discovery.build_candidate(discovery.create(_request(research_ids)))
    assert hypothesis.lineage.portfolio_research_id is not None
    service = _service(assets, ResearchSnapshotRepository(tmp_path))

    graph = service.graph()

    factor_record = factors.get(research_ids[0])
    assert factor_record is not None
    factor_research = _node(graph, "FACTOR_RESEARCH", factor_record.research_id)
    dataset_edge = next(
        edge for edge in _incoming(graph, factor_research) if edge.edge_type == "USES_DATASET"
    )
    assert dataset_edge.source_field == "FactorResearchRecord.dataset_id"
    factor_node = _node(graph, "FACTOR", factor_record.factor.factor_id)
    assert factor_node.revision == (
        factor_record.factor.source_fingerprint or factor_record.factor.version
    )

    relationship = discovery.relationships.list()[0]
    relationship_node = _node(graph, "FACTOR_RELATIONSHIP", relationship.relationship_id)
    relationship_sources = {
        graph_node.artifact_id
        for edge in _incoming(graph, relationship_node)
        if edge.edge_type == "RELATES_FACTORS"
        for graph_node in graph.nodes
        if graph_node.node_id == edge.source_node_id
    }
    assert relationship_sources == set(relationship.factor_research_ids)
    assert all(
        edge.source_field == "FactorRelationshipRecord.factor_research_ids"
        for edge in _incoming(graph, relationship_node)
    )

    walk = discovery.walk_forward.list()[0]
    walk_node = _node(graph, "WALK_FORWARD", walk.walk_forward_id)
    walk_sources = [
        graph_node.artifact_id
        for edge in _incoming(graph, walk_node)
        for graph_node in graph.nodes
        if graph_node.node_id == edge.source_node_id
    ]
    assert walk_sources == [walk.factor_research_id]
    assert _incoming(graph, walk_node)[0].source_field == (
        "WalkForwardResearchRecord.factor_research_id"
    )

    portfolio = discovery.portfolios.get(hypothesis.lineage.portfolio_research_id)
    assert portfolio is not None
    portfolio_node = _node(graph, "PORTFOLIO_RESEARCH", portfolio.portfolio_research_id)
    portfolio_sources = {
        graph_node.artifact_id
        for edge in _incoming(graph, portfolio_node)
        if edge.edge_type == "COMBINES_FACTORS"
        for graph_node in graph.nodes
        if graph_node.node_id == edge.source_node_id
    }
    assert portfolio_sources == {item.research_id for item in portfolio.factor_refs}
    assert all(
        edge.source_field == "PortfolioResearchRecord.factor_refs"
        for edge in _incoming(graph, portfolio_node)
    )

    hypothesis_node = _node(graph, "HYPOTHESIS", hypothesis.hypothesis_id)
    hypothesis_sources = {
        graph_node.artifact_id
        for edge in _incoming(graph, hypothesis_node)
        for graph_node in graph.nodes
        if graph_node.node_id == edge.source_node_id
    }
    assert hypothesis_sources == {
        *hypothesis.factor_research_ids,
        *hypothesis.lineage.relationship_ids,
        *hypothesis.lineage.walk_forward_ids,
        hypothesis.lineage.portfolio_research_id,
    }
    assert service.graph().model_dump() == graph.model_dump()


def test_missing_explicit_lineage_is_visible_without_linking_unreferenced_records(
    tmp_path: Path,
) -> None:
    assets = _assets(tmp_path)
    discovery, _, _, hypotheses, _, _, _, research_ids = assets
    hypothesis = discovery.create(_request(research_ids))
    unrelated = discovery.relationships.list()[0].model_copy(
        update={"relationship_id": "relationship-unreferenced"}
    )
    discovery.relationships.save(unrelated)
    hypothesis = hypotheses.save(
        hypothesis.model_copy(
            update={
                "lineage": hypothesis.lineage.model_copy(
                    update={
                        "relationship_ids": (
                            *hypothesis.lineage.relationship_ids,
                            "relationship-explicitly-missing",
                        )
                    }
                )
            }
        )
    )

    graph = _service(assets, ResearchSnapshotRepository(tmp_path)).graph()

    missing = _node(graph, "FACTOR_RELATIONSHIP", "relationship-explicitly-missing")
    target = _node(graph, "HYPOTHESIS", hypothesis.hypothesis_id)
    assert missing.status == "MISSING_SOURCE"
    assert any(
        edge.source_node_id == missing.node_id
        and edge.target_node_id == target.node_id
        and edge.source_field == "ResearchHypothesis.lineage.relationship_ids"
        for edge in graph.edges
    )
    unrelated_node = _node(graph, "FACTOR_RELATIONSHIP", unrelated.relationship_id)
    assert not any(
        edge.source_node_id == unrelated_node.node_id and edge.target_node_id == target.node_id
        for edge in graph.edges
    )


def test_attached_run_snapshot_revision_and_focused_depth_are_explicit_and_stable(
    tmp_path: Path,
) -> None:
    assets, snapshots, revision_one, revision_two, snapshot, attached, unattached = _complete_chain(
        tmp_path
    )
    service = _service(assets, snapshots)

    graph = service.graph()

    strategy = _node(graph, "STRATEGY", revision_one.lineage.strategy_id or "")
    attached_node = _node(graph, "RUN", attached.manifest.run_id)
    unattached_node = _node(graph, "RUN", unattached.manifest.run_id)
    assert any(
        edge.edge_type == "EXECUTES_STRATEGY"
        and edge.source_node_id == strategy.node_id
        and edge.target_node_id == attached_node.node_id
        and edge.source_field == "ResearchHypothesis.lineage.run_ids"
        for edge in graph.edges
    )
    assert unattached_node.status == "ORPHAN"
    assert not any(
        edge.edge_type == "EXECUTES_STRATEGY" and edge.target_node_id == unattached_node.node_id
        for edge in graph.edges
    )
    assert attached.manifest.trace_id is not None
    trace_node = _node(graph, "TRACE", attached.manifest.trace_id)
    assert any(
        edge.edge_type == "PRODUCES_TRACE"
        and edge.source_node_id == attached_node.node_id
        and edge.target_node_id == trace_node.node_id
        and edge.source_field in {"RunManifest.trace_id", "Frozen RunManifest.trace_id"}
        for edge in graph.edges
    )

    r1 = _node(graph, "HYPOTHESIS", revision_one.hypothesis_id)
    r2 = _node(graph, "HYPOTHESIS", revision_two.hypothesis_id)
    snapshot_node = _node(graph, "SNAPSHOT", snapshot.snapshot_id)
    assert r1.revision == 1
    assert r2.revision == 2
    assert r1.node_id != r2.node_id
    assert any(
        edge.source_node_id == r1.node_id and edge.target_node_id == snapshot_node.node_id
        for edge in graph.edges
    )
    assert not any(
        edge.source_node_id == r2.node_id and edge.target_node_id == snapshot_node.node_id
        for edge in graph.edges
    )

    upstream = service.graph(
        root_type="HYPOTHESIS",
        root_id=revision_one.hypothesis_id,
        direction="UPSTREAM",
        max_depth=1,
    )
    assert all(node.node_type != "STRATEGY" for node in upstream.nodes)
    downstream = service.graph(
        root_type="HYPOTHESIS",
        root_id=revision_one.hypothesis_id,
        direction="DOWNSTREAM",
        max_depth=2,
    )
    assert {node.node_type for node in downstream.nodes} >= {"HYPOTHESIS", "STRATEGY", "RUN"}
    assert all(node.node_type != "TRACE" for node in downstream.nodes)

    rebuilt = _service(assets, ResearchSnapshotRepository(tmp_path)).graph()
    assert [node.node_id for node in rebuilt.nodes] == [node.node_id for node in graph.nodes]
    assert [edge.edge_id for edge in rebuilt.edges] == [edge.edge_id for edge in graph.edges]
    summary = service.summary()
    assert summary.node_count == len(graph.nodes)
    assert summary.edge_count == len(graph.edges)
    assert summary.orphan_count >= 2


def test_explicit_strategy_run_mismatch_preserves_edge_and_marks_integrity(
    tmp_path: Path,
) -> None:
    assets, snapshots, hypothesis, _, _, attached, _ = _complete_chain(tmp_path)
    _, _, _, hypotheses, _, _, _, _ = assets
    assert hypothesis.lineage.strategy_id is not None
    assert attached.trace is not None
    repository = run_store.repository
    run_id = repository.new_run_id()
    running = attached.manifest.model_copy(
        update={
            "run_id": run_id,
            "status": "RUNNING",
            "completed_at": None,
            "strategy": attached.manifest.strategy.model_copy(
                update={"strategy_id": "explicit-mismatch-strategy"}
            ),
            "trace_id": None,
            "metrics": None,
            "artifacts": attached.manifest.artifacts.model_copy(update={"trace_sha256": None}),
        }
    )
    repository.create_running(
        running,
        repository.strategy_path(attached.manifest.run_id).read_bytes(),
    )
    mismatched = running.model_copy(
        update={
            "status": attached.manifest.status,
            "completed_at": attached.manifest.completed_at,
            "trace_id": attached.manifest.trace_id,
            "metrics": attached.manifest.metrics,
            "artifacts": attached.manifest.artifacts,
        }
    )
    repository.finalize(mismatched, attached.trace)
    assert mismatched.trace_id is not None
    hypotheses.save(
        hypothesis.model_copy(
            update={
                "lineage": hypothesis.lineage.model_copy(
                    update={
                        "run_ids": (*hypothesis.lineage.run_ids, mismatched.run_id),
                        "trace_ids": (
                            *hypothesis.lineage.trace_ids,
                            mismatched.trace_id,
                        ),
                    }
                )
            }
        )
    )

    graph = _service(assets, snapshots).graph()

    run_node = _node(graph, "RUN", mismatched.run_id)
    assert run_node.metadata["integrity_mismatch"] is True
    assert run_node.metadata["expected_strategy_id"] == hypothesis.lineage.strategy_id
    assert any(
        edge.edge_type == "EXECUTES_STRATEGY"
        and edge.target_node_id == run_node.node_id
        and edge.source_field == "ResearchHypothesis.lineage.run_ids"
        for edge in graph.edges
    )
