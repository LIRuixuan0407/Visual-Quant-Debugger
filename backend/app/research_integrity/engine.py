from __future__ import annotations

from datetime import UTC, datetime

from app.datasets import DatasetRegistry
from app.discovery.models import ResearchHypothesis
from app.discovery.repository import HypothesisRepository
from app.factor_relationships.repository import FactorRelationshipRepository
from app.factors.repository import FactorResearchRepository
from app.portfolio_lab.repository import PortfolioResearchRepository
from app.research_ledger import ResearchLedgerEntry, ResearchLedgerRepository
from app.runs import RunRepository
from app.runs.models import RunManifest
from app.runs.repository import ArtifactIntegrityError, RunNotFoundError
from app.sdk.registry import StrategyRegistry
from app.walk_forward.repository import WalkForwardRepository

from .models import (
    HypothesisIntegrityReport,
    HypothesisIntegritySummary,
    IntegrityFinding,
    WorkspaceIntegrityReport,
)


class _CheckCollector:
    """Collects findings; every code without a problem gets one explicit PASS row."""

    def __init__(self) -> None:
        self._findings: list[IntegrityFinding] = []

    def issue(
        self,
        code: str,
        severity: str,
        subject: str,
        reason: str,
        evidence: tuple[str, ...] = (),
    ) -> None:
        self._findings = [item for item in self._findings if item.code != code]
        self._findings.append(
            IntegrityFinding(
                code=code,  # type: ignore[arg-type]
                severity=severity,  # type: ignore[arg-type]
                subject=subject,
                reason=reason,
                evidence=evidence,
            )
        )

    def ok(self, code: str, subject: str, reason: str) -> None:
        if code not in {item.code for item in self._findings}:
            self._findings.append(
                IntegrityFinding(
                    code=code,  # type: ignore[arg-type]
                    severity="PASS",
                    subject=subject,
                    reason=reason,
                )
            )

    def resolve(self) -> tuple[IntegrityFinding, ...]:
        return tuple(self._findings)


def _overall_status(violations: int, warnings: int) -> str:
    if violations:
        return "VIOLATION"
    return "WARNING" if warnings else "PASS"


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

    def _family_entries(self, family_id: str) -> tuple[ResearchLedgerEntry, ...]:
        return tuple(
            item
            for item in self.ledger.list()
            if item.kind == "HYPOTHESIS" and item.metadata.get("family_id") == family_id
        )

    def _manifest(self, run_id: str) -> RunManifest | None:
        try:
            return self.runs.get_manifest(run_id)
        except (RunNotFoundError, ArtifactIntegrityError):
            return None

    # -- individual checks -------------------------------------------------

    def _check_post_holdout_modification(
        self, record: ResearchHypothesis, collector: _CheckCollector
    ) -> None:
        subject = record.hypothesis_id
        family = self._family_entries(record.family_id)
        reveal_times = [
            item.created_at for item in family if item.metadata.get("event") == "REVEAL_HOLDOUT"
        ]
        if reveal_times:
            first_reveal = min(reveal_times)
            modified_after = [
                item
                for item in family
                if item.created_at > first_reveal
                and item.metadata.get("event") in {"CREATE_HYPOTHESIS", "CREATE_REVISION"}
            ]
            if modified_after:
                collector.issue(
                    "POST_HOLDOUT_MODIFICATION",
                    "VIOLATION",
                    subject,
                    "This experiment family was modified after Holdout had already been "
                    "revealed, so later revisions may be contaminated by Holdout knowledge.",
                    evidence=tuple(
                        f"{item.metadata.get('event')}:{item.artifact_id}"
                        f"@{item.created_at.isoformat()}"
                        for item in modified_after
                    ),
                )
                return

        entries = self._hypothesis_entries(record.hypothesis_id)
        if not entries:
            collector.issue(
                "POST_HOLDOUT_MODIFICATION",
                "VIOLATION",
                subject,
                "The Hypothesis has no research ledger entries, so its mutation history "
                "cannot be verified.",
                evidence=(),
            )
            return
        latest = max(entries, key=lambda item: item.created_at)
        drift: list[str] = []
        if latest.revision != record.revision:
            drift.append(
                f"revision recorded {latest.revision} but current record is {record.revision}"
            )
        if record.dataset_fingerprint not in latest.dataset_fingerprints:
            drift.append(
                f"dataset fingerprint recorded {latest.dataset_fingerprints} but current "
                f"record uses {record.dataset_fingerprint}"
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
        if drift:
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
            "No same-experiment modification after Holdout reveal and no mutation outside "
            "the research ledger was detected.",
        )

    def _check_future_data_leak(
        self, record: ResearchHypothesis, collector: _CheckCollector
    ) -> None:
        subject = record.hypothesis_id
        dataset = self.datasets.get(record.dataset_id)
        leaks: list[str] = []
        if record.created_with_known_stage == "HOLDOUT":
            leaks.append(
                "hypothesis was created after its Factor research Holdout was already revealed"
            )
        for research_id in record.factor_research_ids:
            factor = self.factors.get(research_id)
            if factor is None or dataset is None:
                continue
            if factor.periods.holdout.end is not None and factor.periods.holdout.end > (
                dataset.end_time
            ):
                leaks.append(
                    f"factor research '{research_id}' Holdout period ends after the dataset "
                    f"coverage end {dataset.end_time.isoformat()}"
                )
        for run_id in record.lineage.run_ids:
            manifest = self._manifest(run_id)
            if manifest is None or dataset is None:
                continue
            if manifest.period.end is not None and manifest.period.end > dataset.end_time:
                leaks.append(
                    f"run '{run_id}' period ends after the dataset coverage end "
                    f"{dataset.end_time.isoformat()}"
                )
            if manifest.period.start is not None and manifest.period.start < (dataset.start_time):
                leaks.append(
                    f"run '{run_id}' period starts before the dataset coverage start "
                    f"{dataset.start_time.isoformat()}"
                )
        if leaks:
            collector.issue(
                "FUTURE_DATA_LEAK",
                "WARNING",
                subject,
                "Research or Run boundaries reach beyond the recorded dataset coverage or "
                "the experiment was defined with Holdout already revealed.",
                evidence=tuple(leaks),
            )
            return
        collector.ok(
            "FUTURE_DATA_LEAK",
            subject,
            "All Factor research and Run periods stay inside the recorded dataset "
            "coverage, and the hypothesis was created before Holdout was revealed.",
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
                    if (
                        registration is not None
                        and registration.source_fingerprint != portfolio.strategy.source_fingerprint
                    ):
                        mismatches.append(
                            "registered strategy source fingerprint no longer matches the "
                            "strategy created from this research"
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
            "hypothesis semantics with matching source revisions.",
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
        for run_id in record.lineage.run_ids:
            if self._manifest(run_id) is None:
                missing.append(f"run '{run_id}' is missing from the Run store")
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
            "Strategy, Run, Trace) resolves to a stored record.",
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
            revision = factor.factor.source_fingerprint or factor.factor.version
            if not revision:
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
            overall_status=_overall_status(violations, warnings),  # type: ignore[arg-type]
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
            overall_status=_overall_status(violations, warnings),  # type: ignore[arg-type]
            total_violations=violations,
            total_warnings=warnings,
        )
