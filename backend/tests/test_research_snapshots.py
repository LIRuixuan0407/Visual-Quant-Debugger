from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_phase23_discovery import _assets, _request

from app.main import app
from app.research_snapshots import (
    CreateResearchSnapshot,
    ExperimentComparisonRequest,
    FrozenArtifact,
    ResearchSnapshotEngine,
    ResearchSnapshotRepository,
    SnapshotIntegrityError,
    compare_experiments,
)
from app.research_snapshots.models import (
    ResearchSnapshot,
    sha256_text,
    snapshot_content_fingerprint,
)
from app.runs import RunLedger, run_store
from app.runs.models import RunManifest


def _complete_hypothesis(tmp_path: Path):  # type: ignore[no-untyped-def]
    (
        discovery,
        factor_engine,
        factors,
        hypotheses,
        ledger,
        datasets,
        strategies,
        research_ids,
    ) = _assets(tmp_path)
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
    run = RunLedger().create(
        strategy_id=hypothesis.lineage.strategy_id,
        dataset_id=hypothesis.dataset_id,
        parameters={},
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    assert run.manifest.trace_id is not None
    hypothesis = discovery.attach_run(
        hypothesis,
        run.manifest.run_id,
        run.manifest.trace_id,
    )
    snapshots = ResearchSnapshotRepository(tmp_path)
    engine = ResearchSnapshotEngine(
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
    return engine, snapshots, hypotheses, ledger, hypothesis


def test_research_snapshot_api_is_registered() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/research-snapshots" in paths
    assert "/api/research-snapshots/compare" in paths
    assert "/api/research-snapshots/{snapshot_id}" in paths


def _artifact_variant(
    artifact: FrozenArtifact,
    *,
    artifact_id: str | None = None,
    source_revision: str | None = None,
    payload: object | None = None,
) -> FrozenArtifact:
    payload_json = (
        artifact.payload_json
        if payload is None
        else json.dumps(payload, separators=(",", ":"), sort_keys=True)
    )
    return FrozenArtifact(
        kind=artifact.kind,
        artifact_id=artifact_id or artifact.artifact_id,
        source_revision=source_revision or artifact.source_revision,
        payload_sha256=sha256_text(payload_json),
        payload_json=payload_json,
    )


def _comparison_variant(snapshot: ResearchSnapshot) -> ResearchSnapshot:
    next_run_id = "run-111111111111111111111111"
    next_trace_id = "trace-comparison-variant"
    manifest = RunManifest.model_validate_json(snapshot.runs[0].payload_json)
    assert manifest.metrics is not None
    next_metrics = manifest.metrics.model_copy(
        update={
            "total_return": manifest.metrics.total_return + 0.025,
            "net_pnl": manifest.metrics.net_pnl + 2_500.0,
            "final_equity": manifest.metrics.final_equity + 2_500.0,
        }
    )
    next_manifest = manifest.model_copy(
        update={
            "run_id": next_run_id,
            "trace_id": next_trace_id,
            "run_fingerprint": "sha256:comparison-variant-run",
            "parameters": {**manifest.parameters, "fee_bps": 15.0},
            "metrics": next_metrics,
        }
    )
    next_parameters = tuple(
        item.model_copy(
            update={
                "owner_id": next_run_id,
                "values": tuple(
                    value.model_copy(update={"value": 15.0}) if value.key == "fee_bps" else value
                    for value in item.values
                ),
            }
        )
        if item.owner_type == "RUN" and item.owner_id == manifest.run_id
        else item
        for item in snapshot.parameters
    )
    changed = snapshot.model_copy(
        update={
            "snapshot_id": "research-snapshot-111111111111111111111111",
            "name": "Controlled fee variant",
            "created_at": snapshot.created_at,
            "content_fingerprint": "sha256:" + "0" * 64,
            "lineage": snapshot.lineage.model_copy(
                update={"run_ids": (next_run_id,), "trace_ids": (next_trace_id,)}
            ),
            "runs": (
                _artifact_variant(
                    snapshot.runs[0],
                    artifact_id=next_run_id,
                    source_revision=next_manifest.run_fingerprint,
                    payload=next_manifest.model_dump(mode="json"),
                ),
            ),
            "traces": (_artifact_variant(snapshot.traces[0], artifact_id=next_trace_id),),
            "parameters": next_parameters,
        }
    )
    changed = changed.model_copy(
        update={"content_fingerprint": snapshot_content_fingerprint(changed)}
    )
    return ResearchSnapshot.model_validate(changed.model_dump())


def test_complete_research_snapshot_freezes_replayable_lineage(tmp_path: Path) -> None:
    engine, snapshots, hypotheses, ledger, hypothesis = _complete_hypothesis(tmp_path)
    snapshot = engine.create(
        CreateResearchSnapshot(
            name="Diversified price signals · frozen research",
            hypothesis_id=hypothesis.hypothesis_id,
        )
    )

    assert snapshot.content_fingerprint == snapshot_content_fingerprint(snapshot)
    assert snapshot.dataset.source_revision == hypothesis.dataset_fingerprint
    assert snapshot.lineage.hypothesis_revision == hypothesis.revision
    assert tuple(item.artifact_id for item in snapshot.factors) == (
        hypothesis.lineage.factor_research_ids
    )
    assert snapshot.strategy.source_revision.startswith("sha256:")
    assert tuple(item.artifact_id for item in snapshot.runs) == hypothesis.lineage.run_ids
    assert tuple(item.artifact_id for item in snapshot.traces) == hypothesis.lineage.trace_ids
    assert snapshot.time_boundaries.research.start is not None
    assert snapshot.time_boundaries.validation.start is not None
    assert snapshot.time_boundaries.holdout.start is not None
    assert {item.owner_type for item in snapshot.parameters} >= {
        "HYPOTHESIS",
        "FACTOR",
        "PORTFOLIO",
        "STRATEGY",
        "RUN",
    }
    assert snapshot.environment.python_version
    assert snapshot.environment.dependencies

    frozen_hypothesis_payload = snapshot.hypothesis.payload_json
    hypotheses.save(hypothesis.model_copy(update={"title": "Changed after freezing"}))
    stored = snapshots.get(snapshot.snapshot_id)
    assert stored is not None
    assert stored.hypothesis.payload_json == frozen_hypothesis_payload
    assert "Changed after freezing" not in stored.hypothesis.payload_json

    entries = [item for item in ledger.list() if item.kind == "SNAPSHOT"]
    assert len(entries) == 1
    assert entries[0].research_snapshot_id == snapshot.snapshot_id
    assert entries[0].metadata["content_fingerprint"] == snapshot.content_fingerprint


def test_snapshot_repository_never_overwrites_an_existing_identity(tmp_path: Path) -> None:
    engine, snapshots, _, _, hypothesis = _complete_hypothesis(tmp_path)
    snapshot = engine.create(
        CreateResearchSnapshot(name="Immutable experiment", hypothesis_id=hypothesis.hypothesis_id)
    )
    changed = snapshot.model_copy(update={"name": "Attempted overwrite"})
    changed = changed.model_copy(
        update={"content_fingerprint": snapshot_content_fingerprint(changed)}
    )

    with pytest.raises(SnapshotIntegrityError, match="immutable and already exists"):
        snapshots.save(changed)


def test_snapshot_requires_portfolio_strategy_run_and_trace(tmp_path: Path) -> None:
    discovery, _, _, _, ledger, datasets, strategies, research_ids = _assets(tmp_path)
    hypothesis = discovery.create(_request(research_ids))
    engine = ResearchSnapshotEngine(
        datasets,
        discovery.factors,
        discovery.relationships,
        discovery.walk_forward,
        discovery.hypotheses,
        discovery.portfolios,
        strategies,
        run_store.repository,
        ResearchSnapshotRepository(tmp_path),
        ledger,
    )

    with pytest.raises(ValueError, match="Portfolio, Strategy, and matched Run / Trace"):
        engine.create(
            CreateResearchSnapshot(
                name="Incomplete research",
                hypothesis_id=hypothesis.hypothesis_id,
            )
        )


def test_experiment_compare_uses_frozen_context_treatments_results_and_traces(
    tmp_path: Path,
) -> None:
    engine, snapshots, _, _, hypothesis = _complete_hypothesis(tmp_path)
    baseline = engine.create(
        CreateResearchSnapshot(name="Baseline experiment", hypothesis_id=hypothesis.hypothesis_id)
    )
    variant = snapshots.save(_comparison_variant(baseline))

    report = compare_experiments(
        snapshots,
        ExperimentComparisonRequest(snapshot_ids=(baseline.snapshot_id, variant.snapshot_id)),
    )

    assert report.comparability == "STRICTLY_COMPARABLE"
    assert all(item.same for item in report.context_diff if item.field != "creation_environment")
    assert any(
        item.owner_type == "RUN" and item.parameter == "fee_bps" and item.values == (5.0, 15.0)
        for item in report.parameter_diff
    )
    run_return = next(
        item
        for item in report.metric_diff
        if item.scope == "PRIMARY_RUN" and item.metric == "total_return"
    )
    assert run_return.differences_from_first[1] == pytest.approx(0.025)
    assert report.primary_run_comparison.comparability == "STRICTLY_COMPARABLE"
    assert report.primary_run_comparison.first_behavioral_divergence is not None
    assert (
        report.primary_run_comparison.first_behavioral_divergence.status
        == "NO_BEHAVIORAL_DIVERGENCE"
    )
    assert "does not select a winner" in report.comparison_disclosure
    assert any(
        item.semantic_key == "RUN:1" and not item.same_revision for item in report.artifact_diff
    )

    different_dataset = variant.model_copy(
        update={
            "snapshot_id": "research-snapshot-222222222222222222222222",
            "content_fingerprint": "sha256:" + "0" * 64,
            "dataset": variant.dataset.model_copy(
                update={"source_revision": "sha256:different-dataset-revision"}
            ),
        }
    )
    different_dataset = different_dataset.model_copy(
        update={"content_fingerprint": snapshot_content_fingerprint(different_dataset)}
    )
    snapshots.save(ResearchSnapshot.model_validate(different_dataset.model_dump()))
    descriptive = compare_experiments(
        snapshots,
        ExperimentComparisonRequest(
            snapshot_ids=(baseline.snapshot_id, different_dataset.snapshot_id)
        ),
    )
    assert descriptive.comparability == "DESCRIPTIVE_ONLY"
