from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.corporate_actions import CorporateActionRepository
from app.datasets import DatasetRegistry
from app.datasets.models import DatasetDefinition
from app.factors import FactorResearchRepository
from app.factors.models import (
    FactorObservation,
    FactorResearchRecord,
    PeriodEvaluation,
    ResearchPeriod,
    ResearchStage,
)
from app.fundamentals import FundamentalRepository
from app.runs import ArtifactIntegrityError, RunNotFoundError
from app.runs.models import RunManifest
from app.trace.models import BacktestTrace, DataDependency
from app.trace.validation import collect_look_ahead_diagnostics
from app.universes import UniverseRepository, membership_provenance_issues

from .models import (
    AuditSeverity,
    AuditSourceState,
    AuditStatus,
    CreateDataAudit,
    DataAuditDetail,
    DataAuditFinding,
    DataAuditRecord,
    DataAuditSourceVerification,
)
from .repository import DataAuditRepository

EVIDENCE_LIMIT = 10
DATASET_ID_PATTERN = re.compile(r"^(?:pairs-sample-v1|dataset-[A-Za-z0-9._-]+)$")
FACTOR_RESEARCH_ID_PATTERN = re.compile(r"^factor-research-[A-Za-z0-9._-]+$")
RUN_ID_PATTERN = re.compile(r"^run-[0-9a-f]{24}$")


def _model_fingerprint(value: BaseModel) -> str:
    payload = json.dumps(value.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def _finding(
    code: str,
    severity: AuditSeverity,
    subject: str,
    reason: str,
    *,
    evidence: tuple[str, ...] = (),
    checked_count: int = 0,
    affected_count: int = 0,
) -> DataAuditFinding:
    return DataAuditFinding(
        code=code,
        severity=severity,
        subject=subject,
        reason=reason,
        evidence=evidence[:EVIDENCE_LIMIT],
        checked_count=checked_count,
        affected_count=affected_count,
    )


def _status(findings: list[DataAuditFinding]) -> AuditStatus:
    severities = {item.severity for item in findings}
    if "VIOLATION" in severities:
        return "VIOLATION"
    if "WARNING" in severities:
        return "WARNING"
    if "INSUFFICIENT_EVIDENCE" in severities:
        return "INCOMPLETE"
    return "PASS"


@dataclass(slots=True)
class _AuditParts:
    findings: list[DataAuditFinding] = field(default_factory=list)
    source_fingerprints: dict[str, str] = field(default_factory=dict)
    checked_observations: int = 0
    checked_dependencies: int = 0
    checked_future_returns: int = 0
    checked_fundamental_inputs: int = 0
    disclosures: list[str] = field(default_factory=list)


class FactorAuditRuntime(Protocol):
    def observations(self, record: FactorResearchRecord) -> tuple[FactorObservation, ...]: ...

    def evaluate_periods(
        self,
        record: FactorResearchRecord,
        periods: tuple[tuple[ResearchStage, ResearchPeriod], ...],
    ) -> tuple[PeriodEvaluation, ...]: ...


class RunAuditStore(Protocol):
    def get_manifest(self, run_id: str) -> RunManifest: ...

    def load_trace_for_run(self, run_id: str) -> BacktestTrace: ...


class DataAuditEngine:
    def __init__(
        self,
        datasets: DatasetRegistry,
        factor_research: FactorResearchRepository,
        factor_engine: FactorAuditRuntime,
        fundamentals: FundamentalRepository,
        universes: UniverseRepository,
        runs: RunAuditStore,
        audits: DataAuditRepository,
        corporate_actions: CorporateActionRepository | None = None,
    ) -> None:
        self.datasets = datasets
        self.factor_research = factor_research
        self.factor_engine = factor_engine
        self.fundamentals = fundamentals
        self.universes = universes
        self.runs = runs
        self.audits = audits
        self.corporate_actions = corporate_actions or CorporateActionRepository(
            datasets.workspace_root
        )

    @staticmethod
    def _source_file_fingerprint(source_path: str | None) -> str | None:
        if not source_path:
            return None
        try:
            return f"sha256:{hashlib.sha256(Path(source_path).read_bytes()).hexdigest()}"
        except OSError:
            return None

    @staticmethod
    def _validate_root(request: CreateDataAudit) -> None:
        patterns = {
            "DATASET": DATASET_ID_PATTERN,
            "FACTOR_RESEARCH": FACTOR_RESEARCH_ID_PATTERN,
            "RUN": RUN_ID_PATTERN,
        }
        if not patterns[request.root_type].fullmatch(request.root_id):
            raise ValueError(f"Invalid {request.root_type} id '{request.root_id}'")

    @staticmethod
    def _dataset_findings(dataset: DatasetDefinition) -> list[DataAuditFinding]:
        quality = dataset.quality
        subject = dataset.dataset_id
        findings = [
            _finding(
                "DATASET_DUPLICATES",
                "VIOLATION" if quality.duplicates else "PASS",
                subject,
                "Duplicate symbol/timestamp rows were found."
                if quality.duplicates
                else "No duplicate symbol/timestamp rows were reported by Dataset validation.",
                evidence=tuple(quality.issues),
                checked_count=quality.rows,
                affected_count=quality.duplicates,
            ),
            _finding(
                "DATASET_MISSING_REQUIRED_VALUES",
                "VIOLATION" if quality.missing_required_values else "PASS",
                subject,
                "Required values are missing from validated rows."
                if quality.missing_required_values
                else "Dataset validation reported no missing required values.",
                evidence=tuple(quality.issues),
                checked_count=quality.rows,
                affected_count=quality.missing_required_values,
            ),
            _finding(
                "DATASET_ROWS_REORDERED",
                "WARNING" if quality.rows_reordered else "PASS",
                subject,
                "Rows required chronological reordering during validation."
                if quality.rows_reordered
                else "Dataset rows were already in canonical chronological order.",
                evidence=tuple(quality.issues),
                checked_count=quality.rows,
                affected_count=quality.rows_reordered,
            ),
            _finding(
                "DATASET_ALIGNMENT_GAPS",
                "WARNING" if quality.alignment_gaps else "PASS",
                subject,
                "The validated cross-section contains symbol/timestamp alignment gaps."
                if quality.alignment_gaps
                else "No symbol/timestamp alignment gaps were reported.",
                evidence=tuple(quality.issues),
                checked_count=quality.rows + quality.alignment_gaps,
                affected_count=quality.alignment_gaps,
            ),
        ]
        timezone_valid = bool(dataset.timezone.strip() and quality.timezone.strip())
        findings.append(
            _finding(
                "DATASET_TIMEZONE",
                "PASS" if timezone_valid else "INSUFFICIENT_EVIDENCE",
                subject,
                "Dataset and validation timezone metadata are present."
                if timezone_valid
                else "Timezone metadata is incomplete.",
                evidence=(
                    f"dataset timezone={dataset.timezone or 'missing'}",
                    f"quality timezone={quality.timezone or 'missing'}",
                    f"source timezone={dataset.source_timezone or 'missing'}",
                ),
                checked_count=1,
                affected_count=0 if timezone_valid else 1,
            )
        )
        if dataset.source_type == "PROVIDER":
            provenance_ok = dataset.provenance is not None
            findings.append(
                _finding(
                    "DATASET_PROVENANCE",
                    "PASS" if provenance_ok else "WARNING",
                    subject,
                    "Provider provenance records the request, retrieval, and market timestamps."
                    if provenance_ok
                    else "Provider-backed data has no saved provider provenance.",
                    evidence=(
                        ()
                        if dataset.provenance is None
                        else (
                            f"provider={dataset.provenance.provider}",
                            f"feed={dataset.provenance.feed}",
                            f"retrieved_at={dataset.provenance.retrieved_at.isoformat()}",
                        )
                    ),
                    checked_count=1,
                    affected_count=0 if provenance_ok else 1,
                )
            )
        else:
            findings.append(
                _finding(
                    "DATASET_PROVENANCE",
                    "INFO",
                    subject,
                    "Provider provenance is not applicable to this dataset source type.",
                    evidence=(f"source_type={dataset.source_type}",),
                    checked_count=1,
                )
            )
        fingerprint_ok = (
            dataset.content_fingerprint.startswith("sha256:")
            and len(dataset.content_fingerprint) == len("sha256:") + 64
        )
        findings.append(
            _finding(
                "DATASET_FINGERPRINT",
                "PASS" if fingerprint_ok else "INSUFFICIENT_EVIDENCE",
                subject,
                "A SHA-256 content fingerprint identifies the validated dataset revision."
                if fingerprint_ok
                else "The dataset does not have a complete SHA-256 content fingerprint.",
                evidence=(dataset.content_fingerprint or "missing",),
                checked_count=1,
                affected_count=0 if fingerprint_ok else 1,
            )
        )
        coverage_ok = (
            dataset.start_time <= dataset.end_time
            and dataset.start_time <= quality.start <= quality.end <= dataset.end_time
        )
        findings.append(
            _finding(
                "DATASET_COVERAGE",
                "PASS" if coverage_ok else "VIOLATION",
                subject,
                "Dataset and quality-report coverage boundaries are chronological and aligned."
                if coverage_ok
                else "Dataset coverage conflicts with its saved quality report.",
                evidence=(
                    f"dataset={dataset.start_time.isoformat()}..{dataset.end_time.isoformat()}",
                    f"quality={quality.start.isoformat()}..{quality.end.isoformat()}",
                ),
                checked_count=1,
                affected_count=0 if coverage_ok else 1,
            )
        )
        return findings

    def _dataset_audit(self, dataset_id: str) -> _AuditParts:
        dataset = self.datasets.get(dataset_id)
        if dataset is None:
            raise KeyError(f"Dataset '{dataset_id}' was not found")
        return _AuditParts(
            findings=self._dataset_findings(dataset),
            source_fingerprints={f"dataset:{dataset.dataset_id}": dataset.content_fingerprint},
            disclosures=[
                "Dataset checks describe structure, provenance, coverage, timezone, and revision. "
                "A valid dataset alone does not certify Factor, Fundamental, or Run point-in-time "
                "safety."
            ],
        )

    @staticmethod
    def _observation_findings(
        record: FactorResearchRecord, observations: tuple[FactorObservation, ...]
    ) -> tuple[list[DataAuditFinding], int, int]:
        availability: list[str] = []
        windows: list[str] = []
        dependency_leaks: list[str] = []
        dependency_count = 0
        for observation in observations:
            identity = f"{observation.symbol}@{observation.timestamp.isoformat()}"
            if observation.available_at > observation.timestamp:
                availability.append(
                    f"{identity}: available_at={observation.available_at.isoformat()}"
                )
            if not (observation.window_start <= observation.window_end <= observation.timestamp):
                windows.append(
                    f"{identity}: window={observation.window_start.isoformat()}.."
                    f"{observation.window_end.isoformat()}"
                )
            for dependency in observation.dependencies:
                dependency_count += 1
                if dependency.available_at > dependency.used_at:
                    dependency_leaks.append(
                        f"{dependency.dependency_id}: available_at="
                        f"{dependency.available_at.isoformat()} used_at="
                        f"{dependency.used_at.isoformat()}"
                    )
        findings = [
            _finding(
                "FACTOR_AVAILABLE_AFTER_SIGNAL",
                "VIOLATION" if availability else "PASS",
                record.research_id,
                "Some Factor values became available after their signal timestamp."
                if availability
                else "Every Factor value was available no later than its signal timestamp.",
                evidence=tuple(availability),
                checked_count=len(observations),
                affected_count=len(availability),
            ),
            _finding(
                "FACTOR_WINDOW_INVALID",
                "VIOLATION" if windows else "PASS",
                record.research_id,
                "Some Factor windows extend beyond their signal timestamp or are reversed."
                if windows
                else "Every Factor window ends no later than its signal timestamp.",
                evidence=tuple(windows),
                checked_count=len(observations),
                affected_count=len(windows),
            ),
            _finding(
                "DEPENDENCY_LOOK_AHEAD",
                "VIOLATION" if dependency_leaks else "PASS",
                record.research_id,
                "Some dependencies were used before they became available."
                if dependency_leaks
                else "Every Factor dependency was available when it was used.",
                evidence=tuple(dependency_leaks),
                checked_count=dependency_count,
                affected_count=len(dependency_leaks),
            ),
        ]
        return findings, dependency_count, len(dependency_leaks)

    @staticmethod
    def _stage_for(
        record: FactorResearchRecord, observation: FactorObservation
    ) -> tuple[ResearchStage, ResearchPeriod] | None:
        periods: tuple[tuple[ResearchStage, ResearchPeriod], ...] = (
            ("RESEARCH", record.periods.research),
            ("VALIDATION", record.periods.validation),
            ("HOLDOUT", record.periods.holdout),
        )
        return next(
            (
                (stage, period)
                for stage, period in periods
                if period.start <= observation.timestamp <= period.end
            ),
            None,
        )

    def _future_boundary_findings(
        self, record: FactorResearchRecord, observations: tuple[FactorObservation, ...]
    ) -> tuple[list[DataAuditFinding], int]:
        periods: tuple[tuple[ResearchStage, ResearchPeriod], ...] = (
            ("RESEARCH", record.periods.research),
            ("VALIDATION", record.periods.validation),
            ("HOLDOUT", record.periods.holdout),
        )
        evaluations = self.factor_engine.evaluate_periods(record, periods)
        actual = {
            (evaluation.stage, horizon.horizon): horizon.observation_count
            for evaluation in evaluations
            for horizon in evaluation.horizons
        }
        checked = 0
        outside: list[str] = []
        eligible: dict[tuple[ResearchStage, int], dict[datetime, int]] = {}
        for observation in observations:
            stage_period = self._stage_for(record, observation)
            if stage_period is None:
                continue
            stage, period = stage_period
            for horizon in (1, 5, 20):
                endpoint = observation.future_return_timestamps.get(horizon)
                target = observation.future_returns.get(horizon)
                if endpoint is None or target is None:
                    continue
                checked += 1
                if not period.start <= endpoint <= period.end:
                    outside.append(
                        f"{stage} {horizon}D {observation.symbol}@"
                        f"{observation.timestamp.isoformat()} endpoint={endpoint.isoformat()}"
                    )
                    continue
                by_time = eligible.setdefault((stage, horizon), {})
                by_time[observation.timestamp] = by_time.get(observation.timestamp, 0) + 1
        expected = {
            key: sum(count for count in by_time.values() if count >= 2)
            for key, by_time in eligible.items()
        }
        used_outside: list[str] = []
        missing_evaluation: list[str] = []
        for stage, _period in periods:
            for horizon in (1, 5, 20):
                key = (stage, horizon)
                if key not in actual:
                    missing_evaluation.append(f"{stage} {horizon}D evaluation is missing")
                    continue
                allowed = expected.get(key, 0)
                if actual[key] > allowed:
                    used_outside.append(
                        f"{stage} {horizon}D evaluated={actual[key]} boundary_eligible={allowed}"
                    )
        outside_finding = _finding(
            "AVAILABLE_FUTURE_TARGET_OUTSIDE_STAGE",
            "INFO" if outside else "PASS",
            record.research_id,
            "Some future targets exist beyond their signal's evaluation stage and are available "
            "for later stages; this is not a leak when the canonical evaluator excludes them."
            if outside
            else "All available future targets remain inside their signal's evaluation stage.",
            evidence=tuple(outside),
            checked_count=checked,
            affected_count=len(outside),
        )
        if used_outside:
            used_finding = _finding(
                "TARGET_USED_OUTSIDE_STAGE",
                "VIOLATION",
                record.research_id,
                "Evaluation counts prove that boundary-ineligible future targets entered a stage.",
                evidence=tuple(used_outside),
                checked_count=checked,
                affected_count=sum(
                    max(0, actual[key] - expected.get(key, 0))
                    for key in actual
                    if actual[key] > expected.get(key, 0)
                ),
            )
        elif missing_evaluation:
            used_finding = _finding(
                "TARGET_USED_OUTSIDE_STAGE",
                "INSUFFICIENT_EVIDENCE",
                record.research_id,
                "Canonical period evaluations are incomplete, so stage target use cannot be "
                "fully verified.",
                evidence=tuple(missing_evaluation),
                checked_count=checked,
                affected_count=len(missing_evaluation),
            )
        else:
            used_finding = _finding(
                "TARGET_USED_OUTSIDE_STAGE",
                "PASS",
                record.research_id,
                "Canonical evaluation counts match the targets eligible inside every stage.",
                checked_count=checked,
            )
        return [outside_finding, used_finding], checked

    @staticmethod
    def _fundamental_findings(
        record: FactorResearchRecord, observations: tuple[FactorObservation, ...]
    ) -> tuple[list[DataAuditFinding], int]:
        checked = 0
        invalid: list[str] = []
        for observation in observations:
            for item in observation.fundamental_inputs:
                checked += 1
                if item.value is None:
                    continue
                if (
                    item.filed_at is None
                    or item.available_at is None
                    or item.available_at < item.filed_at
                    or item.available_at > item.used_at
                ):
                    invalid.append(
                        f"{observation.symbol}.{item.field}@{item.used_at.isoformat()}: "
                        f"filed_at={item.filed_at.isoformat() if item.filed_at else 'missing'} "
                        f"available_at="
                        f"{item.available_at.isoformat() if item.available_at else 'missing'}"
                    )
        findings = [
            _finding(
                "FUNDAMENTAL_AVAILABILITY_VIOLATION",
                "VIOLATION" if invalid else "PASS",
                record.research_id,
                "Some Fundamental inputs violate filed-at, available-at, and used-at ordering."
                if invalid
                else "Every populated Fundamental input satisfies filed_at <= available_at <= "
                "used_at.",
                evidence=tuple(invalid),
                checked_count=checked,
                affected_count=len(invalid),
            )
        ]
        if record.fundamental_dataset_id is not None:
            findings.append(
                _finding(
                    "FUNDAMENTAL_RESTATEMENT_SAFETY",
                    "PASS" if record.restatement_safe else "WARNING",
                    record.fundamental_dataset_id,
                    "The Fundamental dataset is restatement-safe."
                    if record.restatement_safe
                    else "The Fundamental dataset is not restatement-safe; this is a revision "
                    "risk, not proof of future-data leakage.",
                    evidence=(
                        () if record.restatement_warning is None else (record.restatement_warning,)
                    ),
                    checked_count=1,
                    affected_count=0 if record.restatement_safe else 1,
                )
            )
        return findings, checked

    def _factor_audit(self, research_id: str) -> _AuditParts:
        record = self.factor_research.get(research_id)
        if record is None:
            raise KeyError(f"Factor research '{research_id}' was not found")
        parts = _AuditParts(
            source_fingerprints={
                f"factor_research:{record.research_id}": _model_fingerprint(record),
                f"dataset:{record.dataset_id}": record.dataset_revision,
            },
            disclosures=[
                "Factor observations and stage evaluations are obtained from the canonical "
                "Factor Engine. This audit checks time boundaries and evidence counts; it does "
                "not calculate or judge investment performance."
            ],
        )
        source_revision_available = True
        if record.factor.source_fingerprint:
            source_key = f"factor_source:{record.factor.factor_id}"
            parts.source_fingerprints[source_key] = record.factor.source_fingerprint
            current_source = self._source_file_fingerprint(record.factor.source_path)
            source_revision_available = current_source == record.factor.source_fingerprint
            source_severity: AuditSeverity = (
                "PASS"
                if source_revision_available
                else "INSUFFICIENT_EVIDENCE"
                if current_source is None
                else "WARNING"
            )
            parts.findings.append(
                _finding(
                    "FACTOR_SOURCE_REVISION_DRIFT",
                    source_severity,
                    record.factor.factor_id,
                    "The current Factor source matches the revision recorded by this research."
                    if source_revision_available
                    else "The recorded Factor source is no longer available."
                    if current_source is None
                    else "The current Factor source differs from the revision recorded by this "
                    "research; the changed source will not be used to reconstruct old evidence.",
                    evidence=(
                        f"recorded={record.factor.source_fingerprint}",
                        f"current={current_source or 'missing'}",
                        f"path={record.factor.source_path or 'missing'}",
                    ),
                    checked_count=1,
                    affected_count=0 if source_revision_available else 1,
                )
            )
        dataset = self.datasets.get(record.dataset_id)
        if dataset is None:
            parts.findings.append(
                _finding(
                    "DATASET_COVERAGE",
                    "INSUFFICIENT_EVIDENCE",
                    record.dataset_id,
                    "The Factor research dataset is no longer available for canonical audit.",
                    checked_count=1,
                    affected_count=1,
                )
            )
            return parts
        parts.findings.extend(self._dataset_findings(dataset))
        drifted = dataset.content_fingerprint != record.dataset_revision
        parts.findings.append(
            _finding(
                "DATASET_REVISION_DRIFT",
                "WARNING" if drifted else "PASS",
                record.research_id,
                "The current Dataset revision differs from the revision recorded by this Factor "
                "research."
                if drifted
                else "The current Dataset revision matches the Factor research revision.",
                evidence=(
                    f"recorded={record.dataset_revision}",
                    f"current={dataset.content_fingerprint}",
                ),
                checked_count=1,
                affected_count=1 if drifted else 0,
            )
        )
        if record.universe_id is not None:
            universe = self.universes.get(record.universe_id)
            parts.source_fingerprints[f"universe:{record.universe_id}"] = (
                "missing" if universe is None else _model_fingerprint(universe)
            )
        else:
            universe = None
        parts.findings.extend(self._universe_findings(record, dataset, universe))
        parts.findings.extend(self._corporate_action_findings(record, parts))
        if record.fundamental_dataset_id is not None:
            fundamental = self.fundamentals.get(record.fundamental_dataset_id)
            parts.source_fingerprints[f"fundamental_dataset:{record.fundamental_dataset_id}"] = (
                "missing" if fundamental is None else fundamental.content_fingerprint
            )
        try:
            if not source_revision_available:
                raise ValueError(
                    "Exact Factor source revision is unavailable; canonical evidence was not "
                    "recomputed with changed code"
                )
            observations = self.factor_engine.observations(record)
            parts.checked_observations = len(observations)
            observation_findings, dependency_count, _ = self._observation_findings(
                record, observations
            )
            parts.findings.extend(observation_findings)
            parts.checked_dependencies = dependency_count
            future_findings, future_count = self._future_boundary_findings(record, observations)
            parts.findings.extend(future_findings)
            parts.checked_future_returns = future_count
            fundamental_findings, fundamental_count = self._fundamental_findings(
                record, observations
            )
            parts.findings.extend(fundamental_findings)
            parts.checked_fundamental_inputs = fundamental_count
        except (KeyError, TypeError, ValueError) as exc:
            parts.findings.append(
                _finding(
                    "CANONICAL_FACTOR_EVIDENCE",
                    "INSUFFICIENT_EVIDENCE",
                    record.research_id,
                    "Canonical Factor observations could not be reconstructed.",
                    evidence=(str(exc),),
                    checked_count=1,
                    affected_count=1,
                )
            )
        return parts

    def _universe_findings(
        self,
        record: FactorResearchRecord,
        dataset: DatasetDefinition,
        universe: object,
    ) -> list[DataAuditFinding]:
        from app.universes import HistoricalUniverse

        resolved = universe if isinstance(universe, HistoricalUniverse) else None
        static_risk = resolved is None or resolved.mode == "STATIC"
        provenance_issues = (
            ("The referenced Universe is missing.",)
            if resolved is None
            else membership_provenance_issues(resolved.snapshots)
        )
        referenced_symbols = (
            set(record.universe)
            if resolved is None
            else {symbol for snapshot in resolved.snapshots for symbol in snapshot.symbols}
        )
        missing_market = tuple(sorted(referenced_symbols - set(dataset.symbols)))
        return [
            _finding(
                "STATIC_UNIVERSE_SURVIVORSHIP_RISK",
                "WARNING" if static_risk else "PASS",
                record.universe_id or record.research_id,
                "A STATIC Universe carries current constituents through history and remains "
                "exposed to survivorship bias."
                if static_risk
                else "The selected Universe uses explicit point-in-time snapshots.",
                evidence=(record.survivorship_warning,),
                checked_count=1,
                affected_count=1 if static_risk else 0,
            ),
            _finding(
                "UNIVERSE_MEMBERSHIP_PROVENANCE_MISSING",
                "INSUFFICIENT_EVIDENCE" if provenance_issues else "PASS",
                record.universe_id or record.research_id,
                "Some historical Universe members lack verifiable membership provenance."
                if provenance_issues
                else "Every historical Universe member has effective membership provenance.",
                evidence=tuple(provenance_issues),
                checked_count=sum(len(item.symbols) for item in resolved.snapshots)
                if resolved is not None
                else len(record.universe),
                affected_count=len(provenance_issues),
            ),
            _finding(
                "UNIVERSE_MEMBER_WITHOUT_MARKET_DATA",
                "INSUFFICIENT_EVIDENCE" if missing_market else "PASS",
                record.universe_id or record.research_id,
                "Historical Universe members are absent from the immutable Market Dataset."
                if missing_market
                else "Every referenced historical Universe member exists in the Market Dataset.",
                evidence=missing_market,
                checked_count=len(referenced_symbols),
                affected_count=len(missing_market),
            ),
        ]

    def _run_universe_findings(
        self,
        manifest: RunManifest,
        dataset: DatasetDefinition | None,
        parts: _AuditParts,
    ) -> list[DataAuditFinding]:
        universe_id = manifest.universe_id
        universe = None if universe_id is None else self.universes.get(universe_id)
        if universe_id is not None:
            parts.source_fingerprints[f"universe:{universe_id}"] = (
                "missing" if universe is None else _model_fingerprint(universe)
            )
        static_risk = universe is None or universe.mode == "STATIC"
        provenance_issues = (
            ("The referenced Universe is missing.",)
            if universe_id is not None and universe is None
            else (
                ("No explicit Historical Universe is linked to this Run.",)
                if universe is None
                else membership_provenance_issues(universe.snapshots)
            )
        )
        referenced_symbols = (
            set(manifest.dataset.symbols)
            if universe is None
            else {symbol for snapshot in universe.snapshots for symbol in snapshot.symbols}
        )
        market_symbols = set() if dataset is None else set(dataset.symbols)
        missing_market = tuple(sorted(referenced_symbols - market_symbols)) if dataset else ()
        subject = universe_id or manifest.run_id
        return [
            _finding(
                "STATIC_UNIVERSE_SURVIVORSHIP_RISK",
                "WARNING" if static_risk else "PASS",
                subject,
                "This Run has no point-in-time Universe evidence and remains exposed to "
                "survivorship bias."
                if static_risk
                else "The Run references an explicit point-in-time Universe.",
                evidence=(
                    "No explicit Historical Universe is linked to this Run."
                    if universe is None
                    else universe.disclosure,
                ),
                checked_count=1,
                affected_count=1 if static_risk else 0,
            ),
            _finding(
                "UNIVERSE_MEMBERSHIP_PROVENANCE_MISSING",
                "INSUFFICIENT_EVIDENCE" if provenance_issues else "PASS",
                subject,
                "Some Run Universe members lack verifiable membership provenance."
                if provenance_issues
                else "Every Run Universe member has effective membership provenance.",
                evidence=tuple(provenance_issues),
                checked_count=(
                    len(referenced_symbols)
                    if universe is None
                    else sum(len(item.symbols) for item in universe.snapshots)
                ),
                affected_count=len(provenance_issues),
            ),
            _finding(
                "UNIVERSE_MEMBER_WITHOUT_MARKET_DATA",
                "INSUFFICIENT_EVIDENCE" if missing_market else "PASS",
                subject,
                "Historical Run Universe members are absent from the immutable Market Dataset."
                if missing_market
                else "Every referenced Run Universe member exists in the Market Dataset.",
                evidence=missing_market,
                checked_count=len(referenced_symbols),
                affected_count=len(missing_market),
            ),
        ]

    def _run_corporate_action_findings(
        self,
        manifest: RunManifest,
        trace: BacktestTrace | None,
        parts: _AuditParts,
    ) -> list[DataAuditFinding]:
        dataset_id = manifest.corporate_action_dataset_id
        if dataset_id is None:
            return [
                _finding(
                    "CORPORATE_ACTION_DATASET_MISSING",
                    "WARNING",
                    manifest.run_id,
                    "No Corporate Action dataset is explicitly linked to this Run.",
                    evidence=(f"price_adjustment_policy={manifest.price_adjustment_policy}",),
                    checked_count=1,
                    affected_count=1,
                )
            ]
        dataset = self.corporate_actions.get(dataset_id)
        if dataset is None:
            parts.source_fingerprints[f"corporate_action_dataset:{dataset_id}"] = "missing"
            return [
                _finding(
                    "CORPORATE_ACTION_DATASET_MISSING",
                    "INSUFFICIENT_EVIDENCE",
                    dataset_id,
                    "The Corporate Action dataset linked by this Run is missing.",
                    checked_count=1,
                    affected_count=1,
                )
            ]
        parts.source_fingerprints[f"corporate_action_dataset:{dataset_id}"] = (
            dataset.content_fingerprint
        )
        late = tuple(
            item.action_id for item in dataset.actions if item.available_at > item.effective_at
        )
        unresolved_dataset = tuple(
            item.action_id
            for item in dataset.actions
            if item.action_type == "DELISTING" and item.settlement_price is None
        )
        unresolved_trace = (
            ()
            if trace is None
            else tuple(
                item.action_id
                for item in trace.corporate_action_events
                if item.status == "UNRESOLVED"
            )
        )
        unresolved_manifest = manifest.unresolved_corporate_action_ids
        unresolved_consistent = trace is None or (
            unresolved_trace == unresolved_manifest
            and set(unresolved_trace) <= set(unresolved_dataset)
        )
        findings = [
            _finding(
                "CORPORATE_ACTION_DATASET_MISSING",
                "PASS",
                dataset_id,
                "An immutable Corporate Action dataset is explicitly linked to this Run.",
                evidence=(dataset.content_fingerprint,),
                checked_count=1,
            ),
            _finding(
                "CORPORATE_ACTION_PIT_WARNING",
                "WARNING" if late else "PASS",
                dataset_id,
                "Some Corporate Actions became available after their effective time."
                if late
                else "Every Corporate Action was available no later than its effective time.",
                evidence=late,
                checked_count=len(dataset.actions),
                affected_count=len(late),
            ),
            _finding(
                "UNRESOLVED_DELISTING",
                "INSUFFICIENT_EVIDENCE" if unresolved_dataset else "PASS",
                dataset_id,
                "Some Delisting events have no reliable settlement price; no price is guessed."
                if unresolved_dataset
                else "Every recorded Delisting has explicit settlement evidence.",
                evidence=unresolved_dataset,
                checked_count=sum(item.action_type == "DELISTING" for item in dataset.actions),
                affected_count=len(unresolved_dataset),
            ),
        ]
        if trace is not None:
            findings.append(
                _finding(
                    "CORPORATE_ACTION_TRACE_CONSISTENCY",
                    "PASS" if unresolved_consistent else "INSUFFICIENT_EVIDENCE",
                    manifest.run_id,
                    "Run manifest and Trace preserve the same unresolved Corporate Action state."
                    if unresolved_consistent
                    else "Run manifest and Trace disagree about unresolved Corporate Actions.",
                    evidence=(
                        f"manifest={','.join(unresolved_manifest) or '-'}",
                        f"trace={','.join(unresolved_trace) or '-'}",
                    ),
                    checked_count=1,
                    affected_count=0 if unresolved_consistent else 1,
                )
            )
        return findings

    def _corporate_action_findings(
        self,
        record: FactorResearchRecord,
        parts: _AuditParts,
    ) -> list[DataAuditFinding]:
        dataset_id = record.corporate_action_dataset_id
        if dataset_id is None:
            return [
                _finding(
                    "CORPORATE_ACTION_DATASET_MISSING",
                    "WARNING",
                    record.research_id,
                    "No Corporate Action dataset is explicitly linked to this Factor research.",
                    evidence=(f"price_adjustment_policy={record.price_adjustment_policy}",),
                    checked_count=1,
                    affected_count=1,
                )
            ]
        dataset = self.corporate_actions.get(dataset_id)
        if dataset is None:
            parts.source_fingerprints[f"corporate_action_dataset:{dataset_id}"] = "missing"
            return [
                _finding(
                    "CORPORATE_ACTION_DATASET_MISSING",
                    "INSUFFICIENT_EVIDENCE",
                    dataset_id,
                    "The explicitly linked Corporate Action dataset is missing.",
                    checked_count=1,
                    affected_count=1,
                )
            ]
        parts.source_fingerprints[f"corporate_action_dataset:{dataset_id}"] = (
            dataset.content_fingerprint
        )
        late = tuple(
            item.action_id for item in dataset.actions if item.available_at > item.effective_at
        )
        unresolved = tuple(
            item.action_id
            for item in dataset.actions
            if item.action_type == "DELISTING" and item.settlement_price is None
        )
        return [
            _finding(
                "CORPORATE_ACTION_DATASET_MISSING",
                "PASS",
                dataset_id,
                "An immutable Corporate Action dataset is explicitly linked to this research.",
                evidence=(dataset.content_fingerprint,),
                checked_count=1,
            ),
            _finding(
                "CORPORATE_ACTION_PIT_WARNING",
                "WARNING" if late else "PASS",
                dataset_id,
                "Some Corporate Actions became available after their effective time."
                if late
                else "Every Corporate Action was available no later than its effective time.",
                evidence=late,
                checked_count=len(dataset.actions),
                affected_count=len(late),
            ),
            _finding(
                "UNRESOLVED_DELISTING",
                "INSUFFICIENT_EVIDENCE" if unresolved else "PASS",
                dataset_id,
                "Some Delisting events have no reliable settlement price; no price is guessed."
                if unresolved
                else "Every recorded Delisting has explicit settlement evidence.",
                evidence=unresolved,
                checked_count=sum(item.action_type == "DELISTING" for item in dataset.actions),
                affected_count=len(unresolved),
            ),
        ]

    @staticmethod
    def _dependency_finding(
        subject: str, dependencies: tuple[DataDependency, ...]
    ) -> DataAuditFinding:
        violations = tuple(
            f"{item.dependency_id}: available_at={item.available_at.isoformat()} "
            f"used_at={item.used_at.isoformat()}"
            for item in dependencies
            if item.available_at > item.used_at
        )
        return _finding(
            "DEPENDENCY_LOOK_AHEAD",
            "VIOLATION" if violations else "PASS",
            subject,
            "Some Trace dependencies were used before they became available."
            if violations
            else "Every Trace dependency was available when it was used.",
            evidence=violations,
            checked_count=len(dependencies),
            affected_count=len(violations),
        )

    def _run_audit(self, run_id: str) -> _AuditParts:
        try:
            manifest = self.runs.get_manifest(run_id)
        except RunNotFoundError as exc:
            raise KeyError(f"Run '{run_id}' was not found") from exc
        parts = _AuditParts(
            source_fingerprints={
                f"run:{manifest.run_id}": manifest.run_fingerprint,
                f"dataset:{manifest.dataset.dataset_id}": manifest.dataset.content_fingerprint,
            },
            disclosures=[
                "Run checks audit the immutable manifest, Trace dependencies, recorded period, "
                "and Dataset revision. They do not evaluate profitability or certify research "
                "outside this Run."
            ],
        )
        dataset = self.datasets.get(manifest.dataset.dataset_id)
        if dataset is None:
            parts.findings.append(
                _finding(
                    "DATASET_REVISION_DRIFT",
                    "INSUFFICIENT_EVIDENCE",
                    manifest.dataset.dataset_id,
                    "The Run Dataset is no longer registered, so its current revision cannot be "
                    "verified.",
                    checked_count=1,
                    affected_count=1,
                )
            )
        else:
            matches = dataset.content_fingerprint == manifest.dataset.content_fingerprint
            parts.findings.append(
                _finding(
                    "DATASET_REVISION_DRIFT",
                    "PASS" if matches else "WARNING",
                    run_id,
                    "The current Dataset revision matches the Run manifest."
                    if matches
                    else "The current Dataset revision differs from the immutable Run manifest.",
                    evidence=(
                        f"recorded={manifest.dataset.content_fingerprint}",
                        f"current={dataset.content_fingerprint}",
                    ),
                    checked_count=1,
                    affected_count=0 if matches else 1,
                )
            )
        parts.findings.extend(self._run_universe_findings(manifest, dataset, parts))
        try:
            trace = self.runs.load_trace_for_run(run_id)
        except RunNotFoundError:
            trace = None
        parts.findings.extend(self._run_corporate_action_findings(manifest, trace, parts))
        if trace is None:
            parts.findings.append(
                _finding(
                    "TRACE_DEPENDENCY_EVIDENCE",
                    "INSUFFICIENT_EVIDENCE",
                    run_id,
                    "The Run has no readable Trace dependency evidence.",
                    checked_count=1,
                    affected_count=1,
                )
            )
            return parts
        if manifest.artifacts.trace_sha256 is not None:
            parts.source_fingerprints[f"trace:{run_id}"] = manifest.artifacts.trace_sha256
        dependencies = tuple(
            dependency for event in trace.timeline for dependency in event.data_dependencies
        )
        parts.checked_dependencies = len(dependencies)
        dependency_finding = self._dependency_finding(run_id, dependencies)
        diagnostics = collect_look_ahead_diagnostics(trace)
        if diagnostics and dependency_finding.affected_count != len(diagnostics):
            dependency_finding = dependency_finding.model_copy(
                update={
                    "severity": "INSUFFICIENT_EVIDENCE",
                    "reason": "Trace dependency checks and canonical look-ahead diagnostics "
                    "returned inconsistent counts.",
                    "evidence": tuple(item.message for item in diagnostics[:EVIDENCE_LIMIT]),
                }
            )
        parts.findings.append(dependency_finding)
        period_start = manifest.period.start
        period_end = manifest.period.end
        coverage_ok = False
        if period_start is not None and period_end is not None:
            coverage_ok = (
                trace.metadata.data_start <= period_start <= period_end <= trace.metadata.data_end
            )
            if dataset is not None:
                coverage_ok = coverage_ok and (
                    dataset.start_time <= period_start <= period_end <= dataset.end_time
                )
        parts.findings.append(
            _finding(
                "RUN_PERIOD_COVERAGE",
                "PASS" if coverage_ok else "INSUFFICIENT_EVIDENCE",
                run_id,
                "The Run period is covered by its Trace and current Dataset boundaries."
                if coverage_ok
                else "The Run period is missing or is not fully supported by saved coverage "
                "boundaries.",
                evidence=(
                    f"run={manifest.period.start}..{manifest.period.end}",
                    f"trace={trace.metadata.data_start.isoformat()}.."
                    f"{trace.metadata.data_end.isoformat()}",
                ),
                checked_count=1,
                affected_count=0 if coverage_ok else 1,
            )
        )
        return parts

    def create(self, request: CreateDataAudit) -> DataAuditRecord:
        self._validate_root(request)
        if request.root_type == "DATASET":
            parts = self._dataset_audit(request.root_id)
        elif request.root_type == "FACTOR_RESEARCH":
            parts = self._factor_audit(request.root_id)
        else:
            parts = self._run_audit(request.root_id)
        record = DataAuditRecord(
            audit_id=f"data-audit-{secrets.token_hex(10)}",
            root_type=request.root_type,
            root_id=request.root_id,
            created_at=datetime.now(UTC),
            source_fingerprints=dict(sorted(parts.source_fingerprints.items())),
            status=_status(parts.findings),
            findings=tuple(parts.findings),
            checked_observations=parts.checked_observations,
            checked_dependencies=parts.checked_dependencies,
            checked_future_returns=parts.checked_future_returns,
            checked_fundamental_inputs=parts.checked_fundamental_inputs,
            disclosures=tuple(parts.disclosures),
        )
        return self.audits.save(record)

    def _current_fingerprint(self, key: str, audit: DataAuditRecord) -> str | None:
        kind, separator, artifact_id = key.partition(":")
        if not separator or not artifact_id:
            return None
        if kind == "dataset":
            dataset = self.datasets.get(artifact_id)
            return None if dataset is None else dataset.content_fingerprint
        if kind == "factor_research":
            record = self.factor_research.get(artifact_id)
            return None if record is None else _model_fingerprint(record)
        if kind == "factor_source" and audit.root_type == "FACTOR_RESEARCH":
            record = self.factor_research.get(audit.root_id)
            if record is None or record.factor.factor_id != artifact_id:
                return None
            return self._source_file_fingerprint(record.factor.source_path)
        if kind == "fundamental_dataset":
            fundamental_dataset = self.fundamentals.get(artifact_id)
            return None if fundamental_dataset is None else fundamental_dataset.content_fingerprint
        if kind == "universe":
            universe = self.universes.get(artifact_id)
            return None if universe is None else _model_fingerprint(universe)
        if kind == "corporate_action_dataset":
            corporate_actions = self.corporate_actions.get(artifact_id)
            return None if corporate_actions is None else corporate_actions.content_fingerprint
        if kind == "run":
            try:
                return self.runs.get_manifest(artifact_id).run_fingerprint
            except (RunNotFoundError, ArtifactIntegrityError, ValueError, OSError):
                return None
        if kind == "trace":
            try:
                return self.runs.get_manifest(artifact_id).artifacts.trace_sha256
            except (RunNotFoundError, ArtifactIntegrityError, ValueError, OSError):
                return None
        return None

    def verify_source(self, audit_id: str) -> DataAuditSourceVerification:
        record = self.audits.get(audit_id)
        if record is None:
            raise KeyError(f"Data Audit '{audit_id}' was not found")
        current = {
            key: fingerprint
            for key in record.source_fingerprints
            if (fingerprint := self._current_fingerprint(key, record)) is not None
        }
        missing = set(record.source_fingerprints) - set(current)
        changed = any(
            current.get(key) != value for key, value in record.source_fingerprints.items()
        )
        state: AuditSourceState = "MISSING" if missing else "CHANGED" if changed else "MATCHES"
        return DataAuditSourceVerification(
            audit_id=record.audit_id,
            source_state=state,
            recorded_source_fingerprints=record.source_fingerprints,
            current_source_fingerprints=dict(sorted(current.items())),
        )

    def detail(self, audit_id: str) -> DataAuditDetail:
        record = self.audits.get(audit_id)
        if record is None:
            raise KeyError(f"Data Audit '{audit_id}' was not found")
        verification = self.verify_source(audit_id)
        return DataAuditDetail(
            audit=record,
            source_state=verification.source_state,
            current_source_fingerprints=verification.current_source_fingerprints,
        )
