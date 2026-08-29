from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from app.corporate_actions import CorporateActionDataset
from app.corporate_actions.repository import CorporateActionRepository
from app.datasets import DatasetDefinition, DatasetRegistry
from app.discovery.models import ResearchHypothesis
from app.discovery.repository import HypothesisRepository
from app.factor_relationships.models import FactorRelationshipRecord
from app.factor_relationships.repository import FactorRelationshipRepository
from app.factor_sdk.loader import source_fingerprint
from app.factors.models import FactorResearchRecord
from app.factors.repository import FactorResearchRepository
from app.portfolio_lab.models import PortfolioResearchRecord
from app.portfolio_lab.repository import PortfolioResearchRepository
from app.research_snapshots import FrozenArtifact, ResearchSnapshot, ResearchSnapshotRepository
from app.runs import RunManifest, RunRepository, RunValidationReport
from app.strategy_drift import StrategyDriftReport
from app.strategy_drift.repository import StrategyDriftRepository
from app.universes.models import HistoricalUniverse
from app.universes.repository import UniverseRepository
from app.walk_forward.models import WalkForwardResearchRecord
from app.walk_forward.repository import WalkForwardRepository

from .models import (
    BundleConflict,
    BundleConflictStatus,
    BundleExportRequest,
    BundleExternalDependency,
    BundleImportPreview,
    BundleImportResult,
    BundleObject,
    BundleObjectKind,
    ResearchBundleManifest,
)

try:
    APP_VERSION = version("visual-quant-debugger-backend")
except PackageNotFoundError:
    APP_VERSION = "0.1.0"
BUNDLE_ID_PATTERN = re.compile(r"^research-bundle-[0-9a-f]{24}$")
PREVIEW_ID_PATTERN = re.compile(r"^bundle-preview-[0-9a-f]{20}$")
REFERENCE_ONLY_RUN_ARTIFACTS_REASON = (
    "REFERENCE_ONLY bundles preserve the Run manifest but not replay artifacts."
)


def _sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _json_bytes(value: object) -> bytes:
    if isinstance(value, BaseModel):
        return (value.model_dump_json(indent=2) + "\n").encode()
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _content_conflict_status(matches: bool) -> BundleConflictStatus:
    return "REUSE" if matches else "REJECT"


@dataclass(frozen=True)
class _BundlePayload:
    manifest: ResearchBundleManifest
    files: dict[str, bytes]


class ResearchBundleError(ValueError):
    pass


class ResearchBundleService:
    def __init__(
        self,
        snapshots: ResearchSnapshotRepository,
        datasets: DatasetRegistry,
        runs: RunRepository,
    ) -> None:
        self.snapshots = snapshots
        self.datasets = datasets
        self.runs = runs
        self.drift_reports = StrategyDriftRepository(runs.workspace_root)
        self.universes = UniverseRepository(runs.workspace_root)
        self.corporate_actions = CorporateActionRepository(runs.workspace_root)
        self.factors = FactorResearchRepository(runs.workspace_root)
        self.relationships = FactorRelationshipRepository(runs.workspace_root)
        self.walk_forward = WalkForwardRepository(runs.workspace_root)
        self.hypotheses = HypothesisRepository(runs.workspace_root)
        self.portfolios = PortfolioResearchRepository(runs.workspace_root)
        self.archive_root = runs.workspace_root / ".vqd" / "research-bundles"
        preview_namespace = hashlib.sha256(str(runs.workspace_root).encode()).hexdigest()[:16]
        self.preview_root = (
            Path(tempfile.gettempdir()) / "vqd-research-bundle-previews" / preview_namespace
        )

    def export(self, request: BundleExportRequest) -> tuple[ResearchBundleManifest, bytes]:
        payload = self._build(request)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, content in sorted(payload.files.items()):
                archive.writestr(path, content)
            archive.writestr("manifest.json", _json_bytes(payload.manifest))
        return payload.manifest, buffer.getvalue()

    def _build(self, request: BundleExportRequest) -> _BundlePayload:
        files: dict[str, bytes] = {}
        objects: dict[tuple[str, str], BundleObject] = {}
        dependencies: dict[tuple[str, str], BundleExternalDependency] = {}
        frozen_artifact_keys: set[tuple[str, str]] = set()
        expanded_dataset_families: set[str] = set()

        def add_object(
            kind: BundleObjectKind,
            object_id: str,
            fingerprint: str,
            path: str,
            portable: bool,
        ) -> None:
            key = (kind, object_id)
            current = objects.get(key)
            candidate = BundleObject(
                kind=kind,
                object_id=object_id,
                fingerprint=fingerprint,
                path=path,
                portable=portable,
            )
            if current is not None and current != candidate:
                raise ResearchBundleError(f"Conflicting bundle object '{kind}:{object_id}'")
            objects[key] = candidate

        def add_record_object(
            kind: BundleObjectKind,
            object_id: str,
            record: BaseModel,
            directory: str,
        ) -> None:
            path = f"objects/{directory}/{object_id}.json"
            payload = _json_bytes(record)
            files[path] = payload
            add_object(kind, object_id, _sha256(payload), path, True)

        def add_dataset(dataset_id: str) -> None:
            definition = self.datasets.get(dataset_id)
            if definition is None:
                raise ResearchBundleError(f"Dataset '{dataset_id}' was not found")
            family_id = definition.dataset_family_id
            if (
                request.mode == "PORTABLE"
                and family_id is not None
                and family_id not in expanded_dataset_families
            ):
                expanded_dataset_families.add(family_id)
                for revision in self.datasets.revisions(family_id):
                    if (
                        revision.revision <= definition.revision
                        and revision.dataset_id != dataset_id
                    ):
                        add_dataset(revision.dataset_id)
            path = f"objects/datasets/{dataset_id}/metadata.json"
            files[path] = _json_bytes(definition)
            portable = request.mode == "PORTABLE" and dataset_id != "pairs-sample-v1"
            if portable:
                data_path = self.datasets.datasets_root / dataset_id / "data.csv"
                if not data_path.is_file():
                    raise ResearchBundleError(f"Dataset '{dataset_id}' data.csv is missing")
                files[f"objects/datasets/{dataset_id}/data.csv"] = data_path.read_bytes()
            elif request.mode == "REFERENCE_ONLY" and dataset_id != "pairs-sample-v1":
                dependencies[("DATASET_DATA", dataset_id)] = BundleExternalDependency(
                    kind="DATASET_DATA",
                    object_id=dataset_id,
                    reason="REFERENCE_ONLY bundles do not embed Dataset rows.",
                )
            add_object("DATASET", dataset_id, definition.content_fingerprint, path, portable)

        def add_drift_report(report: StrategyDriftReport) -> None:
            path = f"objects/drift-reports/{report.drift_report_id}.json"
            payload = _json_bytes(report)
            files[path] = payload
            add_object(
                "DRIFT_REPORT",
                report.drift_report_id,
                _sha256(payload),
                path,
                True,
            )

        def add_attribution_report(report: RunValidationReport) -> None:
            path = f"objects/attribution-reports/{report.report_id}.json"
            payload = _json_bytes(report)
            files[path] = payload
            add_object(
                "ATTRIBUTION_REPORT",
                report.report_id,
                _sha256(payload),
                path,
                True,
            )

        def add_run(run_id: str) -> None:
            manifest = self.runs.get_manifest(run_id)
            run_dir = self.runs._run_directory(run_id)
            manifest_path = f"objects/runs/{run_id}/manifest.json"
            manifest_payload = _json_bytes(manifest)
            files[manifest_path] = manifest_payload
            files[f"objects/runs/{run_id}/annotations.json"] = _json_bytes(
                self.runs.get_annotations(run_id)
            )
            portable = request.mode == "PORTABLE"
            if portable:
                for source in sorted(run_dir.iterdir()):
                    if source.is_file() and source.name != "manifest.json":
                        files[f"objects/runs/{run_id}/{source.name}"] = source.read_bytes()
            else:
                dependencies[("RUN_ARTIFACTS", run_id)] = BundleExternalDependency(
                    kind="RUN_ARTIFACTS",
                    object_id=run_id,
                    reason=REFERENCE_ONLY_RUN_ARTIFACTS_REASON,
                )
            add_object("RUN", run_id, _sha256(manifest_payload), manifest_path, portable)
            add_dataset(manifest.dataset.dataset_id)
            for summary in self.drift_reports.list():
                if summary.baseline_id == run_id or summary.observed_id == run_id:
                    drift_report = self.drift_reports.get(summary.drift_report_id)
                    if drift_report is not None:
                        add_drift_report(drift_report)
            for path in sorted(self.runs.validations_root.glob("validation-*.json")):
                attribution_report = self.runs.load_validation(path.stem)
                if run_id in {
                    attribution_report.backtest_run_id,
                    attribution_report.paper_run_id,
                    attribution_report.reference_run_id,
                }:
                    add_attribution_report(attribution_report)

        def add_snapshot(snapshot_id: str) -> None:
            snapshot = self.snapshots.get(snapshot_id)
            if snapshot is None:
                raise ResearchBundleError(f"Research Snapshot '{snapshot_id}' was not found")
            path = f"objects/snapshots/{snapshot_id}.json"
            files[path] = _json_bytes(snapshot)
            add_object("SNAPSHOT", snapshot_id, snapshot.content_fingerprint, path, True)
            add_dataset(snapshot.lineage.dataset_id)

            frozen_records: tuple[tuple[BundleObjectKind, str, BaseModel, str], ...] = ()
            parsed_records: list[tuple[BundleObjectKind, str, BaseModel, str]] = []
            frozen_groups: tuple[
                tuple[BundleObjectKind, tuple[FrozenArtifact, ...], type[BaseModel], str], ...
            ] = (
                ("UNIVERSE", snapshot.universes, HistoricalUniverse, "universes"),
                (
                    "CORPORATE_ACTION_DATASET",
                    snapshot.corporate_actions,
                    CorporateActionDataset,
                    "corporate-actions",
                ),
                ("FACTOR_RESEARCH", snapshot.factors, FactorResearchRecord, "factor-research"),
                (
                    "FACTOR_RELATIONSHIP",
                    snapshot.relationships,
                    FactorRelationshipRecord,
                    "factor-relationships",
                ),
                ("WALK_FORWARD", snapshot.walk_forward, WalkForwardResearchRecord, "walk-forward"),
                ("HYPOTHESIS", (snapshot.hypothesis,), ResearchHypothesis, "hypotheses"),
                (
                    "PORTFOLIO_RESEARCH",
                    (snapshot.portfolio,),
                    PortfolioResearchRecord,
                    "portfolio-research",
                ),
            )
            for kind, artifacts, model, directory in frozen_groups:
                for artifact in artifacts:
                    try:
                        record = model.model_validate_json(artifact.payload_json)
                    except ValueError:
                        dependencies[("FROZEN_ARTIFACT_SCHEMA", artifact.artifact_id)] = (
                            BundleExternalDependency(
                                kind="FROZEN_ARTIFACT_SCHEMA",
                                object_id=artifact.artifact_id,
                                reason=(
                                    "A frozen research payload cannot be restored into the current "
                                    "repository schema; it remains preserved inside the immutable "
                                    "Snapshot."
                                ),
                            )
                        )
                        continue
                    parsed_records.append((kind, artifact.artifact_id, record, directory))
            frozen_records = tuple(parsed_records)
            for kind, object_id, record, directory in frozen_records:
                add_record_object(kind, object_id, record, directory)
            for run_id in snapshot.lineage.run_ids:
                add_run(run_id)
            frozen = (
                (snapshot.dataset,)
                + snapshot.universes
                + snapshot.corporate_actions
                + snapshot.factors
                + snapshot.relationships
                + snapshot.walk_forward
                + (snapshot.hypothesis, snapshot.portfolio, snapshot.strategy)
                + snapshot.runs
                + snapshot.traces
            )
            frozen_artifact_keys.update((item.kind, item.artifact_id) for item in frozen)
            for item in snapshot.factors:
                try:
                    factor_record = FactorResearchRecord.model_validate_json(item.payload_json)
                except ValueError:
                    continue
                source_path = factor_record.factor.source_path
                if factor_record.factor.origin != "CUSTOM" or source_path is None:
                    continue
                source = Path(source_path).expanduser()
                dependency_key = ("CUSTOM_FACTOR_SOURCE", item.artifact_id)
                if request.mode != "PORTABLE" or not source.is_file():
                    dependencies[dependency_key] = BundleExternalDependency(
                        kind="CUSTOM_FACTOR_SOURCE",
                        object_id=item.artifact_id,
                        reason=(
                            "Custom Factor source is referenced by fingerprint but is not "
                            "embedded in this bundle."
                        ),
                    )
                    continue
                if factor_record.factor.source_fingerprint and (
                    source_fingerprint(source) != factor_record.factor.source_fingerprint
                ):
                    dependencies[dependency_key] = BundleExternalDependency(
                        kind="CUSTOM_FACTOR_SOURCE",
                        object_id=item.artifact_id,
                        reason=(
                            "Custom Factor source changed after the frozen research revision; "
                            "the current file was not embedded."
                        ),
                    )
                    continue
                source_name = hashlib.sha256(item.artifact_id.encode()).hexdigest()[:16]
                files[f"sources/factors/{source_name}.py"] = source.read_bytes()

        for root in request.root_objects:
            if root.kind == "SNAPSHOT":
                add_snapshot(root.object_id)
            else:
                add_run(root.object_id)

        checksums = {path: _sha256(content) for path, content in sorted(files.items())}
        manifest = ResearchBundleManifest(
            bundle_id=f"research-bundle-{secrets.token_hex(12)}",
            created_at=datetime.now(UTC),
            app_version=APP_VERSION,
            mode=request.mode,
            root_objects=request.root_objects,
            objects=tuple(sorted(objects.values(), key=lambda item: (item.kind, item.object_id))),
            object_count=len(objects),
            frozen_artifact_count=len(frozen_artifact_keys),
            checksums=checksums,
            external_dependencies=tuple(
                sorted(dependencies.values(), key=lambda item: (item.kind, item.object_id))
            ),
        )
        return _BundlePayload(manifest=manifest, files=files)

    def preview(self, content: bytes) -> BundleImportPreview:
        preview_id = f"bundle-preview-{hashlib.sha256(content).hexdigest()[:20]}"
        errors: list[str] = []
        manifest, files = self._read(content)
        conflicts = tuple(self._conflict(item, files) for item in manifest.objects)
        if any(item.status == "REJECT" for item in conflicts):
            errors.append("One or more immutable object IDs conflict with different local content.")
        external = list(manifest.external_dependencies)
        documented_ids = {item.object_id for item in external}
        for conflict in conflicts:
            if conflict.status == "UNAVAILABLE" and conflict.object_id not in documented_ids:
                external.append(
                    BundleExternalDependency(
                        kind=conflict.kind,
                        object_id=conflict.object_id,
                        reason=conflict.detail,
                    )
                )
                documented_ids.add(conflict.object_id)
        self._store_preview(preview_id, content)
        return BundleImportPreview(
            preview_id=preview_id,
            manifest=manifest,
            valid=not errors,
            conflicts=conflicts,
            external_dependencies=tuple(external),
            errors=tuple(errors),
        )

    def import_preview(self, preview_id: str) -> BundleImportResult:
        content = self._load_preview(preview_id)
        manifest, files = self._read(content)
        conflicts = tuple(self._conflict(item, files) for item in manifest.objects)
        rejected = [item for item in conflicts if item.status == "REJECT"]
        if rejected:
            raise ResearchBundleError(rejected[0].detail)
        self._assert_archive_compatible(manifest.bundle_id, content)

        imported: list[str] = []
        reused: list[str] = []
        unavailable: list[str] = []

        def import_order(item: BundleObject) -> tuple[int, int, str]:
            if item.kind == "DATASET":
                definition = DatasetDefinition.model_validate_json(files[item.path])
                return (0, definition.revision, item.object_id)
            if item.kind in {"UNIVERSE", "CORPORATE_ACTION_DATASET"}:
                return (1, 0, item.object_id)
            if item.kind in {
                "FACTOR_RESEARCH",
                "FACTOR_RELATIONSHIP",
                "WALK_FORWARD",
                "HYPOTHESIS",
                "PORTFOLIO_RESEARCH",
            }:
                return (2, 0, item.object_id)
            if item.kind == "RUN":
                return (3, 0, item.object_id)
            if item.kind == "SNAPSHOT":
                return (4, 0, item.object_id)
            return (5, 0, item.object_id)

        for item in sorted(manifest.objects, key=import_order):
            conflict = next(
                candidate
                for candidate in conflicts
                if candidate.kind == item.kind and candidate.object_id == item.object_id
            )
            label = f"{item.kind}:{item.object_id}"
            if conflict.status == "REUSE":
                reused.append(label)
                continue
            if conflict.status == "UNAVAILABLE":
                unavailable.append(label)
                continue
            if item.kind == "DATASET":
                definition = DatasetDefinition.model_validate_json(files[item.path])
                data = files.get(f"objects/datasets/{item.object_id}/data.csv")
                self.datasets.import_exact(definition, data)
            elif item.kind == "RUN":
                run_prefix = f"objects/runs/{item.object_id}/"
                run_files = {
                    path.removeprefix(run_prefix): value
                    for path, value in files.items()
                    if path.startswith(run_prefix) and path != item.path
                }
                annotations = run_files.pop("annotations.json", None)
                self.runs.import_completed(
                    RunManifest.model_validate_json(files[item.path]),
                    run_files,
                    annotations_json=annotations,
                )
            elif item.kind == "UNIVERSE":
                self.universes.save(HistoricalUniverse.model_validate_json(files[item.path]))
            elif item.kind == "CORPORATE_ACTION_DATASET":
                self.corporate_actions.save(
                    CorporateActionDataset.model_validate_json(files[item.path])
                )
            elif item.kind == "FACTOR_RESEARCH":
                self.factors.save(FactorResearchRecord.model_validate_json(files[item.path]))
            elif item.kind == "FACTOR_RELATIONSHIP":
                self.relationships.save(
                    FactorRelationshipRecord.model_validate_json(files[item.path])
                )
            elif item.kind == "WALK_FORWARD":
                self.walk_forward.save(
                    WalkForwardResearchRecord.model_validate_json(files[item.path])
                )
            elif item.kind == "HYPOTHESIS":
                self.hypotheses.save(ResearchHypothesis.model_validate_json(files[item.path]))
            elif item.kind == "PORTFOLIO_RESEARCH":
                self.portfolios.save(PortfolioResearchRecord.model_validate_json(files[item.path]))
            elif item.kind == "SNAPSHOT":
                self.snapshots.save(ResearchSnapshot.model_validate_json(files[item.path]))
            elif item.kind == "DRIFT_REPORT":
                self.drift_reports.save(StrategyDriftReport.model_validate_json(files[item.path]))
            else:
                self.runs.save_validation(RunValidationReport.model_validate_json(files[item.path]))
            imported.append(label)

        self._archive_bundle(manifest.bundle_id, content)
        return BundleImportResult(
            bundle_id=manifest.bundle_id,
            imported=tuple(imported),
            reused=tuple(reused),
            unavailable=tuple(unavailable),
        )

    def _preview_path(self, preview_id: str) -> Path:
        if not PREVIEW_ID_PATTERN.fullmatch(preview_id):
            raise ResearchBundleError(f"Invalid Research Bundle preview id '{preview_id}'")
        path = (self.preview_root / f"{preview_id}.zip").resolve()
        if path.parent != self.preview_root.resolve():
            raise ResearchBundleError("Research Bundle preview path escaped the workspace")
        return path

    def _store_preview(self, preview_id: str, content: bytes) -> None:
        path = self._preview_path(preview_id)
        if path.exists():
            if path.read_bytes() != content:
                raise ResearchBundleError(
                    f"Research Bundle preview '{preview_id}' already exists with different bytes"
                )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".zip.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _load_preview(self, preview_id: str) -> bytes:
        path = self._preview_path(preview_id)
        if not path.is_file():
            raise ResearchBundleError(
                f"Bundle preview '{preview_id}' was not found; upload the bundle again"
            )
        return path.read_bytes()

    def _archive_path(self, bundle_id: str) -> Path:
        if not BUNDLE_ID_PATTERN.fullmatch(bundle_id):
            raise ResearchBundleError(f"Invalid Research Bundle id '{bundle_id}'")
        path = (self.archive_root / f"{bundle_id}.zip").resolve()
        if path.parent != self.archive_root.resolve():
            raise ResearchBundleError("Research Bundle archive path escaped the workspace")
        return path

    def _assert_archive_compatible(self, bundle_id: str, content: bytes) -> None:
        path = self._archive_path(bundle_id)
        if path.exists() and path.read_bytes() != content:
            raise ResearchBundleError(
                f"Research Bundle '{bundle_id}' is already archived with different bytes"
            )

    def _archive_bundle(self, bundle_id: str, content: bytes) -> None:
        path = self._archive_path(bundle_id)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".zip.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _conflict(self, item: BundleObject, files: dict[str, bytes]) -> BundleConflict:
        if item.kind == "SNAPSHOT":
            existing_snapshot = self.snapshots.get(item.object_id)
            if existing_snapshot is None:
                return BundleConflict(
                    kind=item.kind,
                    object_id=item.object_id,
                    status="IMPORT",
                    detail="Immutable Research Snapshot is not present locally.",
                )
            status = _content_conflict_status(
                existing_snapshot.content_fingerprint == item.fingerprint
            )
            return BundleConflict(
                kind=item.kind,
                object_id=item.object_id,
                status=status,
                detail=(
                    "Same Snapshot ID and content fingerprint already exist; reuse is safe."
                    if status == "REUSE"
                    else "Same Snapshot ID exists with different content; import is rejected."
                ),
            )
        if item.kind == "DATASET":
            existing_dataset = self.datasets.get(item.object_id)
            if existing_dataset is not None:
                status = _content_conflict_status(
                    existing_dataset.content_fingerprint == item.fingerprint
                )
                return BundleConflict(
                    kind=item.kind,
                    object_id=item.object_id,
                    status=status,
                    detail=(
                        "Exact Dataset revision already exists; reuse is safe."
                        if status == "REUSE"
                        else "Same Dataset ID exists with different content; import is rejected."
                    ),
                )
            data_path = f"objects/datasets/{item.object_id}/data.csv"
            if item.object_id != "pairs-sample-v1" and data_path not in files:
                return BundleConflict(
                    kind=item.kind,
                    object_id=item.object_id,
                    status="UNAVAILABLE",
                    detail=(
                        "Dataset rows are not embedded in this bundle and are not "
                        "available locally."
                    ),
                )
            return BundleConflict(
                kind=item.kind,
                object_id=item.object_id,
                status="IMPORT",
                detail="Exact Dataset revision can be imported.",
            )
        repository_record: BaseModel | None = None
        repository_kind = False
        if item.kind == "UNIVERSE":
            repository_kind = True
            repository_record = self.universes.get(item.object_id)
        elif item.kind == "CORPORATE_ACTION_DATASET":
            repository_kind = True
            repository_record = self.corporate_actions.get(item.object_id)
        elif item.kind == "FACTOR_RESEARCH":
            repository_kind = True
            repository_record = self.factors.get(item.object_id)
        elif item.kind == "FACTOR_RELATIONSHIP":
            repository_kind = True
            repository_record = self.relationships.get(item.object_id)
        elif item.kind == "WALK_FORWARD":
            repository_kind = True
            repository_record = self.walk_forward.get(item.object_id)
        elif item.kind == "HYPOTHESIS":
            repository_kind = True
            repository_record = self.hypotheses.get(item.object_id)
        elif item.kind == "PORTFOLIO_RESEARCH":
            repository_kind = True
            repository_record = self.portfolios.get(item.object_id)

        if repository_kind:
            if repository_record is None:
                return BundleConflict(
                    kind=item.kind,
                    object_id=item.object_id,
                    status="IMPORT",
                    detail="Immutable research record can be imported.",
                )
            status = _content_conflict_status(
                _sha256(_json_bytes(repository_record)) == item.fingerprint
            )
            return BundleConflict(
                kind=item.kind,
                object_id=item.object_id,
                status=status,
                detail=(
                    "Exact research record already exists; reuse is safe."
                    if status == "REUSE"
                    else (
                        "Same research record ID exists with different content; import is rejected."
                    )
                ),
            )

        if item.kind == "DRIFT_REPORT":
            existing_drift_report = self.drift_reports.get(item.object_id)
            if existing_drift_report is None:
                return BundleConflict(
                    kind=item.kind,
                    object_id=item.object_id,
                    status="IMPORT",
                    detail="Immutable Strategy Drift report can be imported.",
                )
            status = _content_conflict_status(
                _sha256(_json_bytes(existing_drift_report)) == item.fingerprint
            )
            return BundleConflict(
                kind=item.kind,
                object_id=item.object_id,
                status=status,
                detail=(
                    "Exact Strategy Drift report already exists; reuse is safe."
                    if status == "REUSE"
                    else (
                        "Same Strategy Drift report ID exists with different content; "
                        "import is rejected."
                    )
                ),
            )
        if item.kind == "ATTRIBUTION_REPORT":
            try:
                existing_validation = self.runs.load_validation(item.object_id)
            except KeyError:
                existing_validation = None
            if existing_validation is None:
                return BundleConflict(
                    kind=item.kind,
                    object_id=item.object_id,
                    status="IMPORT",
                    detail="Immutable Backtest/Paper attribution report can be imported.",
                )
            status = _content_conflict_status(
                _sha256(_json_bytes(existing_validation)) == item.fingerprint
            )
            return BundleConflict(
                kind=item.kind,
                object_id=item.object_id,
                status=status,
                detail=(
                    "Exact attribution report already exists; reuse is safe."
                    if status == "REUSE"
                    else (
                        "Same attribution report ID exists with different content; "
                        "import is rejected."
                    )
                ),
            )

        try:
            existing_run = self.runs.get_manifest(item.object_id)
        except KeyError:
            existing_run = None
        if existing_run is not None:
            status = _content_conflict_status(
                _sha256(_json_bytes(existing_run)) == item.fingerprint
            )
            return BundleConflict(
                kind=item.kind,
                object_id=item.object_id,
                status=status,
                detail=(
                    "Exact Run already exists; reuse is safe."
                    if status == "REUSE"
                    else "Same Run ID exists with different content; import is rejected."
                ),
            )
        run_prefix = f"objects/runs/{item.object_id}/"
        manifest = RunManifest.model_validate_json(files[item.path])
        required = {
            "strategy.py": manifest.artifacts.strategy_source_sha256,
            "trace.json": manifest.artifacts.trace_sha256,
            "diagnostics.json": manifest.artifacts.diagnostics_sha256,
            "pnl-autopsy.json": manifest.artifacts.pnl_autopsy_sha256,
            "adapter-manifest.json": manifest.artifacts.adapter_manifest_sha256,
            "market-events.jsonl": manifest.artifacts.recorded_market_events_sha256,
            "runtime-consistency.json": manifest.artifacts.runtime_consistency_sha256,
            "broker-events.jsonl": manifest.artifacts.broker_events_sha256,
        }
        missing = [
            filename
            for filename, fingerprint in required.items()
            if fingerprint is not None and f"{run_prefix}{filename}" not in files
        ]
        if missing:
            return BundleConflict(
                kind=item.kind,
                object_id=item.object_id,
                status="UNAVAILABLE",
                detail=(
                    "Run replay artifacts are incomplete; missing " + ", ".join(sorted(missing))
                ),
            )
        return BundleConflict(
            kind=item.kind,
            object_id=item.object_id,
            status="IMPORT",
            detail="Exact Run and replay artifacts can be imported.",
        )

    @staticmethod
    def _object_identity_and_fingerprint(item: BundleObject, payload: bytes) -> tuple[str, str]:
        if item.kind == "SNAPSHOT":
            snapshot = ResearchSnapshot.model_validate_json(payload)
            return snapshot.snapshot_id, snapshot.content_fingerprint
        if item.kind == "DATASET":
            dataset = DatasetDefinition.model_validate_json(payload)
            return dataset.dataset_id, dataset.content_fingerprint
        if item.kind == "UNIVERSE":
            universe = HistoricalUniverse.model_validate_json(payload)
            return universe.universe_id, _sha256(_json_bytes(universe))
        if item.kind == "CORPORATE_ACTION_DATASET":
            corporate_actions = CorporateActionDataset.model_validate_json(payload)
            return (
                corporate_actions.corporate_action_dataset_id,
                _sha256(_json_bytes(corporate_actions)),
            )
        if item.kind == "FACTOR_RESEARCH":
            factor_research = FactorResearchRecord.model_validate_json(payload)
            return factor_research.research_id, _sha256(_json_bytes(factor_research))
        if item.kind == "FACTOR_RELATIONSHIP":
            relationship = FactorRelationshipRecord.model_validate_json(payload)
            return relationship.relationship_id, _sha256(_json_bytes(relationship))
        if item.kind == "WALK_FORWARD":
            walk_forward = WalkForwardResearchRecord.model_validate_json(payload)
            return walk_forward.walk_forward_id, _sha256(_json_bytes(walk_forward))
        if item.kind == "HYPOTHESIS":
            hypothesis = ResearchHypothesis.model_validate_json(payload)
            return hypothesis.hypothesis_id, _sha256(_json_bytes(hypothesis))
        if item.kind == "PORTFOLIO_RESEARCH":
            portfolio = PortfolioResearchRecord.model_validate_json(payload)
            return portfolio.portfolio_research_id, _sha256(_json_bytes(portfolio))
        if item.kind == "RUN":
            run = RunManifest.model_validate_json(payload)
            return run.run_id, _sha256(_json_bytes(run))
        if item.kind == "DRIFT_REPORT":
            drift_report = StrategyDriftReport.model_validate_json(payload)
            return drift_report.drift_report_id, _sha256(_json_bytes(drift_report))
        attribution_report = RunValidationReport.model_validate_json(payload)
        return attribution_report.report_id, _sha256(_json_bytes(attribution_report))

    @staticmethod
    def _read(content: bytes) -> tuple[ResearchBundleManifest, dict[str, bytes]]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(content), "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise ResearchBundleError("Research Bundle is not a valid ZIP archive") from exc
        with archive:
            names = archive.namelist()
            if not names or "manifest.json" not in names:
                raise ResearchBundleError("Research Bundle manifest.json is missing")
            if len(names) != len(set(names)) or any(not _safe_member(name) for name in names):
                raise ResearchBundleError("Research Bundle contains unsafe or duplicate paths")
            try:
                manifest = ResearchBundleManifest.model_validate_json(archive.read("manifest.json"))
            except (KeyError, ValueError) as exc:
                raise ResearchBundleError(f"Research Bundle manifest is invalid: {exc}") from exc
            files = {name: archive.read(name) for name in names if name != "manifest.json"}
        if set(files) != set(manifest.checksums):
            raise ResearchBundleError("Research Bundle file inventory does not match its manifest")
        for path, expected in manifest.checksums.items():
            if _sha256(files[path]) != expected:
                raise ResearchBundleError(f"Research Bundle checksum mismatch for '{path}'")
        object_paths = [item.path for item in manifest.objects]
        if len(object_paths) != len(set(object_paths)):
            raise ResearchBundleError("Research Bundle object inventory reuses a payload path")
        object_keys = {(item.kind, item.object_id) for item in manifest.objects}
        for root in manifest.root_objects:
            if (root.kind, root.object_id) not in object_keys:
                raise ResearchBundleError(
                    f"Research Bundle root '{root.kind}:{root.object_id}' is missing from objects"
                )
        for item in manifest.objects:
            if item.path not in files:
                raise ResearchBundleError(
                    f"Research Bundle object '{item.kind}:{item.object_id}' payload is missing"
                )
            try:
                identity, fingerprint = ResearchBundleService._object_identity_and_fingerprint(
                    item, files[item.path]
                )
            except ValueError as exc:
                raise ResearchBundleError(
                    f"Research Bundle object '{item.kind}:{item.object_id}' payload is "
                    f"invalid: {exc}"
                ) from exc
            if identity != item.object_id:
                raise ResearchBundleError(
                    f"Research Bundle object '{item.kind}:{item.object_id}' payload identity "
                    f"is '{identity}'"
                )
            if fingerprint != item.fingerprint:
                raise ResearchBundleError(
                    f"Research Bundle object '{item.kind}:{item.object_id}' fingerprint does not "
                    "match its payload"
                )
        return manifest, files
