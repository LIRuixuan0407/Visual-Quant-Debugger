from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from app.adapters.models import RuntimeDescriptor, native_runtime
from app.storage import ensure_schema_version
from app.trace import BacktestTrace, trace_from_json, trace_to_json
from app.workspace import default_workspace_root

from .models import (
    AnnotationUpdate,
    RunAnnotations,
    RunArtifactAvailability,
    RunDetail,
    RunListItem,
    RunListResponse,
    RunManifest,
    RunMetrics,
    RunStatus,
    RunValidationReport,
    StrategySourceArtifact,
)

RUN_ID_PATTERN = re.compile(r"^run-[0-9a-f]{24}$")
VALIDATION_ID_PATTERN = re.compile(r"^validation-[0-9a-f]{20}$")
ArtifactName = Literal["diagnostics", "pnl-autopsy"]


class RunNotFoundError(KeyError):
    pass


class ArtifactIntegrityError(ValueError):
    pass


def sha256_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


class RunRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.vqd_root = self.workspace_root / ".vqd"
        self.runs_root = self.vqd_root / "runs"
        self.validations_root = self.vqd_root / "validations"
        self.database_path = self.vqd_root / "vqd.sqlite"
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.vqd_root.mkdir(parents=True, exist_ok=True)
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.validations_root.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            ensure_schema_version(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    run_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    strategy_id TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    strategy_fingerprint TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    dataset_name TEXT NOT NULL,
                    dataset_fingerprint TEXT NOT NULL,
                    period_start TEXT,
                    period_end TEXT,
                    research_cutoff TEXT,
                    execution_model_id TEXT NOT NULL,
                    execution_model_version TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    metrics_json TEXT,
                    trace_id TEXT,
                    run_fingerprint TEXT NOT NULL,
                    reproduced_from_run_id TEXT,
                    display_name TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            columns = {
                str(row["name"]) for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "runtime_json" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN runtime_json TEXT")
            if "run_type" not in columns:
                connection.execute(
                    "ALTER TABLE runs ADD COLUMN run_type TEXT NOT NULL DEFAULT 'BACKTEST'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_strategy_id ON runs(strategy_id)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_runs_dataset_id ON runs(dataset_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_runs_trace_id ON runs(trace_id)")
            connection.execute("PRAGMA optimize")
            connection.commit()

    def schema_version(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
        if row is None:
            raise RuntimeError("Run Ledger schema version is missing")
        return int(row["value"])

    @staticmethod
    def new_run_id() -> str:
        return f"run-{secrets.token_hex(12)}"

    def _run_directory(self, run_id: str) -> Path:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError(f"Invalid run id '{run_id}'")
        target = (self.runs_root / run_id).resolve()
        if target.parent != self.runs_root.resolve():
            raise ValueError("Run artifact path escaped the workspace")
        return target

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary = path.with_name(f"{path.name}.tmp")
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def strategy_path(self, run_id: str) -> Path:
        return self._run_directory(run_id) / "strategy.py"

    def adapter_manifest_path(self, run_id: str) -> Path:
        return self._run_directory(run_id) / "adapter-manifest.json"

    def create_running(
        self,
        manifest: RunManifest,
        strategy_source: bytes,
        adapter_manifest: bytes | None = None,
    ) -> None:
        if manifest.status != "RUNNING":
            raise ValueError("A new run must begin in RUNNING status")
        if sha256_bytes(strategy_source) != manifest.artifacts.strategy_source_sha256:
            raise ValueError("Strategy snapshot fingerprint does not match the manifest")
        target = self._run_directory(manifest.run_id)
        target.mkdir(parents=True, exist_ok=False)
        try:
            self._atomic_write(target / "strategy.py", strategy_source)
            if adapter_manifest is not None:
                if sha256_bytes(adapter_manifest) != manifest.artifacts.adapter_manifest_sha256:
                    raise ValueError("Adapter manifest fingerprint does not match the run manifest")
                self._atomic_write(target / "adapter-manifest.json", adapter_manifest)
            self._atomic_write(
                target / "manifest.json", (manifest.model_dump_json(indent=2) + "\n").encode()
            )
            with self._connection() as connection:
                self._insert_manifest(connection, manifest)
                connection.commit()
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    @staticmethod
    def _insert_manifest(connection: sqlite3.Connection, manifest: RunManifest) -> None:
        connection.execute(
            """
            INSERT INTO runs(
                run_id, run_version, status, created_at, completed_at,
                strategy_id, strategy_name, strategy_fingerprint,
                dataset_id, dataset_name, dataset_fingerprint,
                period_start, period_end, research_cutoff,
                execution_model_id, execution_model_version,
                parameters_json, metrics_json, trace_id, run_fingerprint,
                reproduced_from_run_id, runtime_json
                , run_type
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest.run_id,
                manifest.run_version,
                manifest.status,
                manifest.created_at.isoformat(),
                None,
                manifest.strategy.strategy_id,
                manifest.strategy.name,
                manifest.strategy.source_fingerprint,
                manifest.dataset.dataset_id,
                manifest.dataset.name,
                manifest.dataset.content_fingerprint,
                None,
                None,
                None if manifest.period.cutoff is None else manifest.period.cutoff.isoformat(),
                manifest.execution_model.execution_model_id,
                manifest.execution_model.version,
                _json(manifest.parameters),
                None,
                None,
                manifest.run_fingerprint,
                manifest.reproduced_from_run_id,
                _json(manifest.runtime.model_dump(mode="json")),
                manifest.run_type,
            ),
        )

    def finalize(self, manifest: RunManifest, trace: BacktestTrace | None) -> None:
        if manifest.status == "RUNNING":
            raise ValueError("A finalized run cannot remain RUNNING")
        target = self._run_directory(manifest.run_id)
        if trace is not None:
            payload = (trace_to_json(trace) + "\n").encode()
            if sha256_bytes(payload) != manifest.artifacts.trace_sha256:
                raise ValueError("Trace fingerprint does not match the manifest")
            self._atomic_write(target / "trace.json", payload)
        self._atomic_write(
            target / "manifest.json", (manifest.model_dump_json(indent=2) + "\n").encode()
        )
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE runs SET
                    status = ?, completed_at = ?, period_start = ?, period_end = ?,
                    metrics_json = ?, trace_id = ?, runtime_json = ?
                WHERE run_id = ? AND status = 'RUNNING'
                """,
                (
                    manifest.status,
                    None if manifest.completed_at is None else manifest.completed_at.isoformat(),
                    None if manifest.period.start is None else manifest.period.start.isoformat(),
                    None if manifest.period.end is None else manifest.period.end.isoformat(),
                    None
                    if manifest.metrics is None
                    else _json(manifest.metrics.model_dump(mode="json")),
                    manifest.trace_id,
                    _json(manifest.runtime.model_dump(mode="json")),
                    manifest.run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Run '{manifest.run_id}' was not in RUNNING state")
            connection.commit()

    def import_completed(
        self,
        manifest: RunManifest,
        artifacts: dict[str, bytes],
        *,
        annotations_json: bytes | None = None,
    ) -> RunManifest:
        if manifest.status == "RUNNING":
            raise ArtifactIntegrityError("Portable import requires a finalized Run")
        if self._exists(manifest.run_id):
            existing = self.get_manifest(manifest.run_id)
            if existing == manifest:
                return existing
            raise ArtifactIntegrityError(
                f"Run '{manifest.run_id}' already exists with different content"
            )

        expected = {
            "strategy.py": manifest.artifacts.strategy_source_sha256,
            "trace.json": manifest.artifacts.trace_sha256,
            "diagnostics.json": manifest.artifacts.diagnostics_sha256,
            "pnl-autopsy.json": manifest.artifacts.pnl_autopsy_sha256,
            "adapter-manifest.json": manifest.artifacts.adapter_manifest_sha256,
            "market-events.jsonl": manifest.artifacts.recorded_market_events_sha256,
            "runtime-consistency.json": manifest.artifacts.runtime_consistency_sha256,
            "broker-events.jsonl": manifest.artifacts.broker_events_sha256,
        }
        for filename, fingerprint in expected.items():
            if fingerprint is None:
                continue
            payload = artifacts.get(filename)
            if payload is None:
                raise ArtifactIntegrityError(
                    f"Portable Run '{manifest.run_id}' is missing '{filename}'"
                )
            if sha256_bytes(payload) != fingerprint:
                raise ArtifactIntegrityError(
                    f"Portable Run '{manifest.run_id}' has a hash mismatch for '{filename}'"
                )

        target = self._run_directory(manifest.run_id)
        target.mkdir(parents=True, exist_ok=False)
        try:
            for filename, payload in artifacts.items():
                if filename not in expected:
                    continue
                self._atomic_write(target / filename, payload)
            self._atomic_write(
                target / "manifest.json", (manifest.model_dump_json(indent=2) + "\n").encode()
            )
            with self._connection() as connection:
                self._insert_manifest(connection, manifest)
                connection.execute(
                    """
                    UPDATE runs SET
                        completed_at = ?, period_start = ?, period_end = ?,
                        metrics_json = ?, trace_id = ?, runtime_json = ?
                    WHERE run_id = ?
                    """,
                    (
                        (
                            None
                            if manifest.completed_at is None
                            else manifest.completed_at.isoformat()
                        ),
                        (
                            None
                            if manifest.period.start is None
                            else manifest.period.start.isoformat()
                        ),
                        None if manifest.period.end is None else manifest.period.end.isoformat(),
                        None
                        if manifest.metrics is None
                        else _json(manifest.metrics.model_dump(mode="json")),
                        manifest.trace_id,
                        _json(manifest.runtime.model_dump(mode="json")),
                        manifest.run_id,
                    ),
                )
                connection.commit()
            self._verify_artifacts(manifest)
            if annotations_json is not None:
                annotations = RunAnnotations.model_validate_json(annotations_json)
                self.update_annotations(
                    manifest.run_id,
                    AnnotationUpdate(
                        display_name=annotations.display_name,
                        note=annotations.note,
                        tags=annotations.tags,
                    ),
                )
        except Exception:
            with self._connection() as connection:
                connection.execute("DELETE FROM runs WHERE run_id = ?", (manifest.run_id,))
                connection.commit()
            shutil.rmtree(target, ignore_errors=True)
            raise
        return manifest

    def _manifest_path(self, run_id: str) -> Path:
        return self._run_directory(run_id) / "manifest.json"

    def _exists(self, run_id: str) -> bool:
        self._run_directory(run_id)
        with self._connection() as connection:
            return (
                connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                is not None
            )

    def get_manifest(self, run_id: str, *, verify: bool = True) -> RunManifest:
        if not self._exists(run_id):
            raise RunNotFoundError(run_id)
        path = self._manifest_path(run_id)
        if not path.is_file():
            raise ArtifactIntegrityError(f"Run '{run_id}' manifest is missing")
        try:
            manifest = RunManifest.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ArtifactIntegrityError(f"Run '{run_id}' manifest is invalid: {exc}") from exc
        if manifest.run_id != run_id:
            raise ArtifactIntegrityError(f"Run '{run_id}' manifest identity does not match")
        if verify:
            self._verify_artifacts(manifest)
        return manifest

    def _verify_artifacts(self, manifest: RunManifest) -> None:
        target = self._run_directory(manifest.run_id)
        expected = {
            "strategy.py": manifest.artifacts.strategy_source_sha256,
            "trace.json": manifest.artifacts.trace_sha256,
            "diagnostics.json": manifest.artifacts.diagnostics_sha256,
            "pnl-autopsy.json": manifest.artifacts.pnl_autopsy_sha256,
            "adapter-manifest.json": manifest.artifacts.adapter_manifest_sha256,
            "market-events.jsonl": manifest.artifacts.recorded_market_events_sha256,
            "runtime-consistency.json": manifest.artifacts.runtime_consistency_sha256,
            "broker-events.jsonl": manifest.artifacts.broker_events_sha256,
        }
        for filename, fingerprint in expected.items():
            if fingerprint is None:
                continue
            path = target / filename
            if not path.is_file():
                raise ArtifactIntegrityError(
                    f"Artifact integrity check failed: {filename} is missing for {manifest.run_id}"
                )
            actual = sha256_bytes(path.read_bytes())
            if actual != fingerprint:
                raise ArtifactIntegrityError(
                    f"Artifact integrity check failed: {filename} hash mismatch for "
                    f"{manifest.run_id}"
                )

    def load_trace_for_run(self, run_id: str) -> BacktestTrace:
        manifest = self.get_manifest(run_id)
        if manifest.trace_id is None or manifest.artifacts.trace_sha256 is None:
            raise RunNotFoundError(f"Run '{run_id}' has no trace")
        return trace_from_json((self._run_directory(run_id) / "trace.json").read_bytes())

    def run_id_for_trace(self, trace_id: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT run_id FROM runs WHERE trace_id = ? ORDER BY created_at DESC LIMIT 1",
                (trace_id,),
            ).fetchone()
        return None if row is None else str(row["run_id"])

    def load_trace(self, trace_id: str) -> BacktestTrace | None:
        run_id = self.run_id_for_trace(trace_id)
        return None if run_id is None else self.load_trace_for_run(run_id)

    @staticmethod
    def _annotations(row: sqlite3.Row) -> RunAnnotations:
        return RunAnnotations(
            display_name=str(row["display_name"]),
            note=str(row["note"]),
            tags=tuple(json.loads(row["tags_json"])),
        )

    @staticmethod
    def _list_item(row: sqlite3.Row) -> RunListItem:
        from .models import ResearchPeriod

        metrics_payload = row["metrics_json"]
        return RunListItem(
            run_id=str(row["run_id"]),
            run_type=cast(Literal["BACKTEST", "PAPER"], str(row["run_type"])),
            trace_id=None if row["trace_id"] is None else str(row["trace_id"]),
            status=cast(RunStatus, str(row["status"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            completed_at=None
            if row["completed_at"] is None
            else datetime.fromisoformat(str(row["completed_at"])),
            strategy_id=str(row["strategy_id"]),
            strategy_name=str(row["strategy_name"]),
            strategy_fingerprint=str(row["strategy_fingerprint"]),
            dataset_id=str(row["dataset_id"]),
            dataset_name=str(row["dataset_name"]),
            dataset_fingerprint=str(row["dataset_fingerprint"]),
            parameters=json.loads(row["parameters_json"]),
            period=ResearchPeriod(
                start=None
                if row["period_start"] is None
                else datetime.fromisoformat(str(row["period_start"])),
                end=None
                if row["period_end"] is None
                else datetime.fromisoformat(str(row["period_end"])),
                cutoff=None
                if row["research_cutoff"] is None
                else datetime.fromisoformat(str(row["research_cutoff"])),
            ),
            metrics=None
            if metrics_payload is None
            else RunMetrics.model_validate_json(metrics_payload),
            run_fingerprint=str(row["run_fingerprint"]),
            reproduced_from_run_id=None
            if row["reproduced_from_run_id"] is None
            else str(row["reproduced_from_run_id"]),
            annotations=RunRepository._annotations(row),
            runtime=(
                native_runtime()
                if row["runtime_json"] is None
                else RuntimeDescriptor.model_validate_json(row["runtime_json"])
            ),
        )

    def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        strategy_id: str | None = None,
        dataset_id: str | None = None,
        status: RunStatus | None = None,
        search: str | None = None,
        run_ids: tuple[str, ...] | None = None,
    ) -> RunListResponse:
        predicates: list[str] = []
        values: list[object] = []
        if strategy_id:
            predicates.append("strategy_id = ?")
            values.append(strategy_id)
        if dataset_id:
            predicates.append("dataset_id = ?")
            values.append(dataset_id)
        if status:
            predicates.append("status = ?")
            values.append(status)
        if search:
            predicates.append("(run_id LIKE ? OR display_name LIKE ? OR tags_json LIKE ?)")
            term = f"%{search}%"
            values.extend((term, term, term))
        if run_ids is not None:
            if not run_ids:
                return RunListResponse(items=(), total=0, limit=limit, offset=offset)
            placeholders = ",".join("?" for _ in run_ids)
            predicates.append(f"run_id IN ({placeholders})")
            values.extend(run_ids)
        where = "" if not predicates else " WHERE " + " AND ".join(predicates)
        with self._connection() as connection:
            total = int(
                connection.execute(f"SELECT COUNT(*) AS count FROM runs{where}", values).fetchone()[
                    "count"
                ]
            )
            rows = connection.execute(
                f"SELECT * FROM runs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
        return RunListResponse(
            items=tuple(self._list_item(row) for row in rows),
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_annotations(self, run_id: str) -> RunAnnotations:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return self._annotations(row)

    def update_annotations(self, run_id: str, update: AnnotationUpdate) -> RunAnnotations:
        self._run_directory(run_id)
        tags = tuple(dict.fromkeys(tag.strip() for tag in update.tags if tag.strip()))
        if len(tags) > 20 or any(len(tag) > 64 for tag in tags):
            raise ValueError("Use at most 20 tags of 64 characters or fewer")
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE runs SET display_name = ?, note = ?, tags_json = ? WHERE run_id = ?",
                (update.display_name.strip(), update.note, _json(tags), run_id),
            )
            if cursor.rowcount != 1:
                raise RunNotFoundError(run_id)
            connection.commit()
        return RunAnnotations(display_name=update.display_name.strip(), note=update.note, tags=tags)

    def artifact_availability(self, run_id: str) -> RunArtifactAvailability:
        manifest = self.get_manifest(run_id)
        target = self._run_directory(run_id)
        return RunArtifactAvailability(
            strategy_source=(target / "strategy.py").is_file(),
            trace=manifest.artifacts.trace_sha256 is not None,
            diagnostics=manifest.artifacts.diagnostics_sha256 is not None,
            pnl_autopsy=manifest.artifacts.pnl_autopsy_sha256 is not None,
            adapter_manifest=manifest.artifacts.adapter_manifest_sha256 is not None,
            recorded_market_events=manifest.artifacts.recorded_market_events_sha256 is not None,
            runtime_consistency=manifest.artifacts.runtime_consistency_sha256 is not None,
            broker_events=manifest.artifacts.broker_events_sha256 is not None,
        )

    def write_paper_artifacts(
        self,
        run_id: str,
        market_events: bytes,
        runtime_consistency: bytes,
        broker_events: bytes | None = None,
    ) -> None:
        target = self._run_directory(run_id)
        self._atomic_write(target / "market-events.jsonl", market_events)
        self._atomic_write(target / "runtime-consistency.json", runtime_consistency)
        if broker_events is not None:
            self._atomic_write(target / "broker-events.jsonl", broker_events)

    def detail(self, run_id: str) -> RunDetail:
        return RunDetail(
            manifest=self.get_manifest(run_id),
            annotations=self.get_annotations(run_id),
            artifacts=self.artifact_availability(run_id),
        )

    def _validation_path(self, report_id: str) -> Path:
        if not VALIDATION_ID_PATTERN.fullmatch(report_id):
            raise ValueError(f"Invalid validation report id '{report_id}'")
        target = (self.validations_root / f"{report_id}.json").resolve()
        if target.parent != self.validations_root.resolve():
            raise ValueError("Validation report path escaped the workspace")
        return target

    def save_validation(self, report: RunValidationReport) -> None:
        path = self._validation_path(report.report_id)
        payload = (report.model_dump_json(indent=2) + "\n").encode()
        if path.is_file():
            existing = path.read_bytes()
            if existing != payload:
                raise ArtifactIntegrityError(
                    f"Validation report '{report.report_id}' is immutable and already exists"
                )
            return
        self._atomic_write(path, payload)

    def load_validation(self, report_id: str) -> RunValidationReport:
        path = self._validation_path(report_id)
        if not path.is_file():
            raise RunNotFoundError(report_id)
        try:
            return RunValidationReport.model_validate_json(path.read_bytes())
        except ValueError as exc:
            raise ArtifactIntegrityError(f"Validation report '{report_id}' is not valid") from exc

    def list_validations(self) -> tuple[RunValidationReport, ...]:
        if not self.validations_root.exists():
            return ()
        return tuple(
            self.load_validation(path.stem)
            for path in sorted(self.validations_root.glob("validation-*.json"))
        )

    def strategy_source(self, run_id: str) -> StrategySourceArtifact:
        manifest = self.get_manifest(run_id)
        path = self.strategy_path(run_id)
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactIntegrityError("Strategy snapshot is not valid UTF-8") from exc
        return StrategySourceArtifact(
            run_id=run_id,
            sha256=manifest.artifacts.strategy_source_sha256,
            source=source,
        )

    @staticmethod
    def _derived_filename(name: ArtifactName) -> str:
        return "diagnostics.json" if name == "diagnostics" else "pnl-autopsy.json"

    def load_derived(self, run_id: str, name: ArtifactName) -> bytes | None:
        manifest = self.get_manifest(run_id)
        fingerprint = (
            manifest.artifacts.diagnostics_sha256
            if name == "diagnostics"
            else manifest.artifacts.pnl_autopsy_sha256
        )
        if fingerprint is None:
            return None
        return (self._run_directory(run_id) / self._derived_filename(name)).read_bytes()

    def save_derived(self, run_id: str, name: ArtifactName, content: bytes) -> None:
        manifest = self.get_manifest(run_id)
        fingerprint = sha256_bytes(content)
        target = self._run_directory(run_id)
        self._atomic_write(target / self._derived_filename(name), content)
        hashes = manifest.artifacts.model_copy(
            update={
                "diagnostics_sha256" if name == "diagnostics" else "pnl_autopsy_sha256": fingerprint
            }
        )
        updated = manifest.model_copy(update={"artifacts": hashes})
        self._atomic_write(
            target / "manifest.json", (updated.model_dump_json(indent=2) + "\n").encode()
        )

    def delete(self, run_id: str) -> None:
        target = self._run_directory(run_id)
        if not self._exists(run_id):
            raise RunNotFoundError(run_id)
        staged = target.with_name(f".delete-{run_id}")
        if target.exists():
            target.replace(staged)
        try:
            with self._connection() as connection:
                connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
                connection.commit()
        except Exception:
            if staged.exists():
                staged.replace(target)
            raise
        if staged.exists():
            shutil.rmtree(staged)


class RunStore:
    """Shared repository handle whose workspace can be isolated by tests."""

    def __init__(self) -> None:
        self.repository = RunRepository()

    def use_workspace(self, workspace_root: str | Path) -> RunRepository:
        self.repository = RunRepository(workspace_root)
        return self.repository


run_store = RunStore()
