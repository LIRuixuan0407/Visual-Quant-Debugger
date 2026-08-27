from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.datasets import DatasetRegistry
from app.discovery.models import ResearchHypothesis
from app.discovery.repository import HypothesisRepository
from app.factor_relationships.repository import FactorRelationshipRepository
from app.factors.models import FactorResearchRecord, ResearchPeriod, ResearchStage
from app.factors.repository import FactorResearchRepository
from app.portfolio_lab.models import PortfolioResearchRecord
from app.portfolio_lab.repository import PortfolioResearchRepository
from app.portfolio_lab.strategy_factory import PortfolioStrategyFactory
from app.research_ledger import ResearchLedgerEntry, ResearchLedgerRepository
from app.runs import RunRepository
from app.runs.models import RunManifest
from app.runs.repository import ArtifactIntegrityError, RunNotFoundError
from app.sdk.registry import StrategyRegistry
from app.walk_forward.repository import WalkForwardRepository

from .models import (
    HypothesisIntegrityReport,
    HypothesisIntegritySummary,
    IntegrityCheckCode,
    IntegrityFinding,
    IntegritySeverity,
    IntegrityStatus,
    WorkspaceIntegrityReport,
)

# Ledger events that may legitimately follow REVEAL_HOLDOUT for the same
# hypothesis. Creating a new revision is the sanctioned way to keep researching.
_POST_REVEAL_SANCTIONED_EVENTS = {"CREATE_NATIVE_STRATEGY", "ATTACH_RUN"}
_PRE_REVEAL_STATUSES = {"DRAFT", "RESEARCHED", "VALIDATED"}


class _CheckCollector:
    """Collects findings; every code without a problem gets one explicit PASS row."""

    def __init__(self) -> None:
        self._findings: list[IntegrityFinding] = []

    def issue(
        self,
        code: IntegrityCheckCode,
        severity: IntegritySeverity,
        subject: str,
        reason: str,
        evidence: tuple[str, ...] = (),
    ) -> None:
        self._findings = [item for item in self._findings if item.code != code]
        self._findings.append(
            IntegrityFinding(
                code=code,
                severity=severity,
                subject=subject,
                reason=reason,
                evidence=evidence,
            )
        )

    def ok(self, code: IntegrityCheckCode, subject: str, reason: str) -> None:
        if code not in {item.code for item in self._findings}:
            self._findings.append(
                IntegrityFinding(
                    code=code,
                    severity="PASS",
                    subject=subject,
                    reason=reason,
                )
            )

    def resolve(self) -> tuple[IntegrityFinding, ...]:
        return tuple(self._findings)


def _overall_status(violations: int, warnings: int) -> IntegrityStatus:
    if violations:
        return "VIOLATION"
    return "WARNING" if warnings else "PASS"


def _factor_revision(factor: FactorResearchRecord) -> str:
    return factor.factor.source_fingerprint or factor.factor.version


class ResearchIntegrityEngine:
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
        ledger: ResearchLedgerRepository,
    ) -> None:
        self.datasets = datasets
        self.factors = factors
        self.relationships = relationships
        self.walk_forward = walk_forward
        self.hypotheses = hypotheses
        self.portfolios = portfolios
        self.strategies = strategies
        self.runs = runs
        self.ledger = ledger

    # -- helpers ---------------------------------------------------------

    def _hypothesis_entries(self, hypothesis_id: str) -> tuple[ResearchLedgerEntry, ...]:
        return tuple(
            item
            for item in self.ledger.list()
            if item.kind == "HYPOTHESIS" and item.artifact_id == hypothesis_id
        )

    def _manifest(self, run_id: str) -> RunManifest | None:
        try:
            return self.runs.get_manifest(run_id)
        except (RunNotFoundError, ArtifactIntegrityError):
            return None

    def _current_factor_revisions(self, record: ResearchHypothesis) -> tuple[str, ...]:
        revisions: list[str] = []
        for research_id in record.factor_research_ids:
            factor = self.factors.get(research_id)
            if factor is not None:
                revisions.append(_factor_revision(factor))
        return tuple(revisions)

    def _stage_period(self, factor: FactorResearchRecord, stage: ResearchStage) -> ResearchPeriod:
        return {
            "RESEARCH": factor.periods.research,
            "VALIDATION": factor.periods.validation,
            "HOLDOUT": factor.periods.holdout,
        }[stage]

    def _holdout_boundary(self, record: ResearchHypothesis) -> ResearchPeriod | None:
        for research_id in record.factor_research_ids:
            factor = self.factors.get(research_id)
            if factor is not None:
                return factor.periods.holdout
        return None

    def _strategy_source_fingerprint(
        self, portfolio: PortfolioResearchRecord, strategy_id: str
    ) -> str | None:
        """Regenerate the strategy source the Portfolio must produce and fingerprint it.

        Reuses the real PortfolioStrategyFactory generator so the guardrail can never
        drift from the actual Portfolio-to-Strategy semantics; nothing is registered
        or written.
        """

        factory = PortfolioStrategyFactory(
            self.strategies,
            self.factors,
            self.datasets.workspace_root,
        )
        try:
            source = factory._source(portfolio, strategy_id)
        except KeyError:
            return None
        return f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"

    # -- individual checks -------------------------------------------------

    def _check_post_holdout_modification(
        self, record: ResearchHypothesis, collector: _CheckCollector
    ) -> None:
        subject = record.hypothesis_id
        entries = self._hypothesis_entries(subject)
        if not entries:
            collector.issue(
                "POST_HOLDOUT_MODIFICATION",
                "VIOLATION",
                subject,
                "The Hypothesis has no research ledger entries, so its mutation history "
                "cannot be verified.",
            )
            return

        reveal_times = [
            item.created_at for item in entries if item.metadata.get("event") == "REVEAL_HOLDOUT"
        ]
        if reveal_times:
            first_reveal = min(reveal_times)
            modified = [
                item
                for item in entries
                if item.created_at > first_reveal
                and item.metadata.get("event") not in _POST_REVEAL_SANCTIONED_EVENTS
            ]
            if modified:
                collector.issue(
                    "POST_HOLDOUT_MODIFICATION",
                    "VIOLATION",
                    subject,
                    "The same experiment was modified after its Holdout had been revealed; "
                    "only Strategy creation and Run attachment may follow the reveal, and a "
                    "new revision is the sanctioned way to change the experiment.",
                    evidence=tuple(
                        f"{item.metadata.get('event')} event at "
                        f"{item.created_at.isoformat()} modified this hypothesis after "
                        "Holdout reveal"
                        for item in modified
                    ),
                )
                return

        current_revisions = self._current_factor_revisions(record)
        # Ledger events can share microsecond timestamps, so "latest" is ambiguous on
        # ties. The disciplined flow records every state change, so the current record
        # must match at least one recorded event; only flag drift when none does.
        recorded_states = tuple(
            (
                item.revision,
                item.dataset_fingerprints,
                item.metadata.get("status"),
                item.strategy_id,
                item.portfolio_research_id,
                tuple(item.factor_ids),
                tuple(item.factor_revisions),
            )
            for item in entries
        )
        current_state = (
            record.revision,
            (record.dataset_fingerprint,),
            record.status,
            record.lineage.strategy_id,
            record.lineage.portfolio_research_id,
            record.lineage.factor_ids,
            current_revisions if len(current_revisions) == len(record.factor_research_ids) else (),
        )
        if current_state not in recorded_states:
            latest = max(entries, key=lambda item: item.created_at)
            drift: list[str] = []
            if latest.revision != record.revision:
                drift.append(
                    f"revision recorded {latest.revision} but current record is {record.revision}"
                )
            if record.dataset_fingerprint not in latest.dataset_fingerprints:
                drift.append(
                    "dataset fingerprint recorded "
                    f"{', '.join(latest.dataset_fingerprints) or 'none'} "
                    f"but current record uses {record.dataset_fingerprint}"
                )
            if latest.metadata.get("status") != record.status:
                drift.append(
                    f"status recorded {latest.metadata.get('status')} but current record is "
                    f"{record.status}"
                )
            if latest.strategy_id != record.lineage.strategy_id:
                drift.append(
                    f"strategy recorded {latest.strategy_id} but current lineage uses "
                    f"{record.lineage.strategy_id}"
                )
            if latest.portfolio_research_id != record.lineage.portfolio_research_id:
                drift.append(
                    f"portfolio recorded {latest.portfolio_research_id} but current lineage "
                    f"uses {record.lineage.portfolio_research_id}"
                )
            if tuple(latest.factor_ids) != record.lineage.factor_ids:
                drift.append(
                    f"factor ids recorded {', '.join(latest.factor_ids) or 'none'} but current "
                    f"lineage uses {', '.join(record.lineage.factor_ids) or 'none'}"
                )
            if (
                len(current_revisions) == len(latest.factor_revisions)
                and tuple(latest.factor_revisions) != current_revisions
            ):
                drift.append(
                    f"factor revisions recorded {', '.join(latest.factor_revisions) or 'none'} "
                    f"but current factors are {', '.join(current_revisions) or 'none'}"
                )
            collector.issue(
                "POST_HOLDOUT_MODIFICATION",
                "VIOLATION",
                subject,
                "The Hypothesis changed without a matching research ledger event, so the "
                "change was applied outside the disciplined revision flow.",
                evidence=tuple(drift),
            )
            return
        collector.ok(
            "POST_HOLDOUT_MODIFICATION",
            subject,
            "The experiment was not modified after Holdout reveal; later revisions live in "
            "separate hypothesis records and every recorded change has a matching ledger "
            "event.",
        )

    def _check_future_data_leak(
        self, record: ResearchHypothesis, collector: _CheckCollector
    ) -> None:
        subject = record.hypothesis_id
        dataset = self.datasets.get(record.dataset_id)
        hard: list[str] = []
        soft: list[str] = []
        if record.created_with_known_stage == "HOLDOUT":
            soft.append(
                "hypothesis was created after its Factor research Holdout was already revealed"
            )

        for research_id in record.factor_research_ids:
            factor = self.factors.get(research_id)
            if factor is None:
                continue
            if (
                dataset is not None
                and factor.periods.holdout.end is not None
                and factor.periods.holdout.end > dataset.end_time
            ):
                hard.append(
                    f"factor research '{research_id}' Holdout period ends after the dataset "
                    f"coverage end {dataset.end_time.isoformat()}"
                )
            for evaluation in factor.evaluations:
                declared = self._stage_period(factor, evaluation.stage)
                if (
                    evaluation.period.start != declared.start
                    or evaluation.period.end != declared.end
                ):
                    hard.append(
                        f"factor research '{research_id}' {evaluation.stage} evaluation period "
                        "does not match its declared stage boundaries"
                    )
                for horizon in evaluation.horizons:
                    for point in horizon.timeline:
                        if not (declared.start <= point.timestamp <= declared.end):
                            hard.append(
                                f"factor research '{research_id}' {evaluation.stage} evaluation "
                                f"timeline reaches outside its {evaluation.stage} window at "
                                f"{point.timestamp.isoformat()}"
                            )
            for observation in factor.sample_observations:
                if observation.available_at < observation.window_end:
                    hard.append(
                        f"factor research '{research_id}' observation at "
                        f"{observation.timestamp.isoformat()} claims availability at "
                        f"{observation.available_at.isoformat()} before its input window "
                        "closed"
                    )
            if not factor.restatement_safe:
                soft.append(
                    f"factor research '{research_id}' is not restatement safe: "
                    f"{factor.restatement_warning or 'fundamental inputs may be revised'}"
                )

        holdout = self._holdout_boundary(record)
        for run_id in record.lineage.run_ids:
            manifest = self._manifest(run_id)
            if manifest is None:
                continue
            if dataset is not None:
                if manifest.period.end is not None and manifest.period.end > dataset.end_time:
                    hard.append(
                        f"run '{run_id}' period ends after the dataset coverage end "
                        f"{dataset.end_time.isoformat()}"
                    )
                if manifest.period.start is not None and manifest.period.start < (
                    dataset.start_time
                ):
                    hard.append(
                        f"run '{run_id}' period starts before the dataset coverage start "
                        f"{dataset.start_time.isoformat()}"
                    )
            if (
                manifest.period.cutoff is not None
                and manifest.period.end is not None
                and manifest.period.end > manifest.period.cutoff
            ):
                hard.append(
                    f"run '{run_id}' period end {manifest.period.end.isoformat()} exceeds its "
                    f"declared research cutoff {manifest.period.cutoff.isoformat()}"
                )
            if (
                holdout is not None
                and record.status in _PRE_REVEAL_STATUSES
                and manifest.period.end is not None
                and manifest.period.end > holdout.start
            ):
                hard.append(f"run '{run_id}' covers the Holdout window before Holdout was revealed")

        if hard:
            collector.issue(
                "FUTURE_DATA_LEAK",
                "VIOLATION",
                subject,
                "Research or Run evidence crosses a point-in-time boundary: stage windows, "
                "dataset coverage, run cutoffs, or the Holdout reveal order.",
                evidence=tuple((*hard, *soft)),
            )
            return
        if soft:
            collector.issue(
                "FUTURE_DATA_LEAK",
                "WARNING",
                subject,
                "The experiment was defined with Holdout already revealed or uses inputs that "
                "are not restatement safe.",
                evidence=tuple(soft),
            )
            return
        collector.ok(
            "FUTURE_DATA_LEAK",
            subject,
            "All Factor evaluation timelines and Run periods stay inside their declared "
            "stage windows, dataset coverage, and research cutoffs, and the hypothesis was "
            "created before Holdout was revealed.",
        )

    def _check_dataset_silent_change(
        self, record: ResearchHypothesis, collector: _CheckCollector
    ) -> None:
        subject = record.hypothesis_id
        dataset = self.datasets.get(record.dataset_id)
        changes: list[str] = []
        if dataset is None:
            changes.append(f"dataset '{record.dataset_id}' is no longer registered")
        elif dataset.content_fingerprint != record.dataset_fingerprint:
            changes.append(
                f"dataset fingerprint drifted from {record.dataset_fingerprint} to "
                f"{dataset.content_fingerprint}"
            )
        for research_id in record.factor_research_ids:
            factor = self.factors.get(research_id)
            if factor is None:
                continue
            if factor.dataset_revision != record.dataset_fingerprint:
                changes.append(
                    f"factor research '{research_id}' was computed on dataset revision "
                    f"{factor.dataset_revision}"
                )
        for run_id in record.lineage.run_ids:
            manifest = self._manifest(run_id)
            if manifest is None:
                continue
            if manifest.dataset.dataset_id != record.dataset_id:
                changes.append(
                    f"run '{run_id}' was executed on dataset '{manifest.dataset.dataset_id}' "
                    f"instead of '{record.dataset_id}'"
                )
            if manifest.dataset.content_fingerprint != record.dataset_fingerprint:
                changes.append(
                    f"run '{run_id}' was executed on dataset revision "
                    f"{manifest.dataset.content_fingerprint}"
                )
        if changes:
            collector.issue(
                "DATASET_SILENT_CHANGE",
                "VIOLATION",
                subject,
                "The dataset behind this hypothesis changed or no longer matches the "
                "revision the research was computed on.",
                evidence=tuple(changes),
            )
            return
        collector.ok(
            "DATASET_SILENT_CHANGE",
            subject,
            "The current dataset revision still matches the Hypothesis, its Factor "
            "research, and every attached Run.",
        )

    def _check_strategy_semantics(
        self, record: ResearchHypothesis, collector: _CheckCollector
    ) -> None:
        subject = record.hypothesis_id
        mismatches: list[str] = []
        portfolio = (
            None
            if record.lineage.portfolio_research_id is None
            else self.portfolios.get(record.lineage.portfolio_research_id)
        )
        if portfolio is not None:
            if portfolio.rebalance != record.rebalance_idea:
                mismatches.append(
                    f"portfolio rebalances {portfolio.rebalance} while the hypothesis defines "
                    f"{record.rebalance_idea}"
                )
            if set(item.research_id for item in portfolio.factor_refs) != set(
                record.factor_research_ids
            ):
                mismatches.append(
                    "portfolio factor references do not match the hypothesis Factor research"
                )
            candidate_fields: tuple[tuple[str, object, object], ...] = (
                ("combination", portfolio.combination, record.candidate.combination),
                ("selection", portfolio.construction.selection, record.candidate.selection),
                ("top percent", portfolio.construction.top_percent, record.candidate.top_percent),
                ("weighting", portfolio.construction.weighting, record.candidate.weighting),
                (
                    "max single position weight",
                    portfolio.construction.max_single_position_weight,
                    record.candidate.max_single_position_weight,
                ),
            )
            for field, actual, expected in candidate_fields:
                if actual != expected:
                    mismatches.append(
                        f"portfolio {field} is {actual} while the hypothesis candidate "
                        f"defines {expected}"
                    )
            if set(portfolio.filters.include_symbols) != set(record.universe):
                mismatches.append(
                    "portfolio universe filter does not match the hypothesis universe"
                )
            if record.lineage.strategy_id is not None:
                if portfolio.strategy is None:
                    mismatches.append(
                        f"portfolio has no Native Strategy while lineage claims "
                        f"'{record.lineage.strategy_id}'"
                    )
                elif portfolio.strategy.strategy_id != record.lineage.strategy_id:
                    mismatches.append(
                        f"portfolio strategy '{portfolio.strategy.strategy_id}' does not "
                        f"match lineage strategy '{record.lineage.strategy_id}'"
                    )
                if portfolio.strategy is not None:
                    registration = self.strategies.get_registration(record.lineage.strategy_id)
                    if registration is None:
                        mismatches.append(
                            f"strategy '{record.lineage.strategy_id}' has no registration revision"
                        )
                    else:
                        if registration.source_fingerprint != portfolio.strategy.source_fingerprint:
                            mismatches.append(
                                "registered strategy source fingerprint no longer matches the "
                                "strategy created from this research"
                            )
                        if record.lineage.strategy_id.startswith("portfolio-"):
                            expected_source = self._strategy_source_fingerprint(
                                portfolio, record.lineage.strategy_id
                            )
                            if (
                                expected_source is not None
                                and expected_source != registration.source_fingerprint
                            ):
                                mismatches.append(
                                    f"portfolio configuration changed after the Native "
                                    f"Strategy was generated: strategy "
                                    f"'{record.lineage.strategy_id}' no longer matches the "
                                    "current Portfolio semantics"
                                )
        for run_id in record.lineage.run_ids:
            manifest = self._manifest(run_id)
            if manifest is None or portfolio is None or portfolio.strategy is None:
                continue
            if manifest.strategy.strategy_id != record.lineage.strategy_id:
                mismatches.append(
                    f"run '{run_id}' uses strategy '{manifest.strategy.strategy_id}' instead "
                    f"of '{record.lineage.strategy_id}'"
                )
            if manifest.strategy.source_fingerprint != portfolio.strategy.source_fingerprint:
                mismatches.append(
                    f"run '{run_id}' uses strategy source revision "
                    f"{manifest.strategy.source_fingerprint} instead of "
                    f"{portfolio.strategy.source_fingerprint}"
                )
            cost_fields: tuple[tuple[str, float], ...] = (
                ("fee_bps", portfolio.fee_bps),
                ("slippage_bps", portfolio.slippage_bps),
                ("initial_cash", portfolio.initial_cash),
                ("gross_notional", portfolio.gross_notional),
            )
            for key, expected in cost_fields:
                actual = manifest.parameters.get(key)
                if actual is not None and float(actual) != float(expected):
                    mismatches.append(
                        f"run '{run_id}' executed with {key} {actual} while the research "
                        f"portfolio defines {expected}"
                    )
        if mismatches:
            collector.issue(
                "STRATEGY_SEMANTIC_MISMATCH",
                "VIOLATION",
                subject,
                "The executed Strategy no longer expresses the semantics of the research "
                "hypothesis it claims to implement.",
                evidence=tuple(mismatches),
            )
            return
        collector.ok(
            "STRATEGY_SEMANTIC_MISMATCH",
            subject,
            "The Portfolio, Native Strategy, and attached Runs still express the recorded "
            "hypothesis semantics with matching source revisions and cost parameters.",
        )

    def _check_missing_lineage(
        self, record: ResearchHypothesis, collector: _CheckCollector
    ) -> None:
        subject = record.hypothesis_id
        missing: list[str] = []
        for research_id in record.factor_research_ids:
            if self.factors.get(research_id) is None:
                missing.append(f"factor research '{research_id}' is missing")
        for relationship_id in record.lineage.relationship_ids:
            if self.relationships.get(relationship_id) is None:
                missing.append(f"factor relationship '{relationship_id}' is missing")
        for walk_forward_id in record.lineage.walk_forward_ids:
            if self.walk_forward.get(walk_forward_id) is None:
                missing.append(f"walk-forward research '{walk_forward_id}' is missing")
        if record.lineage.portfolio_research_id is not None:
            if self.portfolios.get(record.lineage.portfolio_research_id) is None:
                missing.append(
                    f"portfolio research '{record.lineage.portfolio_research_id}' is missing"
                )
        elif record.status in {"RESEARCHED", "VALIDATED", "HOLDOUT_REVEALED", "STRATEGY_CREATED"}:
            missing.append(
                f"status {record.status} requires a Portfolio research record in lineage"
            )
        if record.status == "STRATEGY_CREATED" and record.lineage.strategy_id is None:
            missing.append("status STRATEGY_CREATED requires a Native Strategy in lineage")
        if len(record.lineage.run_ids) != len(record.lineage.trace_ids):
            missing.append("attached Run and Trace identifiers are not matched pairs")
        if record.status == "STRATEGY_CREATED" and not record.lineage.run_ids:
            missing.append(
                "status STRATEGY_CREATED requires at least one attached Run / Trace pair"
            )
        for run_id in record.lineage.run_ids:
            if self._manifest(run_id) is None:
                missing.append(f"run '{run_id}' is missing from the Run store")
        for run_id, trace_id in zip(record.lineage.run_ids, record.lineage.trace_ids, strict=False):
            manifest = self._manifest(run_id)
            if manifest is None or manifest.trace_id is None:
                continue
            if manifest.trace_id != trace_id:
                missing.append(f"run '{run_id}' does not own Trace '{trace_id}'")
                continue
            if self.runs.run_id_for_trace(trace_id) != run_id:
                missing.append(f"Trace '{trace_id}' belongs to a different Run")
            try:
                self.runs.load_trace_for_run(run_id)
            except (RunNotFoundError, ArtifactIntegrityError, ValueError, OSError):
                missing.append(f"run '{run_id}' has no readable Trace artifact")
        if missing:
            collector.issue(
                "MISSING_LINEAGE",
                "VIOLATION",
                subject,
                "The recorded research lineage references records that no longer exist or "
                "are required by the current lifecycle status.",
                evidence=tuple(missing),
            )
            return
        collector.ok(
            "MISSING_LINEAGE",
            subject,
            "Every lineage reference (Factor, Relationship, Walk-Forward, Portfolio, "
            "Strategy, Run, Trace) resolves to a stored record with matched ownership.",
        )

    def _check_missing_revision(
        self, record: ResearchHypothesis, collector: _CheckCollector
    ) -> None:
        subject = record.hypothesis_id
        missing: list[str] = []
        for research_id in record.factor_research_ids:
            factor = self.factors.get(research_id)
            if factor is None:
                continue
            if not _factor_revision(factor):
                missing.append(f"factor research '{research_id}' has no revision identity")
        if record.lineage.strategy_id is not None:
            registration = self.strategies.get_registration(record.lineage.strategy_id)
            if registration is None:
                missing.append(
                    f"strategy '{record.lineage.strategy_id}' has no registration revision"
                )
        if not self._hypothesis_entries(record.hypothesis_id):
            missing.append("hypothesis has no research ledger revision entries")
        if missing:
            collector.issue(
                "MISSING_REVISION",
                "WARNING",
                subject,
                "Some lineage records cannot be pinned to an exact revision, which weakens "
                "reproducibility.",
                evidence=tuple(missing),
            )
            return
        collector.ok(
            "MISSING_REVISION",
            subject,
            "All lineage records carry an exact revision identity and the hypothesis is "
            "covered by research ledger entries.",
        )

    # -- public API --------------------------------------------------------

    def audit(self, hypothesis_id: str) -> HypothesisIntegrityReport:
        record = self.hypotheses.get(hypothesis_id)
        if record is None:
            raise KeyError(f"Hypothesis '{hypothesis_id}' was not found")
        collector = _CheckCollector()
        self._check_post_holdout_modification(record, collector)
        self._check_future_data_leak(record, collector)
        self._check_dataset_silent_change(record, collector)
        self._check_strategy_semantics(record, collector)
        self._check_missing_lineage(record, collector)
        self._check_missing_revision(record, collector)
        findings = collector.resolve()
        violations = sum(1 for item in findings if item.severity == "VIOLATION")
        warnings = sum(1 for item in findings if item.severity == "WARNING")
        return HypothesisIntegrityReport(
            hypothesis_id=record.hypothesis_id,
            family_id=record.family_id,
            title=record.title,
            revision=record.revision,
            lifecycle_status=record.status,
            checked_at=datetime.now(UTC),
            findings=findings,
            violation_count=violations,
            warning_count=warnings,
            overall_status=_overall_status(violations, warnings),
        )

    def overview(self) -> WorkspaceIntegrityReport:
        summaries: list[HypothesisIntegritySummary] = []
        for record in self.hypotheses.list():
            report = self.audit(record.hypothesis_id)
            summaries.append(
                HypothesisIntegritySummary(
                    hypothesis_id=report.hypothesis_id,
                    family_id=report.family_id,
                    title=report.title,
                    revision=report.revision,
                    lifecycle_status=report.lifecycle_status,
                    overall_status=report.overall_status,
                    violation_count=report.violation_count,
                    warning_count=report.warning_count,
                )
            )
        violations = sum(item.violation_count for item in summaries)
        warnings = sum(item.warning_count for item in summaries)
        return WorkspaceIntegrityReport(
            generated_at=datetime.now(UTC),
            hypotheses=tuple(summaries),
            overall_status=_overall_status(violations, warnings),
            total_violations=violations,
            total_warnings=warnings,
        )
