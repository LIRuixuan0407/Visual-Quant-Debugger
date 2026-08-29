from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from test_research_snapshots import _complete_hypothesis

import app.api.research_bundles as research_bundles_api
import app.api.workspaces as workspaces_api
from app.datasets import DatasetImportRequest, DatasetRegistry
from app.discovery.repository import HypothesisRepository
from app.factor_relationships.repository import FactorRelationshipRepository
from app.factors.repository import FactorResearchRepository
from app.main import app
from app.portfolio_lab.repository import PortfolioResearchRepository
from app.research_bundles import (
    BundleExportRequest,
    BundleRootObject,
    ResearchBundleError,
    ResearchBundleService,
)
from app.research_snapshots import (
    CreateResearchSnapshot,
    FrozenArtifact,
    ResearchSnapshotRepository,
)
from app.research_snapshots.models import (
    ResearchSnapshot,
    SnapshotEnvironment,
    SnapshotLineage,
    SnapshotPeriod,
    SnapshotTimeBoundaries,
    sha256_text,
    snapshot_content_fingerprint,
)
from app.runs import RunLedger, RunManifest, RunRepository, run_store
from app.runs.models import (
    ArtifactHashes,
    DatasetRevision,
    EnvironmentSnapshot,
    ExecutionModelRevision,
    StrategyRevision,
)
from app.runs.models import (
    ResearchPeriod as RunResearchPeriod,
)
from app.runs.repository import sha256_bytes
from app.trace import trace_to_json
from app.universes.repository import UniverseRepository
from app.walk_forward.repository import WalkForwardRepository


def _dataset(registry: DatasetRegistry, sample_path: Path):  # type: ignore[no-untyped-def]
    source_rows = list(csv.DictReader(sample_path.open(encoding="utf-8")))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=("timestamp", "symbol", "close"))
    writer.writeheader()
    for row in source_rows:
        writer.writerow(
            {"timestamp": row["timestamp"], "symbol": "ASSET_A", "close": row["asset_a_close"]}
        )
        writer.writerow(
            {"timestamp": row["timestamp"], "symbol": "ASSET_B", "close": row["asset_b_close"]}
        )
    preview = registry.preview("portable.csv", output.getvalue().encode())
    return registry.commit(
        DatasetImportRequest(
            preview_id=preview.preview_id,
            name="Portable pairs data",
            mapping={"timestamp": "timestamp", "symbol": "symbol", "close": "close"},
            timezone="UTC",
            frequency="1D",
        )
    )


def _artifact(kind: str, artifact_id: str, revision: str, payload: str) -> FrozenArtifact:
    return FrozenArtifact(
        kind=kind,  # type: ignore[arg-type]
        artifact_id=artifact_id,
        source_revision=revision,
        payload_sha256=sha256_text(payload),
        payload_json=payload,
    )


def _snapshot(source: Path, run_id: str, dataset_id: str) -> ResearchSnapshot:
    snapshots = ResearchSnapshotRepository(source)
    runs = RunRepository(source)
    manifest = runs.get_manifest(run_id)
    trace = runs.load_trace_for_run(run_id)
    dataset = DatasetRegistry(source).get(dataset_id)
    assert dataset is not None
    assert manifest.trace_id is not None
    assert manifest.artifacts.trace_sha256 is not None
    empty = "{}"
    created = datetime.now(UTC)
    snapshot = ResearchSnapshot(
        snapshot_id="research-snapshot-1234567890abcdef12345678",
        name="Portable research",
        created_at=created,
        content_fingerprint="sha256:" + "0" * 64,
        lineage=SnapshotLineage(
            dataset_id=dataset.dataset_id,
            dataset_family_id=dataset.dataset_family_id,
            dataset_revision=dataset.revision,
            factor_research_ids=("factor-research-portable",),
            factor_ids=("factor-portable",),
            relationship_ids=(),
            walk_forward_ids=(),
            hypothesis_id="hypothesis-portable",
            hypothesis_revision=1,
            portfolio_research_id="portfolio-portable",
            strategy_id=manifest.strategy.strategy_id,
            run_ids=(manifest.run_id,),
            trace_ids=(manifest.trace_id,),
        ),
        dataset=_artifact(
            "DATASET",
            dataset.dataset_id,
            dataset.content_fingerprint,
            dataset.model_dump_json(),
        ),
        factors=(_artifact("FACTOR_RESEARCH", "factor-research-portable", "sha256:factor", empty),),
        relationships=(),
        walk_forward=(),
        hypothesis=_artifact("HYPOTHESIS", "hypothesis-portable", "r1", empty),
        portfolio=_artifact("PORTFOLIO_RESEARCH", "portfolio-portable", "r1", empty),
        strategy=_artifact(
            "STRATEGY_SOURCE",
            manifest.strategy.strategy_id,
            manifest.strategy.source_fingerprint,
            empty,
        ),
        runs=(
            _artifact(
                "RUN_MANIFEST",
                manifest.run_id,
                manifest.run_fingerprint,
                manifest.model_dump_json(),
            ),
        ),
        traces=(
            _artifact(
                "TRACE",
                manifest.trace_id,
                manifest.artifacts.trace_sha256,
                trace_to_json(trace, indent=None),
            ),
        ),
        parameters=(),
        time_boundaries=SnapshotTimeBoundaries(
            research=SnapshotPeriod(label="Research", source_id=dataset.dataset_id),
            validation=SnapshotPeriod(label="Validation", source_id=dataset.dataset_id),
            holdout=SnapshotPeriod(label="Holdout", source_id=dataset.dataset_id),
            runs=(
                SnapshotPeriod(
                    label="Run",
                    source_id=manifest.run_id,
                    start=manifest.period.start,
                    end=manifest.period.end,
                    cutoff=manifest.period.cutoff,
                ),
            ),
        ),
        environment=SnapshotEnvironment(
            python_version="3.12",
            python_implementation="CPython",
            platform="test",
            machine="test",
            vqd_version="0.1.0",
            dependencies=(),
        ),
    )
    snapshot = snapshot.model_copy(
        update={"content_fingerprint": snapshot_content_fingerprint(snapshot)}
    )
    snapshots.save(snapshot)
    return snapshot


def _source_workspace(tmp_path: Path):  # type: ignore[no-untyped-def]
    source = tmp_path / "source"
    source.mkdir()
    datasets = DatasetRegistry(source)
    dataset = _dataset(datasets, Path(__file__).parents[2] / "sample_data" / "pairs_daily.csv")
    previous = run_store.repository
    run_store.repository = RunRepository(source)
    try:
        run = RunLedger().create(
            strategy_id="pairs-trading",
            dataset_id=dataset.dataset_id,
            parameters={"lookback": 20},
            research_cutoff=None,
            dataset_registry_override=datasets,
        )
    finally:
        run_store.repository = previous
    snapshot = _snapshot(source, run.manifest.run_id, dataset.dataset_id)
    service = ResearchBundleService(
        ResearchSnapshotRepository(source),
        datasets,
        RunRepository(source),
    )
    return source, service, dataset, run.manifest, snapshot


def test_portable_bundle_restores_exact_dataset_run_trace_and_snapshot(tmp_path: Path) -> None:
    source, source_service, dataset, run, snapshot = _source_workspace(tmp_path)
    before_export = {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file() and "-wal" not in path.name and "-shm" not in path.name
    }
    manifest, content = source_service.export(
        BundleExportRequest(
            mode="PORTABLE",
            root_objects=(BundleRootObject(kind="SNAPSHOT", object_id=snapshot.snapshot_id),),
        )
    )
    assert manifest.mode == "PORTABLE"
    assert manifest.frozen_artifact_count >= 6
    assert {item.kind for item in manifest.external_dependencies} <= {"FROZEN_ARTIFACT_SCHEMA"}
    after_export = {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file() and "-wal" not in path.name and "-shm" not in path.name
    }
    assert after_export == before_export

    target = tmp_path / "target"
    target_service = ResearchBundleService(
        ResearchSnapshotRepository(target),
        DatasetRegistry(target),
        RunRepository(target),
    )
    before_preview = {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file() and "-wal" not in path.name and "-shm" not in path.name
    }
    preview = target_service.preview(content)
    after_preview = {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file() and "-wal" not in path.name and "-shm" not in path.name
    }
    assert after_preview == before_preview
    assert preview.valid is True
    assert {item.status for item in preview.conflicts} == {"IMPORT"}

    result = target_service.import_preview(preview.preview_id)
    assert f"SNAPSHOT:{snapshot.snapshot_id}" in result.imported
    assert target_service.datasets.get(dataset.dataset_id) == dataset
    assert target_service.runs.get_manifest(run.run_id) == run
    assert target_service.runs.load_trace_for_run(run.run_id) is not None
    assert target_service.snapshots.get(snapshot.snapshot_id) == snapshot

    second = target_service.preview(content)
    assert {item.status for item in second.conflicts} == {"REUSE"}
    repeated = target_service.import_preview(second.preview_id)
    assert repeated.imported == ()
    assert len(repeated.reused) == 3


def test_reference_only_preview_marks_missing_rows_and_replay_unavailable(tmp_path: Path) -> None:
    _, source_service, _, run, snapshot = _source_workspace(tmp_path)
    _, content = source_service.export(
        BundleExportRequest(
            mode="REFERENCE_ONLY",
            root_objects=(BundleRootObject(kind="SNAPSHOT", object_id=snapshot.snapshot_id),),
        )
    )
    target = tmp_path / "target-reference"
    target_service = ResearchBundleService(
        ResearchSnapshotRepository(target),
        DatasetRegistry(target),
        RunRepository(target),
    )
    preview = target_service.preview(content)
    statuses = {(item.kind, item.status) for item in preview.conflicts}
    assert ("SNAPSHOT", "IMPORT") in statuses
    assert ("DATASET", "UNAVAILABLE") in statuses
    assert ("RUN", "UNAVAILABLE") in statuses
    assert any(item.object_id == run.run_id for item in preview.external_dependencies)

    imported = target_service.import_preview(preview.preview_id)
    assert imported.imported == (f"SNAPSHOT:{snapshot.snapshot_id}",)
    assert len(imported.unavailable) == 2
    assert target_service.snapshots.get(snapshot.snapshot_id) == snapshot


def test_portable_bundle_preserves_dataset_revision_chain(tmp_path: Path) -> None:
    source = tmp_path / "source-revisions"
    datasets = DatasetRegistry(source)
    first = _dataset(
        datasets,
        Path(__file__).parents[2] / "sample_data" / "pairs_daily.csv",
    )
    first_csv = datasets.datasets_root / first.dataset_id / "data.csv"
    rows = list(csv.DictReader(first_csv.open(encoding="utf-8")))
    rows[-1]["close"] = str(float(rows[-1]["close"]) + 1.0)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=("timestamp", "symbol", "close"))
    writer.writeheader()
    writer.writerows(rows)
    preview = datasets.preview("portable-r2.csv", output.getvalue().encode())
    second = datasets.commit(
        DatasetImportRequest(
            preview_id=preview.preview_id,
            name=first.name,
            mapping={"timestamp": "timestamp", "symbol": "symbol", "close": "close"},
            timezone="UTC",
            frequency="1D",
            dataset_family_id=first.dataset_family_id,
            revision_reason="Extend portable history",
        )
    )
    previous = run_store.repository
    run_store.repository = RunRepository(source)
    try:
        run = RunLedger().create(
            strategy_id="pairs-trading",
            dataset_id=second.dataset_id,
            parameters={"lookback": 20},
            research_cutoff=None,
            dataset_registry_override=datasets,
        )
    finally:
        run_store.repository = previous

    source_service = ResearchBundleService(
        ResearchSnapshotRepository(source),
        datasets,
        RunRepository(source),
    )
    manifest, content = source_service.export(
        BundleExportRequest(
            mode="PORTABLE",
            root_objects=(BundleRootObject(kind="RUN", object_id=run.manifest.run_id),),
        )
    )
    bundled_dataset_ids = {item.object_id for item in manifest.objects if item.kind == "DATASET"}
    assert bundled_dataset_ids == {first.dataset_id, second.dataset_id}

    target = tmp_path / "target-revisions"
    target_service = ResearchBundleService(
        ResearchSnapshotRepository(target),
        DatasetRegistry(target),
        RunRepository(target),
    )
    preview = target_service.preview(content)
    target_service.import_preview(preview.preview_id)
    history = target_service.datasets.family_history(second.dataset_family_id or "")
    assert history is not None
    assert [item.dataset_id for item in history.revisions] == [
        first.dataset_id,
        second.dataset_id,
    ]
    assert history.family.latest_dataset_id == second.dataset_id


def test_bundle_rejects_checksum_tampering_before_import(tmp_path: Path) -> None:
    _, source_service, _, _, snapshot = _source_workspace(tmp_path)
    _, content = source_service.export(
        BundleExportRequest(
            mode="PORTABLE",
            root_objects=(BundleRootObject(kind="SNAPSHOT", object_id=snapshot.snapshot_id),),
        )
    )
    source = io.BytesIO(content)
    output = io.BytesIO()
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(output, "w") as modified:
        for name in original.namelist():
            payload = original.read(name)
            if "/snapshots/" in name:
                payload += b"tampered"
            modified.writestr(name, payload)

    target_service = ResearchBundleService(
        ResearchSnapshotRepository(tmp_path / "target-tamper"),
        DatasetRegistry(tmp_path / "target-tamper"),
        RunRepository(tmp_path / "target-tamper"),
    )
    with pytest.raises(ResearchBundleError, match="checksum mismatch"):
        target_service.preview(output.getvalue())


def test_same_immutable_id_with_different_snapshot_content_is_rejected(tmp_path: Path) -> None:
    _, source_service, _, _, snapshot = _source_workspace(tmp_path)
    _, content = source_service.export(
        BundleExportRequest(
            mode="PORTABLE",
            root_objects=(BundleRootObject(kind="SNAPSHOT", object_id=snapshot.snapshot_id),),
        )
    )
    target = tmp_path / "target-conflict"
    snapshots = ResearchSnapshotRepository(target)
    conflicting = snapshot.model_copy(
        update={
            "name": "Different local content",
            "content_fingerprint": "sha256:" + "0" * 64,
        }
    )
    conflicting = conflicting.model_copy(
        update={"content_fingerprint": snapshot_content_fingerprint(conflicting)}
    )
    snapshots.save(conflicting)
    target_service = ResearchBundleService(
        snapshots,
        DatasetRegistry(target),
        RunRepository(target),
    )

    preview = target_service.preview(content)

    assert preview.valid is False
    conflict = next(
        item
        for item in preview.conflicts
        if item.kind == "SNAPSHOT" and item.object_id == snapshot.snapshot_id
    )
    assert conflict.status == "REJECT"
    with pytest.raises(ResearchBundleError, match="different content"):
        target_service.import_preview(preview.preview_id)
    assert snapshots.get(snapshot.snapshot_id) == conflicting


def test_portable_custom_strategy_source_is_archived_but_never_executed(tmp_path: Path) -> None:
    source = tmp_path / "source-code"
    datasets = DatasetRegistry(source)
    runs = RunRepository(source)
    definition = datasets.get("pairs-sample-v1")
    assert definition is not None
    sentinel = tmp_path / "must-not-exist.txt"
    source_bytes = (
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('executed', encoding='utf-8')\n"
    ).encode()
    source_hash = sha256_bytes(source_bytes)
    run_id = runs.new_run_id()
    running = RunManifest(
        run_id=run_id,
        run_fingerprint="sha256:portable-source-test",
        status="RUNNING",
        created_at=datetime.now(UTC),
        completed_at=None,
        strategy=StrategyRevision(
            strategy_id="portable-custom-strategy",
            name="Portable custom strategy",
            version="1.0",
            class_name="PortableStrategy",
            source_fingerprint=source_hash,
            original_source_path="/outside/workspace/portable_strategy.py",
        ),
        dataset=DatasetRevision(
            dataset_id=definition.dataset_id,
            name=definition.name,
            content_fingerprint=definition.content_fingerprint,
            dataset_family_id=definition.dataset_family_id,
            revision=definition.revision,
            source_timezone=definition.source_timezone,
            symbols=definition.symbols,
        ),
        period=RunResearchPeriod(start=None, end=None, cutoff=None),
        parameters={},
        execution_model=ExecutionModelRevision(),
        engine=EnvironmentSnapshot(
            python_version="3.12",
            platform="test",
            vqd_version="0.1.0",
        ),
        artifacts=ArtifactHashes(strategy_source_sha256=source_hash),
    )
    runs.create_running(running, source_bytes)
    completed = running.model_copy(
        update={"status": "COMPLETED", "completed_at": datetime.now(UTC)}
    )
    runs.finalize(completed, None)
    service = ResearchBundleService(ResearchSnapshotRepository(source), datasets, runs)
    bundle_manifest, content = service.export(
        BundleExportRequest(
            mode="PORTABLE",
            root_objects=(BundleRootObject(kind="RUN", object_id=run_id),),
        )
    )

    target = tmp_path / "target-code"
    target_service = ResearchBundleService(
        ResearchSnapshotRepository(target),
        DatasetRegistry(target),
        RunRepository(target),
    )
    preview = target_service.preview(content)
    target_service.import_preview(preview.preview_id)

    assert sentinel.exists() is False
    imported_source = target_service.runs.strategy_source(run_id)
    assert "must-not-exist.txt" in imported_source.source
    archive = target / ".vqd" / "research-bundles" / f"{bundle_manifest.bundle_id}.zip"
    assert archive.read_bytes() == content


def test_bundle_api_import_uses_persisted_preview_across_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source_service, _, _, snapshot = _source_workspace(tmp_path)
    _, content = source_service.export(
        BundleExportRequest(
            mode="PORTABLE",
            root_objects=(BundleRootObject(kind="SNAPSHOT", object_id=snapshot.snapshot_id),),
        )
    )

    target = tmp_path / "target-api"
    monkeypatch.setattr(
        research_bundles_api,
        "research_snapshot_repository",
        ResearchSnapshotRepository(target),
    )
    monkeypatch.setattr(research_bundles_api, "dataset_registry", DatasetRegistry(target))
    monkeypatch.setattr(workspaces_api, "dataset_registry", DatasetRegistry(target))
    monkeypatch.setattr(
        workspaces_api,
        "research_snapshot_repository",
        ResearchSnapshotRepository(target),
    )
    monkeypatch.setattr(
        workspaces_api,
        "factor_research_repository",
        FactorResearchRepository(target),
    )
    monkeypatch.setattr(
        workspaces_api,
        "factor_relationship_repository",
        FactorRelationshipRepository(target),
    )
    monkeypatch.setattr(workspaces_api, "walk_forward_repository", WalkForwardRepository(target))
    monkeypatch.setattr(
        workspaces_api,
        "hypothesis_repository",
        HypothesisRepository(target),
    )
    monkeypatch.setattr(
        workspaces_api,
        "portfolio_research_repository",
        PortfolioResearchRepository(target),
    )
    previous = run_store.repository
    run_store.repository = RunRepository(target)
    workspaces_api._workspace_service = None
    workspaces_api.workspace_service().ensure_default_workspace()

    async def request(method: str, path: str, **kwargs: object) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    try:
        preview = asyncio.run(
            request(
                "POST",
                "/api/research-bundles/preview",
                content=content,
                headers={"Content-Type": "application/zip"},
            )
        )
        assert preview.status_code == 200
        preview_id = preview.json()["preview_id"]
        imported = asyncio.run(
            request(
                "POST",
                f"/api/research-bundles/import/{preview_id}",
                json={"target_workspace_id": "workspace-default"},
            )
        )
        memberships = workspaces_api.workspace_service().memberships("workspace-default")
    finally:
        run_store.repository = previous

    assert imported.status_code == 200
    assert imported.json()["bundle_id"].startswith("research-bundle-")
    assert imported.json()["target_workspace_id"] == "workspace-default"
    assert any(
        item.object_type == "RESEARCH_BUNDLE" and item.object_id == imported.json()["bundle_id"]
        for item in memberships
    )
    assert any(
        item.object_type == "SNAPSHOT" and item.object_id == snapshot.snapshot_id
        for item in memberships
    )
    assert ResearchSnapshotRepository(target).get(snapshot.snapshot_id) == snapshot


def test_portable_bundle_restores_live_research_records_from_a_real_snapshot(
    tmp_path: Path,
) -> None:
    engine, snapshots, hypotheses, _, hypothesis = _complete_hypothesis(tmp_path)
    snapshot = engine.create(
        CreateResearchSnapshot(
            name="Portable complete research",
            hypothesis_id=hypothesis.hypothesis_id,
        )
    )
    source_service = ResearchBundleService(
        snapshots,
        engine.datasets,
        RunRepository(tmp_path),
    )
    manifest, content = source_service.export(
        BundleExportRequest(
            mode="PORTABLE",
            root_objects=(BundleRootObject(kind="SNAPSHOT", object_id=snapshot.snapshot_id),),
        )
    )

    kinds = {item.kind for item in manifest.objects}
    assert {
        "DATASET",
        "FACTOR_RESEARCH",
        "HYPOTHESIS",
        "PORTFOLIO_RESEARCH",
        "RUN",
        "SNAPSHOT",
    } <= kinds
    assert manifest.external_dependencies == ()

    target = tmp_path / "portable-target"
    target_service = ResearchBundleService(
        ResearchSnapshotRepository(target),
        DatasetRegistry(target),
        RunRepository(target),
    )
    preview = target_service.preview(content)
    assert preview.valid is True
    assert all(item.status == "IMPORT" for item in preview.conflicts)
    target_service.import_preview(preview.preview_id)

    target_factors = FactorResearchRepository(target)
    for research_id in snapshot.lineage.factor_research_ids:
        assert target_factors.get(research_id) == engine.factors.get(research_id)

    target_relationships = FactorRelationshipRepository(target)
    for relationship_id in snapshot.lineage.relationship_ids:
        assert target_relationships.get(relationship_id) == engine.relationships.get(
            relationship_id
        )

    target_walk_forward = WalkForwardRepository(target)
    for walk_forward_id in snapshot.lineage.walk_forward_ids:
        assert target_walk_forward.get(walk_forward_id) == engine.walk_forward.get(walk_forward_id)

    assert HypothesisRepository(target).get(hypothesis.hypothesis_id) == hypotheses.get(
        hypothesis.hypothesis_id
    )
    assert PortfolioResearchRepository(target).get(
        snapshot.lineage.portfolio_research_id or ""
    ) == engine.portfolios.get(snapshot.lineage.portfolio_research_id or "")

    target_universes = UniverseRepository(target)
    for universe_id in snapshot.lineage.universe_ids:
        assert target_universes.get(universe_id) == engine.universes.get(universe_id)

    repeated_preview = target_service.preview(content)
    assert {item.status for item in repeated_preview.conflicts} == {"REUSE"}
    repeated = target_service.import_preview(repeated_preview.preview_id)
    assert repeated.imported == ()
    assert repeated.unavailable == ()


def test_bundle_rejects_manifest_identity_that_does_not_match_payload(tmp_path: Path) -> None:
    _, source_service, _, _, snapshot = _source_workspace(tmp_path)
    _, content = source_service.export(
        BundleExportRequest(
            mode="PORTABLE",
            root_objects=(BundleRootObject(kind="SNAPSHOT", object_id=snapshot.snapshot_id),),
        )
    )
    source = io.BytesIO(content)
    output = io.BytesIO()
    replacement_id = "research-snapshot-aaaaaaaaaaaaaaaaaaaaaaaa"
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(output, "w") as modified:
        manifest = json.loads(original.read("manifest.json"))
        manifest["root_objects"][0]["object_id"] = replacement_id
        snapshot_object = next(item for item in manifest["objects"] if item["kind"] == "SNAPSHOT")
        snapshot_object["object_id"] = replacement_id
        for name in original.namelist():
            if name != "manifest.json":
                modified.writestr(name, original.read(name))
        modified.writestr("manifest.json", json.dumps(manifest))

    target_service = ResearchBundleService(
        ResearchSnapshotRepository(tmp_path / "target-identity"),
        DatasetRegistry(tmp_path / "target-identity"),
        RunRepository(tmp_path / "target-identity"),
    )
    with pytest.raises(ResearchBundleError, match="payload identity"):
        target_service.preview(output.getvalue())
