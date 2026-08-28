from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.adapters.models import (
    AdapterDataset,
    AdapterMarketPoint,
    AdapterRunRequest,
    AdapterRunResult,
    AdapterStrategyManifest,
    RuntimeDescriptor,
)
from app.adapters.registry import adapter_registry
from app.adapters.runner import FrameworkRunError, FrameworkRunner
from app.adapters.trace_builder import build_adapter_trace
from app.adapters.validation import validate_parameters
from app.backtest import BacktestParameters
from app.corporate_actions import CorporateActionRepository, CorporateActionService
from app.corporate_actions.models import PriceAdjustmentPolicy
from app.datasets import DatasetRegistry, dataset_registry
from app.sdk.loader import LoadedStrategy, load_strategy, source_fingerprint
from app.sdk.models import RuntimeFailure
from app.sdk.registry import StrategyRegistration, StrategyRegistry, strategy_registry
from app.trace import BacktestTrace, trace_to_json
from app.universes import UniverseRepository

from .engine import OpenRunResult, execute_open_run
from .models import (
    ArtifactHashes,
    BacktestRunRecord,
    DatasetRevision,
    EnvironmentSnapshot,
    ExecutionModelRevision,
    ResearchPeriod,
    RunDetail,
    RunManifest,
    RunMetrics,
    StrategyRevision,
)
from .repository import RunRepository, run_store, sha256_bytes

VQD_ENGINE_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class PersistedRunResult:
    manifest: RunManifest
    trace: BacktestTrace | None
    engine_result: OpenRunResult | None
    adapter_result: AdapterRunResult | None = None


def _canonical_fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _trace_payload(trace: BacktestTrace) -> bytes:
    return (trace_to_json(trace) + "\n").encode()


def _run_metrics(trace: BacktestTrace) -> RunMetrics:
    metrics = trace.metrics
    final_equity = trace.timeline[-1].pnl_snapshot.equity
    return RunMetrics(
        total_return=float(metrics.get("total_return", 0.0)),
        sharpe=float(metrics.get("sharpe", 0.0)),
        max_drawdown=float(metrics.get("max_drawdown", 0.0)),
        turnover=float(metrics.get("turnover", 0.0)),
        trades=len(trace.trades),
        final_equity=final_equity,
        fees=float(metrics.get("total_fees", trace.timeline[-1].cost_snapshot.cumulative_fees)),
        slippage=float(
            metrics.get("total_slippage", trace.timeline[-1].cost_snapshot.cumulative_slippage)
        ),
        net_pnl=float(metrics.get("net_pnl", trace.timeline[-1].pnl_snapshot.cumulative_net_pnl)),
    )


def _trace_id(run_id: str, trace: BacktestTrace) -> str:
    semantic = trace_to_json(trace, indent=None)
    identity = f"{run_id}\n{semantic}".encode()
    return f"trace-{hashlib.sha256(identity).hexdigest()[:16]}"


class RunLedger:
    @property
    def repository(self) -> RunRepository:
        return run_store.repository

    @staticmethod
    def _normalized_parameters(
        loaded: LoadedStrategy, supplied: dict[str, int | float]
    ) -> dict[str, int | float]:
        strategy = loaded.strategy_class()
        values = {item.name: item.default for item in strategy.parameter_definitions()}
        values.update(supplied)
        values.setdefault("initial_cash", 100_000.0)
        values.setdefault("fee_bps", 5.0)
        values.setdefault("slippage_bps", 5.0)
        if strategy.metadata.strategy_id == "pairs-trading":
            values.setdefault("gross_target", 20_000.0)
        return values

    def create(
        self,
        *,
        strategy_id: str,
        dataset_id: str,
        parameters: dict[str, int | float],
        research_cutoff: datetime | None,
        strategy_registry_override: StrategyRegistry | None = None,
        dataset_registry_override: DatasetRegistry | None = None,
        source_path: Path | None = None,
        class_name: str | None = None,
        reproduced_from_run_id: str | None = None,
        adapter_manifest_override: AdapterStrategyManifest | None = None,
        universe_id: str | None = None,
        corporate_action_dataset_id: str | None = None,
        price_adjustment_policy: PriceAdjustmentPolicy = "RAW",
    ) -> PersistedRunResult:
        strategies = strategy_registry_override or strategy_registry
        datasets = dataset_registry_override or dataset_registry
        registration = strategies.get_registration(strategy_id)
        if registration is not None and registration.runtime_kind == "framework":
            if universe_id is not None or corporate_action_dataset_id is not None:
                raise ValueError(
                    "Historical Universe and Corporate Action references are not supported "
                    "by framework adapter runs"
                )
            return self._create_framework(
                registration=registration,
                dataset_id=dataset_id,
                parameters=parameters,
                research_cutoff=research_cutoff,
                datasets=datasets,
                source_path=source_path,
                reproduced_from_run_id=reproduced_from_run_id,
                manifest_override=adapter_manifest_override,
            )
        initial_loaded = (
            load_strategy(source_path, class_name)
            if source_path is not None
            else strategies.load(strategy_id)
        )
        if initial_loaded.strategy_class.metadata.strategy_id != strategy_id:
            raise ValueError("Strategy snapshot id does not match the requested strategy")
        definition = datasets.get(dataset_id)
        if definition is None:
            raise KeyError(f"Dataset '{dataset_id}' was not found")
        if price_adjustment_policy == "SPLIT_ADJUSTED" and corporate_action_dataset_id is None:
            raise ValueError("SPLIT_ADJUSTED runs require a Corporate Action dataset")
        universes = UniverseRepository(datasets.workspace_root)
        universe = None if universe_id is None else universes.get(universe_id)
        if universe_id is not None and universe is None:
            raise KeyError(f"Universe '{universe_id}' was not found")
        if universe is not None and universe.dataset_id not in (None, dataset_id):
            raise ValueError("Historical Universe references another Market Dataset")
        corporate_action_repository = CorporateActionRepository(datasets.workspace_root)
        corporate_action_service = CorporateActionService(
            corporate_action_repository,
            datasets,
        )
        corporate_action_dataset = (
            None
            if corporate_action_dataset_id is None
            else corporate_action_repository.get(corporate_action_dataset_id)
        )
        if corporate_action_dataset_id is not None and corporate_action_dataset is None:
            raise KeyError(
                f"Corporate Action dataset '{corporate_action_dataset_id}' was not found"
            )
        adjusted_frames = corporate_action_service.adjusted_frames(
            dataset_id,
            corporate_action_dataset_id,
            price_adjustment_policy,
        )
        values = self._normalized_parameters(initial_loaded, parameters)
        source_bytes = initial_loaded.source_path.read_bytes()
        source_hash = sha256_bytes(source_bytes)
        metadata = initial_loaded.strategy_class.metadata
        run_id = self.repository.new_run_id()
        created_at = datetime.now(UTC)
        execution_model = ExecutionModelRevision()
        runtime = RuntimeDescriptor()
        run_fingerprint = _canonical_fingerprint(
            {
                "strategy_fingerprint": source_hash,
                "dataset_fingerprint": definition.content_fingerprint,
                "universe_id": universe_id,
                "corporate_action_dataset_id": corporate_action_dataset_id,
                "corporate_action_fingerprint": (
                    None
                    if corporate_action_dataset is None
                    else corporate_action_dataset.content_fingerprint
                ),
                "price_adjustment_policy": price_adjustment_policy,
                "research_cutoff": None if research_cutoff is None else research_cutoff.isoformat(),
                "parameters": values,
                "execution_model_id": execution_model.execution_model_id,
                "execution_model_version": execution_model.version,
                "runtime_kind": runtime.kind,
                "adapter_id": runtime.adapter_id,
                "adapter_version": runtime.adapter_version,
                "framework_name": runtime.framework_name,
                "framework_version": runtime.framework_version,
                "engine_version": VQD_ENGINE_VERSION,
            }
        )
        running = RunManifest(
            run_id=run_id,
            run_fingerprint=run_fingerprint,
            status="RUNNING",
            created_at=created_at,
            completed_at=None,
            strategy=StrategyRevision(
                strategy_id=strategy_id,
                name=metadata.name,
                version=metadata.version,
                class_name=initial_loaded.strategy_class.__name__,
                source_fingerprint=source_hash,
                original_source_path=str(initial_loaded.source_path),
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
            universe_id=universe_id,
            corporate_action_dataset_id=corporate_action_dataset_id,
            price_adjustment_policy=price_adjustment_policy,
            period=ResearchPeriod(start=None, end=None, cutoff=research_cutoff),
            parameters=values,
            execution_model=execution_model,
            runtime=runtime,
            engine=EnvironmentSnapshot(
                python_version=platform.python_version(),
                platform=platform.platform(),
                vqd_version=VQD_ENGINE_VERSION,
            ),
            artifacts=ArtifactHashes(strategy_source_sha256=source_hash),
            reproduced_from_run_id=reproduced_from_run_id,
        )
        self.repository.create_running(running, source_bytes)
        snapshot_path = self.repository.strategy_path(run_id)
        try:
            snapshot_loaded = load_strategy(snapshot_path, running.strategy.class_name)
            result = execute_open_run(
                strategy_id=strategy_id,
                dataset_id=dataset_id,
                parameters=values,
                research_cutoff=research_cutoff,
                strategy_registry=strategies,
                dataset_registry=datasets,
                loaded_strategy=snapshot_loaded,
                frames_override=adjusted_frames,
                corporate_actions=(
                    () if corporate_action_dataset is None else corporate_action_dataset.actions
                ),
                price_adjustment_policy=price_adjustment_policy,
                historical_universe=universe,
            )
        except Exception as exc:
            timestamp = definition.start_time
            failure = RuntimeFailure(
                strategy_id=strategy_id,
                timestamp=timestamp,
                event_index=0,
                exception_type=type(exc).__name__,
                message=str(exc),
                traceback="",
            )
            failed = running.model_copy(
                update={
                    "status": "FAILED",
                    "completed_at": datetime.now(UTC),
                    "failure": failure,
                }
            )
            self.repository.finalize(failed, None)
            raise
        trace = result.trace
        status = (
            "FAILED" if trace is None else "PARTIAL" if result.status == "PARTIAL" else "COMPLETED"
        )
        trace_payload = None if trace is None else _trace_payload(trace)
        completed = running.model_copy(
            update={
                "status": status,
                "completed_at": datetime.now(UTC),
                "period": ResearchPeriod(
                    start=None if not result.frames else result.frames[0].timestamp,
                    end=None if not result.frames else result.frames[-1].timestamp,
                    cutoff=research_cutoff,
                ),
                "trace_id": None if trace is None else _trace_id(run_id, trace),
                "metrics": None if trace is None else _run_metrics(trace),
                "artifacts": running.artifacts.model_copy(
                    update={
                        "trace_sha256": None
                        if trace_payload is None
                        else sha256_bytes(trace_payload)
                    }
                ),
                "failure": result.failure,
                "unresolved_corporate_action_ids": (
                    ()
                    if trace is None
                    else tuple(
                        item.action_id
                        for item in trace.corporate_action_events
                        if item.status == "UNRESOLVED"
                    )
                ),
            }
        )
        self.repository.finalize(completed, trace)
        return PersistedRunResult(completed, trace, result)

    def _create_framework(
        self,
        *,
        registration: StrategyRegistration,
        dataset_id: str,
        parameters: dict[str, int | float],
        research_cutoff: datetime | None,
        datasets: DatasetRegistry,
        source_path: Path | None,
        reproduced_from_run_id: str | None,
        manifest_override: AdapterStrategyManifest | None,
    ) -> PersistedRunResult:
        registered = registration
        manifest = manifest_override or registered.adapter_manifest
        if manifest is None or registered.adapter_id is None or registered.adapter_version is None:
            raise ValueError("Framework registration is missing adapter metadata")
        definition = datasets.get(dataset_id)
        if definition is None:
            raise KeyError(f"Dataset '{dataset_id}' was not found")
        values = validate_parameters(parameters, manifest)
        frames = datasets.load_frames(dataset_id)
        if research_cutoff is not None:
            if research_cutoff.tzinfo is None or research_cutoff.utcoffset() is None:
                raise ValueError("research_cutoff must be timezone-aware")
            frames = tuple(frame for frame in frames if frame.timestamp <= research_cutoff)
        adapter_dataset = AdapterDataset(
            dataset_id=definition.dataset_id,
            name=definition.name,
            revision=definition.content_fingerprint,
            symbols=tuple(sorted(set.intersection(*(set(frame.symbols) for frame in frames)))),
            fields=definition.fields,
            points=tuple(
                AdapterMarketPoint(
                    timestamp=frame.timestamp,
                    values={symbol: dict(frame.values[symbol]) for symbol in sorted(frame.symbols)},
                )
                for frame in frames
            ),
        )
        actual_source = Path(source_path or registered.source_path).expanduser().resolve()
        source_bytes = actual_source.read_bytes()
        source_hash = sha256_bytes(source_bytes)
        adapter_manifest_bytes = (manifest.model_dump_json(indent=2) + "\n").encode()
        framework_version = adapter_registry.installed_version(registered.adapter_id)
        runtime = RuntimeDescriptor(
            kind="framework",
            adapter_id=registered.adapter_id,
            adapter_version=registered.adapter_version,
            framework_name=registered.framework_name or registered.adapter_id,
            framework_version=framework_version or registered.framework_version,
            execution_owner=registered.framework_name or registered.adapter_id,
            trace_fidelity="BASIC",
            determinism="SEEDED" if manifest.random_seed is not None else "UNVERIFIED",
            random_seed=manifest.random_seed,
            python_executable=sys.executable,
            historical_research_only=True,
        )
        execution_model = ExecutionModelRevision(
            execution_model_id=f"framework:{registered.adapter_id}",
            version=registered.adapter_version,
            description=f"Execution and accounting owned by {runtime.execution_owner}",
        )
        run_id = self.repository.new_run_id()
        created_at = datetime.now(UTC)
        run_fingerprint = _canonical_fingerprint(
            {
                "strategy_fingerprint": source_hash,
                "dataset_fingerprint": definition.content_fingerprint,
                "research_cutoff": None if research_cutoff is None else research_cutoff.isoformat(),
                "parameters": values,
                "runtime_kind": runtime.kind,
                "adapter_id": runtime.adapter_id,
                "adapter_version": runtime.adapter_version,
                "framework_name": runtime.framework_name,
                "framework_version": runtime.framework_version,
                "execution_owner": runtime.execution_owner,
                "execution_config": manifest.execution_config,
                "engine_version": VQD_ENGINE_VERSION,
            }
        )
        running = RunManifest(
            run_id=run_id,
            run_fingerprint=run_fingerprint,
            status="RUNNING",
            created_at=created_at,
            completed_at=None,
            strategy=StrategyRevision(
                strategy_id=manifest.strategy_id,
                name=manifest.name,
                version=manifest.version,
                class_name=registered.class_name,
                source_fingerprint=source_hash,
                original_source_path=str(actual_source),
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
            period=ResearchPeriod(start=None, end=None, cutoff=research_cutoff),
            parameters=values,
            execution_model=execution_model,
            runtime=runtime,
            engine=EnvironmentSnapshot(
                python_version=platform.python_version(),
                platform=platform.platform(),
                vqd_version=VQD_ENGINE_VERSION,
            ),
            artifacts=ArtifactHashes(
                strategy_source_sha256=source_hash,
                adapter_manifest_sha256=sha256_bytes(adapter_manifest_bytes),
            ),
            reproduced_from_run_id=reproduced_from_run_id,
        )
        self.repository.create_running(running, source_bytes, adapter_manifest_bytes)
        request = AdapterRunRequest(
            adapter_id=registered.adapter_id,
            source_path=str(self.repository.strategy_path(run_id)),
            entrypoint=registered.class_name,
            manifest=manifest,
            dataset=adapter_dataset,
            parameters=values,
            research_cutoff=research_cutoff,
        )
        try:
            adapter_result = FrameworkRunner().execute(request)
            trace = build_adapter_trace(adapter_result, definition.name)
        except FrameworkRunError as exc:
            failed = running.model_copy(
                update={
                    "status": "FAILED",
                    "completed_at": datetime.now(UTC),
                    "failure": RuntimeFailure(
                        strategy_id=registered.strategy_id,
                        timestamp=definition.start_time,
                        event_index=0,
                        exception_type=exc.code,
                        message=exc.summary,
                        traceback="\n".join(item for item in (exc.stderr, exc.traceback) if item),
                    ),
                }
            )
            self.repository.finalize(failed, None)
            return PersistedRunResult(failed, None, None, None)
        trace_payload = _trace_payload(trace)
        completed_runtime = trace.metadata.runtime.model_copy(
            update={"python_executable": sys.executable}
        )
        completed = running.model_copy(
            update={
                "status": "COMPLETED",
                "completed_at": datetime.now(UTC),
                "period": ResearchPeriod(
                    start=trace.metadata.data_start,
                    end=trace.metadata.data_end,
                    cutoff=research_cutoff,
                ),
                "runtime": completed_runtime,
                "trace_id": _trace_id(run_id, trace),
                "metrics": _run_metrics(trace),
                "artifacts": running.artifacts.model_copy(
                    update={"trace_sha256": sha256_bytes(trace_payload)}
                ),
            }
        )
        self.repository.finalize(completed, trace)
        return PersistedRunResult(completed, trace, None, adapter_result)

    def reproduce(
        self,
        run_id: str,
        *,
        strategy_registry_override: StrategyRegistry | None = None,
        dataset_registry_override: DatasetRegistry | None = None,
    ) -> PersistedRunResult:
        manifest = self.repository.get_manifest(run_id)
        datasets = dataset_registry_override or dataset_registry
        current_dataset = datasets.get(manifest.dataset.dataset_id)
        if current_dataset is None:
            raise ValueError("The referenced dataset revision is no longer available")
        if current_dataset.content_fingerprint != manifest.dataset.content_fingerprint:
            raise ValueError("Dataset fingerprint no longer matches the saved run revision")
        if manifest.runtime.kind == "framework":
            adapter_id = manifest.runtime.adapter_id
            if adapter_id is None:
                raise ValueError("Saved framework run is missing its adapter identity")
            current_version = adapter_registry.installed_version(adapter_id)
            if current_version != manifest.runtime.framework_version:
                raise ValueError(
                    "Current framework version differs from original run. "
                    f"Original: {manifest.runtime.framework_version or 'unknown'}; "
                    f"Current: {current_version or 'not installed'}. "
                    "Exact environment reproduction is unavailable."
                )
            strategies = strategy_registry_override or strategy_registry
            registration = strategies.get_registration(manifest.strategy.strategy_id)
            if registration is None or registration.runtime_kind != "framework":
                raise ValueError("The saved framework strategy is not currently registered")
            if (
                registration.adapter_id != manifest.runtime.adapter_id
                or registration.adapter_version != manifest.runtime.adapter_version
            ):
                raise ValueError("Current adapter identity differs from the original run")
            saved_adapter_manifest = AdapterStrategyManifest.model_validate_json(
                self.repository.adapter_manifest_path(run_id).read_text()
            )
        else:
            saved_adapter_manifest = None
        return self.create(
            strategy_id=manifest.strategy.strategy_id,
            dataset_id=manifest.dataset.dataset_id,
            parameters=manifest.parameters,
            research_cutoff=manifest.period.cutoff,
            strategy_registry_override=strategy_registry_override,
            dataset_registry_override=datasets,
            source_path=self.repository.strategy_path(run_id),
            class_name=manifest.strategy.class_name,
            reproduced_from_run_id=run_id,
            adapter_manifest_override=saved_adapter_manifest,
            universe_id=manifest.universe_id,
            corporate_action_dataset_id=manifest.corporate_action_dataset_id,
            price_adjustment_policy=manifest.price_adjustment_policy,
        )

    def execution_record(self, trace_id: str) -> BacktestRunRecord | None:
        run_id = self.repository.run_id_for_trace(trace_id)
        if run_id is None:
            return None
        manifest = self.repository.get_manifest(run_id)
        trace = self.repository.load_trace_for_run(run_id)
        dataset_source = str(
            Path(__file__).parents[3] / "sample_data" / "pairs_daily.csv"
            if manifest.dataset.dataset_id == "pairs-sample-v1"
            else self.repository.workspace_root
            / ".vqd"
            / "datasets"
            / manifest.dataset.dataset_id
            / "data.csv"
        )
        return BacktestRunRecord(
            run_id=run_id,
            trace=trace,
            strategy_id=manifest.strategy.strategy_id,
            parameters=BacktestParameters(),
            bars=(),
            dataset_source=dataset_source,
            parameter_values=dict(manifest.parameters),
            strategy_version=manifest.strategy.version,
            strategy_fingerprint=manifest.strategy.source_fingerprint,
            strategy_source_path=self.repository.strategy_path(run_id),
            strategy_class_name=manifest.strategy.class_name,
            dataset_id=manifest.dataset.dataset_id,
            dataset_fingerprint=manifest.dataset.content_fingerprint,
            created_at=manifest.created_at,
            research_cutoff=manifest.period.cutoff,
            status="PARTIAL" if manifest.status == "PARTIAL" else "COMPLETED",
        )

    def detail(
        self,
        run_id: str,
        *,
        strategy_registry_override: StrategyRegistry | None = None,
    ) -> RunDetail:
        detail = self.repository.detail(run_id)
        strategies = strategy_registry_override or strategy_registry
        current: str | None = None
        try:
            if detail.manifest.strategy.strategy_id == "pairs-trading":
                current = source_fingerprint(strategies.load("pairs-trading").source_path)
            else:
                registration = next(
                    (
                        item
                        for item in strategies.list()
                        if item.strategy_id == detail.manifest.strategy.strategy_id
                    ),
                    None,
                )
                if registration is not None and Path(registration.source_path).is_file():
                    current = source_fingerprint(Path(registration.source_path))
        except (OSError, ValueError):
            current = None
        return detail.model_copy(
            update={
                "current_strategy_fingerprint": current,
                "current_source_matches": None
                if current is None
                else current == detail.manifest.strategy.source_fingerprint,
            }
        )


run_ledger = RunLedger()
