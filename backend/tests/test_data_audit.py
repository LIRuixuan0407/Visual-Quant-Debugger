from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

import app.api.data_audit as data_audit_api
from app.backtest import BacktestParameters, run_backtest
from app.corporate_actions import (
    CorporateAction,
    CorporateActionEvent,
    CorporateActionRepository,
    CorporateActionService,
    CreateCorporateActionDataset,
)
from app.data import load_pair_csv
from app.data_audit import (
    CreateDataAudit,
    DataAuditEngine,
    DataAuditIntegrityError,
    DataAuditRepository,
)
from app.datasets import DatasetImportRequest, DatasetRegistry
from app.datasets.models import DatasetDefinition
from app.factors.catalog import factor_definition
from app.factors.models import (
    FactorObservation,
    FactorResearchRecord,
    HorizonEvaluation,
    PeriodEvaluation,
    ResearchPeriod,
    ResearchPeriods,
    ResearchStage,
)
from app.factors.repository import FactorResearchRepository
from app.fundamentals import FundamentalRepository
from app.fundamentals.models import FundamentalFieldSnapshot
from app.main import app
from app.runs import RunNotFoundError
from app.runs.models import (
    ArtifactHashes,
    DatasetRevision,
    EnvironmentSnapshot,
    ExecutionModelRevision,
    RunManifest,
    StrategyRevision,
)
from app.runs.models import ResearchPeriod as RunPeriod
from app.strategies import PairsTradingParameters
from app.trace.models import BacktestTrace, DataDependency
from app.universes import (
    CreateHistoricalUniverse,
    HistoricalUniverse,
    UniverseMembershipProvenance,
    UniverseRepository,
    UniverseSnapshot,
)


class StubFactorEngine:
    def __init__(
        self, observations: tuple[FactorObservation, ...], *, use_outside_target: bool = False
    ) -> None:
        self._observations = observations
        self.use_outside_target = use_outside_target
        self.observation_calls = 0
        self.evaluation_calls = 0

    def observations(self, record: FactorResearchRecord) -> tuple[FactorObservation, ...]:
        self.observation_calls += 1
        assert record.research_id.startswith("factor-research-")
        return self._observations

    def evaluate_periods(
        self,
        record: FactorResearchRecord,
        periods: tuple[tuple[ResearchStage, ResearchPeriod], ...],
    ) -> tuple[PeriodEvaluation, ...]:
        self.evaluation_calls += 1
        assert periods == (
            ("RESEARCH", record.periods.research),
            ("VALIDATION", record.periods.validation),
            ("HOLDOUT", record.periods.holdout),
        )
        return tuple(
            _period_evaluation(
                stage,
                period,
                research_counts={1: 2, 5: 2, 20: 2 if self.use_outside_target else 0},
            )
            for stage, period in periods
        )


class StubRunRepository:
    def __init__(self, manifest: RunManifest, trace: BacktestTrace) -> None:
        self.manifest = manifest
        self.trace = trace

    def get_manifest(self, run_id: str) -> RunManifest:
        if run_id != self.manifest.run_id:
            raise RunNotFoundError(run_id)
        return self.manifest

    def load_trace_for_run(self, run_id: str) -> BacktestTrace:
        if run_id != self.manifest.run_id:
            raise RunNotFoundError(run_id)
        return self.trace


def _horizon(horizon: int, observation_count: int) -> HorizonEvaluation:
    return HorizonEvaluation.model_validate(
        {
            "horizon": horizon,
            "observation_count": observation_count,
            "cross_section_count": 1 if observation_count else 0,
            "ic": None,
            "rank_ic": None,
            "ic_stability": None,
            "rank_ic_stability": None,
            "quantile_returns": (None, None, None, None, None),
            "long_short_spread": None,
            "turnover": None,
            "coverage": 0.0,
            "monotonic": False,
            "timeline": (),
        }
    )


def _period_evaluation(
    stage: ResearchStage,
    period: ResearchPeriod,
    *,
    research_counts: dict[int, int],
) -> PeriodEvaluation:
    counts = research_counts if stage == "RESEARCH" else {1: 0, 5: 0, 20: 0}
    return PeriodEvaluation(
        stage=stage,
        period=period,
        horizons=tuple(_horizon(horizon, counts[horizon]) for horizon in (1, 5, 20)),
    )


def _periods() -> ResearchPeriods:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return ResearchPeriods(
        research=ResearchPeriod(start=start, end=start + timedelta(days=9)),
        validation=ResearchPeriod(start=start + timedelta(days=10), end=start + timedelta(days=19)),
        holdout=ResearchPeriod(start=start + timedelta(days=20), end=start + timedelta(days=29)),
    )


def _fundamental_input(*, future: bool = False) -> FundamentalFieldSnapshot:
    used_at = datetime(2025, 1, 10, tzinfo=UTC)
    available_at = used_at + timedelta(days=1) if future else used_at - timedelta(days=1)
    return FundamentalFieldSnapshot(
        field="net_income",
        status="AVAILABLE",
        value=100.0,
        unit="USD",
        fiscal_period="FY2024",
        report_date=used_at - timedelta(days=10),
        filed_at=used_at - timedelta(days=2),
        available_at=available_at,
        used_at=used_at,
        age_days=1,
        form="10-K",
        accession="test-accession",
    )


def _observations(
    *,
    dependency_future: bool = False,
    fundamental_future: bool = False,
) -> tuple[FactorObservation, ...]:
    timestamp = datetime(2025, 1, 10, tzinfo=UTC)
    result: list[FactorObservation] = []
    for index, symbol in enumerate(("AAPL", "MSFT"), start=1):
        available_at = timestamp + timedelta(days=1) if dependency_future else timestamp
        dependency = DataDependency(
            dependency_id=f"dependency-{index}",
            source="market_data",
            field="close",
            symbol=symbol,
            value=100.0 + index,
            source_timestamp=timestamp - timedelta(days=1),
            available_at=available_at,
            used_at=timestamp,
        )
        result.append(
            FactorObservation(
                symbol=symbol,
                timestamp=timestamp,
                factor_id="momentum",
                value=float(index),
                window_start=timestamp - timedelta(days=5),
                window_end=timestamp,
                available_at=timestamp,
                future_returns={1: 0.01, 5: 0.02, 20: 0.03},
                future_return_timestamps={
                    1: timestamp,
                    5: timestamp,
                    20: timestamp + timedelta(days=6),
                },
                dependencies=(dependency,),
                fundamental_inputs=(_fundamental_input(future=fundamental_future),),
            )
        )
    return tuple(result)


def _record(
    datasets: DatasetRegistry,
    observations: tuple[FactorObservation, ...],
    *,
    survivorship_bias_free: bool = True,
    restatement_safe: bool = True,
) -> FactorResearchRecord:
    dataset = datasets.get("pairs-sample-v1")
    assert dataset is not None
    periods = _periods()
    return FactorResearchRecord(
        research_id="factor-research-a1b2c3d4",
        name="Boundary audit fixture",
        created_at=datetime(2025, 2, 1, tzinfo=UTC),
        dataset_id=dataset.dataset_id,
        dataset_name=dataset.name,
        dataset_revision=dataset.content_fingerprint,
        factor=factor_definition("momentum"),
        parameters={"lookback": 5},
        universe=("AAPL", "MSFT"),
        survivorship_bias_free=survivorship_bias_free,
        survivorship_warning="Static constituent membership may contain survivorship bias.",
        periods=periods,
        evaluations=(
            _period_evaluation("RESEARCH", periods.research, research_counts={1: 2, 5: 2, 20: 0}),
        ),
        factor_observation_count=len(observations),
        sample_observations=observations,
        fundamental_dataset_id="fundamental-audit" if observations else None,
        fundamental_provider="SEC" if observations else None,
        restatement_safe=restatement_safe,
        restatement_warning=(
            None if restatement_safe else "Latest-value history can include later restatements."
        ),
    )


def _engine(
    tmp_path: Path,
    factor_engine: StubFactorEngine,
    record: FactorResearchRecord,
    *,
    runs: StubRunRepository | None = None,
) -> DataAuditEngine:
    datasets = DatasetRegistry(tmp_path)
    factors = FactorResearchRepository(tmp_path)
    factors.save(record)
    run_repository = runs or StubRunRepository(_run_manifest(datasets), _trace())
    return DataAuditEngine(
        datasets,
        factors,
        factor_engine,
        FundamentalRepository(tmp_path),
        UniverseRepository(tmp_path),
        run_repository,
        DataAuditRepository(tmp_path),
    )


def _trace() -> BacktestTrace:
    project_root = Path(__file__).parents[2]
    result = run_backtest(
        load_pair_csv(project_root / "sample_data" / "pairs_daily.csv"),
        BacktestParameters(strategy=PairsTradingParameters(lookback=5, entry_z=1.0, exit_z=0.8)),
    )
    return result.trace


def _run_manifest(datasets: DatasetRegistry, trace: BacktestTrace | None = None) -> RunManifest:
    dataset = datasets.get("pairs-sample-v1")
    assert dataset is not None
    current_trace = trace or _trace()
    return RunManifest(
        run_id="run-0123456789abcdef01234567",
        run_fingerprint="sha256:" + "1" * 64,
        status="COMPLETED",
        created_at=datetime(2025, 2, 1, tzinfo=UTC),
        completed_at=datetime(2025, 2, 2, tzinfo=UTC),
        strategy=StrategyRevision(
            strategy_id="pairs-trading",
            name="Pairs Trading",
            version="1.0.0",
            class_name="PairsTradingStrategy",
            source_fingerprint="sha256:" + "2" * 64,
            original_source_path="saved-strategy.py",
        ),
        dataset=DatasetRevision(
            dataset_id=dataset.dataset_id,
            name=dataset.name,
            content_fingerprint=dataset.content_fingerprint,
            source_timezone=dataset.source_timezone,
            symbols=dataset.symbols,
        ),
        period=RunPeriod(
            start=current_trace.metadata.data_start,
            end=current_trace.metadata.data_end,
            cutoff=None,
        ),
        parameters={"lookback": 5},
        execution_model=ExecutionModelRevision(),
        engine=EnvironmentSnapshot(
            python_version="3.12",
            platform="test",
            vqd_version="test",
        ),
        trace_id="trace-audit-fixture",
        artifacts=ArtifactHashes(
            strategy_source_sha256="sha256:" + "2" * 64,
            trace_sha256="sha256:" + "3" * 64,
        ),
    )


def _finding(record, code: str):
    return next(item for item in record.findings if item.code == code)


def test_dataset_quality_mapping_and_provenance_semantics(tmp_path: Path) -> None:
    dataset = DatasetRegistry(tmp_path).get("pairs-sample-v1")
    assert dataset is not None
    codes = [item.code for item in DataAuditEngine._dataset_findings(dataset)]
    assert codes == [
        "DATASET_DUPLICATES",
        "DATASET_MISSING_REQUIRED_VALUES",
        "DATASET_ROWS_REORDERED",
        "DATASET_ALIGNMENT_GAPS",
        "DATASET_TIMEZONE",
        "DATASET_PROVENANCE",
        "DATASET_FINGERPRINT",
        "DATASET_COVERAGE",
    ]
    provider = dataset.model_copy(update={"source_type": "PROVIDER", "provenance": None})
    csv_dataset = dataset.model_copy(update={"source_type": "CSV", "provenance": None})
    assert (
        _finding_obj(DataAuditEngine._dataset_findings(provider), "DATASET_PROVENANCE").severity
        == "WARNING"
    )
    assert (
        _finding_obj(DataAuditEngine._dataset_findings(csv_dataset), "DATASET_PROVENANCE").severity
        == "INFO"
    )


def _finding_obj(findings, code: str):
    return next(item for item in findings if item.code == code)


def test_factor_audit_reuses_canonical_engine_and_excludes_cross_stage_targets(
    tmp_path: Path,
) -> None:
    datasets = DatasetRegistry(tmp_path)
    observations = _observations()
    record = _record(datasets, observations)
    stub = StubFactorEngine(observations)
    audit = _engine(tmp_path, stub, record).create(
        CreateDataAudit(root_type="FACTOR_RESEARCH", root_id=record.research_id)
    )

    assert stub.observation_calls == 1
    assert stub.evaluation_calls == 1
    assert audit.checked_observations == 2
    assert audit.checked_dependencies == 2
    assert audit.checked_future_returns == 6
    assert audit.checked_fundamental_inputs == 2
    assert _finding(audit, "AVAILABLE_FUTURE_TARGET_OUTSIDE_STAGE").severity == "INFO"
    assert _finding(audit, "AVAILABLE_FUTURE_TARGET_OUTSIDE_STAGE").affected_count == 2
    assert _finding(audit, "TARGET_USED_OUTSIDE_STAGE").severity == "PASS"
    assert _finding(audit, "FUNDAMENTAL_AVAILABILITY_VIOLATION").severity == "PASS"


def test_actual_outside_stage_target_use_is_a_violation(tmp_path: Path) -> None:
    datasets = DatasetRegistry(tmp_path)
    observations = _observations()
    record = _record(datasets, observations)
    audit = _engine(
        tmp_path,
        StubFactorEngine(observations, use_outside_target=True),
        record,
    ).create(CreateDataAudit(root_type="FACTOR_RESEARCH", root_id=record.research_id))
    finding = _finding(audit, "TARGET_USED_OUTSIDE_STAGE")
    assert finding.severity == "VIOLATION"
    assert finding.affected_count == 2
    assert audit.status == "VIOLATION"


def test_dependency_and_fundamental_future_availability_are_violations(
    tmp_path: Path,
) -> None:
    datasets = DatasetRegistry(tmp_path)
    observations = _observations(dependency_future=True, fundamental_future=True)
    record = _record(datasets, observations)
    audit = _engine(tmp_path, StubFactorEngine(observations), record).create(
        CreateDataAudit(root_type="FACTOR_RESEARCH", root_id=record.research_id)
    )
    dependency = _finding(audit, "DEPENDENCY_LOOK_AHEAD")
    fundamental = _finding(audit, "FUNDAMENTAL_AVAILABILITY_VIOLATION")
    assert (dependency.severity, dependency.checked_count, dependency.affected_count) == (
        "VIOLATION",
        2,
        2,
    )
    assert (fundamental.severity, fundamental.checked_count, fundamental.affected_count) == (
        "VIOLATION",
        2,
        2,
    )


def test_restatement_and_survivorship_are_warnings_not_future_leaks(
    tmp_path: Path,
) -> None:
    datasets = DatasetRegistry(tmp_path)
    observations = _observations()
    record = _record(
        datasets,
        observations,
        survivorship_bias_free=False,
        restatement_safe=False,
    )
    audit = _engine(tmp_path, StubFactorEngine(observations), record).create(
        CreateDataAudit(root_type="FACTOR_RESEARCH", root_id=record.research_id)
    )
    assert _finding(audit, "FUNDAMENTAL_RESTATEMENT_SAFETY").severity == "WARNING"
    assert _finding(audit, "STATIC_UNIVERSE_SURVIVORSHIP_RISK").severity == "WARNING"
    assert _finding(audit, "FUNDAMENTAL_AVAILABILITY_VIOLATION").severity == "PASS"
    assert audit.status == "WARNING"


def test_corporate_action_and_universe_evidence_have_distinct_findings(
    tmp_path: Path,
) -> None:
    datasets = DatasetRegistry(tmp_path)
    dataset = datasets.get("pairs-sample-v1")
    assert dataset is not None
    used_at = dataset.start_time
    symbols = (*dataset.symbols, "MISSING_MARKET_DATA")
    universe = HistoricalUniverse(
        universe_id="universe-audit-evidence",
        name="Historical membership",
        source="Index archive",
        mode="POINT_IN_TIME",
        dataset_id=dataset.dataset_id,
        created_at=datetime(2025, 2, 1, tzinfo=UTC),
        snapshots=(
            UniverseSnapshot(
                effective_date=used_at,
                symbols=symbols,
                membership_provenance=tuple(
                    UniverseMembershipProvenance(
                        symbol=symbol,
                        source="Index archive",
                        effective_from=used_at,
                        evidence="Archived constituent file",
                    )
                    for symbol in symbols
                ),
            ),
        ),
        survivorship_bias_free=True,
        disclosure="Historical membership is source-backed.",
    )
    UniverseRepository(tmp_path).save(universe)
    actions = CorporateActionService(CorporateActionRepository(tmp_path)).create(
        CreateCorporateActionDataset(
            name="Late delisting evidence",
            provider="Exchange",
            actions=(
                CorporateAction(
                    action_id="delisting-missing-settlement",
                    symbol=dataset.symbols[0],
                    action_type="DELISTING",
                    effective_at=used_at,
                    announced_at=None,
                    available_at=used_at + timedelta(days=1),
                    source="Exchange bulletin",
                    evidence="Settlement was not published.",
                    delisting_reason="Insolvency",
                ),
            ),
            disclosure="No settlement price is inferred.",
        )
    )
    observations = _observations()
    record = _record(datasets, observations).model_copy(
        update={
            "universe": symbols,
            "universe_id": universe.universe_id,
            "universe_mode": "POINT_IN_TIME",
            "survivorship_bias_free": True,
            "corporate_action_dataset_id": actions.corporate_action_dataset_id,
        }
    )
    audit = _engine(tmp_path, StubFactorEngine(observations), record).create(
        CreateDataAudit(root_type="FACTOR_RESEARCH", root_id=record.research_id)
    )

    assert _finding(audit, "STATIC_UNIVERSE_SURVIVORSHIP_RISK").severity == "PASS"
    assert _finding(audit, "UNIVERSE_MEMBERSHIP_PROVENANCE_MISSING").severity == "PASS"
    missing_market = _finding(audit, "UNIVERSE_MEMBER_WITHOUT_MARKET_DATA")
    assert missing_market.severity == "INSUFFICIENT_EVIDENCE"
    assert missing_market.evidence == ("MISSING_MARKET_DATA",)
    assert _finding(audit, "CORPORATE_ACTION_DATASET_MISSING").severity == "PASS"
    assert _finding(audit, "CORPORATE_ACTION_PIT_WARNING").severity == "WARNING"
    assert _finding(audit, "UNRESOLVED_DELISTING").severity == "INSUFFICIENT_EVIDENCE"


def test_run_audit_includes_universe_and_corporate_action_evidence(tmp_path: Path) -> None:
    datasets = DatasetRegistry(tmp_path)
    dataset = datasets.get("pairs-sample-v1")
    assert dataset is not None
    universe = UniverseRepository(tmp_path).create(
        CreateHistoricalUniverse(
            name="Run historical universe",
            source="Index archive",
            mode="POINT_IN_TIME",
            dataset_id=dataset.dataset_id,
            snapshots=(
                UniverseSnapshot(
                    effective_date=dataset.start_time,
                    symbols=dataset.symbols,
                    membership_provenance=tuple(
                        UniverseMembershipProvenance(
                            symbol=symbol,
                            source="Index archive",
                            effective_from=dataset.start_time,
                            evidence="Archived constituent file",
                        )
                        for symbol in dataset.symbols
                    ),
                ),
            ),
            disclosure="Historical membership is source-backed.",
        )
    )
    action = CorporateAction(
        action_id="run-unresolved-delisting",
        symbol=dataset.symbols[0],
        action_type="DELISTING",
        effective_at=dataset.end_time,
        announced_at=None,
        available_at=dataset.end_time,
        source="Exchange bulletin",
        evidence="No reliable settlement price was published.",
        delisting_reason="Insolvency",
    )
    actions = CorporateActionService(CorporateActionRepository(tmp_path)).create(
        CreateCorporateActionDataset(
            name="Run action evidence",
            provider="Exchange",
            actions=(action,),
            disclosure="No settlement price is inferred.",
        )
    )
    trace = _trace().model_copy(
        update={
            "corporate_action_events": (
                CorporateActionEvent(
                    action_id=action.action_id,
                    symbol=action.symbol,
                    action_type="DELISTING",
                    timestamp=action.effective_at,
                    status="UNRESOLVED",
                    quantity_before=10.0,
                    quantity_after=10.0,
                    cash_amount=0.0,
                    settlement_price=None,
                    evidence=action.evidence,
                ),
            )
        }
    )
    manifest = _run_manifest(datasets, trace).model_copy(
        update={
            "universe_id": universe.universe_id,
            "corporate_action_dataset_id": actions.corporate_action_dataset_id,
            "unresolved_corporate_action_ids": (action.action_id,),
        }
    )
    placeholder = _record(datasets, ())
    audit = _engine(
        tmp_path,
        StubFactorEngine(()),
        placeholder,
        runs=StubRunRepository(manifest, trace),
    ).create(CreateDataAudit(root_type="RUN", root_id=manifest.run_id))

    assert _finding(audit, "STATIC_UNIVERSE_SURVIVORSHIP_RISK").severity == "PASS"
    assert _finding(audit, "UNIVERSE_MEMBERSHIP_PROVENANCE_MISSING").severity == "PASS"
    assert _finding(audit, "UNIVERSE_MEMBER_WITHOUT_MARKET_DATA").severity == "PASS"
    assert _finding(audit, "CORPORATE_ACTION_DATASET_MISSING").severity == "PASS"
    assert _finding(audit, "CORPORATE_ACTION_PIT_WARNING").severity == "PASS"
    assert _finding(audit, "UNRESOLVED_DELISTING").severity == "INSUFFICIENT_EVIDENCE"
    assert _finding(audit, "CORPORATE_ACTION_TRACE_CONSISTENCY").severity == "PASS"
    assert audit.source_fingerprints[f"universe:{universe.universe_id}"].startswith("sha256:")
    assert (
        audit.source_fingerprints[
            f"corporate_action_dataset:{actions.corporate_action_dataset_id}"
        ]
        == actions.content_fingerprint
    )


def test_run_trace_look_ahead_is_counted_and_saved_as_violation(tmp_path: Path) -> None:
    datasets = DatasetRegistry(tmp_path)
    trace = _trace()
    dependency = trace.timeline[0].data_dependencies[0]
    future = dependency.model_copy(update={"available_at": dependency.used_at + timedelta(days=1)})
    event = trace.timeline[0].model_copy(
        update={"data_dependencies": (future, *trace.timeline[0].data_dependencies[1:])}
    )
    trace = trace.model_copy(update={"timeline": (event, *trace.timeline[1:])})
    manifest = _run_manifest(datasets, trace)
    runs = StubRunRepository(manifest, trace)
    placeholder = _record(datasets, ())
    audit = _engine(tmp_path, StubFactorEngine(()), placeholder, runs=runs).create(
        CreateDataAudit(root_type="RUN", root_id=manifest.run_id)
    )
    finding = _finding(audit, "DEPENDENCY_LOOK_AHEAD")
    assert finding.severity == "VIOLATION"
    assert finding.affected_count == 1
    assert finding.checked_count == audit.checked_dependencies
    assert audit.status == "VIOLATION"


def _csv() -> bytes:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = ["timestamp,symbol,close"]
    for day in range(3):
        for symbol in ("AAPL", "MSFT"):
            rows.append(f"{(start + timedelta(days=day)).isoformat()},{symbol},{100 + day}")
    return ("\n".join(rows) + "\n").encode()


def test_immutable_repository_restart_and_source_drift_are_distinct(tmp_path: Path) -> None:
    datasets = DatasetRegistry(tmp_path)
    preview = datasets.preview("audit.csv", _csv())
    dataset = datasets.commit(
        DatasetImportRequest(
            preview_id=preview.preview_id,
            name="Audit dataset",
            mapping={"timestamp": "timestamp", "symbol": "symbol", "close": "close"},
        )
    )
    factors = FactorResearchRepository(tmp_path)
    audits = DataAuditRepository(tmp_path)
    engine = DataAuditEngine(
        datasets,
        factors,
        StubFactorEngine(()),
        FundamentalRepository(tmp_path),
        UniverseRepository(tmp_path),
        StubRunRepository(_run_manifest(datasets), _trace()),
        audits,
    )
    record = engine.create(CreateDataAudit(root_type="DATASET", root_id=dataset.dataset_id))
    path = tmp_path / ".vqd" / "data-audits" / record.audit_id / "audit.json"
    assert path.is_file()
    assert not path.with_suffix(".json.tmp").exists()
    assert DataAuditRepository(tmp_path).get(record.audit_id) == record
    with pytest.raises(DataAuditIntegrityError, match="immutable"):
        audits.save(record.model_copy(update={"status": "WARNING"}))

    metadata = tmp_path / ".vqd" / "datasets" / dataset.dataset_id / "metadata.json"
    changed: DatasetDefinition = dataset.model_copy(
        update={"content_fingerprint": "sha256:" + "f" * 64}
    )
    metadata.write_text(changed.model_dump_json(indent=2) + "\n", encoding="utf-8")
    verification = engine.verify_source(record.audit_id)
    assert verification.source_state == "CHANGED"
    assert DataAuditRepository(tmp_path).get(record.audit_id) == record


def test_changed_custom_factor_source_is_reported_and_never_recomputed(tmp_path: Path) -> None:
    datasets = DatasetRegistry(tmp_path)
    observations = _observations()
    source = tmp_path / "custom_factor.py"
    source.write_text("old source\n", encoding="utf-8")
    recorded_fingerprint = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_text("changed source\n", encoding="utf-8")
    base = _record(datasets, ())
    custom_factor = base.factor.model_copy(
        update={
            "origin": "CUSTOM",
            "source_path": str(source),
            "source_fingerprint": recorded_fingerprint,
        }
    )
    record = base.model_copy(update={"factor": custom_factor})
    factor_engine = StubFactorEngine(observations)
    engine = _engine(tmp_path, factor_engine, record)

    audit = engine.create(CreateDataAudit(root_type="FACTOR_RESEARCH", root_id=record.research_id))

    source_finding = _finding(audit, "FACTOR_SOURCE_REVISION_DRIFT")
    assert source_finding.severity == "WARNING"
    assert "will not be used" in source_finding.reason
    assert _finding(audit, "CANONICAL_FACTOR_EVIDENCE").severity == "INSUFFICIENT_EVIDENCE"
    assert factor_engine.observation_calls == 0
    assert engine.detail(audit.audit_id).source_state == "CHANGED"


async def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_api_create_list_detail_verify_and_errors(tmp_path: Path, monkeypatch) -> None:
    datasets = DatasetRegistry(tmp_path)
    placeholder = _record(datasets, ())
    engine = _engine(tmp_path, StubFactorEngine(()), placeholder)
    audits = engine.audits
    monkeypatch.setattr(data_audit_api, "_engine", lambda: engine)
    monkeypatch.setattr(data_audit_api, "data_audit_repository", audits)

    assert "/api/data-audits" in app.openapi()["paths"]
    created = asyncio.run(
        _request(
            "POST",
            "/api/data-audits",
            json={"root_type": "DATASET", "root_id": "pairs-sample-v1"},
        )
    )
    assert created.status_code == 201
    audit_id = created.json()["audit"]["audit_id"]
    assert asyncio.run(_request("GET", "/api/data-audits")).json()[0]["audit_id"] == audit_id
    assert asyncio.run(_request("GET", f"/api/data-audits/{audit_id}")).status_code == 200
    verified = asyncio.run(_request("POST", f"/api/data-audits/{audit_id}/verify-source"))
    assert verified.json()["source_state"] == "MATCHES"
    assert (
        asyncio.run(
            _request(
                "POST",
                "/api/data-audits",
                json={"root_type": "DATASET", "root_id": "dataset-missing"},
            )
        ).status_code
        == 404
    )
    assert (
        asyncio.run(
            _request(
                "POST",
                "/api/data-audits",
                json={"root_type": "DATASET", "root_id": "../escape"},
            )
        ).status_code
        == 422
    )
    assert asyncio.run(_request("GET", "/api/data-audits/not-an-audit")).status_code == 422


def test_newer_dataset_revision_does_not_change_old_audit_source_state(tmp_path: Path) -> None:
    datasets = DatasetRegistry(tmp_path)
    preview = datasets.preview("audit-r1.csv", _csv())
    r1 = datasets.commit(
        DatasetImportRequest(
            preview_id=preview.preview_id,
            name="Versioned audit dataset",
            mapping={"timestamp": "timestamp", "symbol": "symbol", "close": "close"},
        )
    )
    engine = DataAuditEngine(
        datasets,
        FactorResearchRepository(tmp_path),
        StubFactorEngine(()),
        FundamentalRepository(tmp_path),
        UniverseRepository(tmp_path),
        StubRunRepository(_run_manifest(datasets), _trace()),
        DataAuditRepository(tmp_path),
    )
    audit = engine.create(CreateDataAudit(root_type="DATASET", root_id=r1.dataset_id))

    extended = _csv().replace(
        b"2025-01-03T00:00:00+00:00,MSFT,102\n",
        (
            b"2025-01-03T00:00:00+00:00,MSFT,102\n"
            b"2025-01-04T00:00:00+00:00,AAPL,103\n"
            b"2025-01-04T00:00:00+00:00,MSFT,103\n"
        ),
    )
    second_preview = datasets.preview("audit-r2.csv", extended)
    r2 = datasets.commit(
        DatasetImportRequest(
            preview_id=second_preview.preview_id,
            name="Versioned audit dataset",
            mapping={"timestamp": "timestamp", "symbol": "symbol", "close": "close"},
            dataset_family_id=r1.dataset_family_id,
            revision_reason="Extend audit coverage",
        )
    )

    verification = engine.verify_source(audit.audit_id)
    assert verification.source_state == "MATCHES"
    assert verification.newer_dataset_revision_available is True
    assert verification.latest_dataset_id == r2.dataset_id
    assert verification.latest_dataset_revision == 2
    detail = engine.detail(audit.audit_id)
    assert detail.source_state == "MATCHES"
    assert detail.newer_dataset_revision_available is True
