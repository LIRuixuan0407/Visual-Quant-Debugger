from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from shutil import rmtree

from test_phase23_discovery import _assets, _request

from app.discovery import CreateHypothesisRevision
from app.main import app
from app.research_integrity import (
    HypothesisIntegrityReport,
    ResearchIntegrityEngine,
    WorkspaceIntegrityReport,
)
from app.research_ledger import ResearchLedgerEntry
from app.research_snapshots import (
    CreateResearchSnapshot,
    ResearchSnapshotEngine,
    ResearchSnapshotRepository,
)
from app.runs import RunLedger, run_store
from app.sdk.registry import StrategyRegistry


def _integrity_engine(assets: tuple) -> ResearchIntegrityEngine:  # type: ignore[no-untyped-def]
    (
        discovery,
        _factor_engine,
        factors,
        hypotheses,
        ledger,
        datasets,
        strategies,
        _research_ids,
    ) = assets
    return ResearchIntegrityEngine(
        datasets,
        factors,
        discovery.relationships,
        discovery.walk_forward,
        hypotheses,
        discovery.portfolios,
        strategies,
        run_store.repository,
        ledger,
    )


def _complete_hypothesis(tmp_path: Path):  # type: ignore[no-untyped-def]
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
        record = factors.get(research_id)
        assert record is not None
        factors.save(factor_engine.reveal(record, "VALIDATION"))
    hypothesis = discovery.validate(hypothesis)
    for research_id in research_ids:
        record = factors.get(research_id)
        assert record is not None
        factors.save(factor_engine.reveal(record, "HOLDOUT"))
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
    hypothesis = discovery.attach_run(hypothesis, run.manifest.run_id, run.manifest.trace_id)
    engine = _integrity_engine(assets)
    return engine, discovery, hypotheses, hypothesis


def _finding(report: HypothesisIntegrityReport, code: str):  # type: ignore[no-untyped-def]
    return next(item for item in report.findings if item.code == code)


def test_research_integrity_api_is_registered() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/research-integrity" in paths
    assert "/api/research-integrity/{hypothesis_id}" in paths


def test_completed_research_chain_passes_all_guardrails(tmp_path: Path) -> None:
    engine, _, hypotheses, hypothesis = _complete_hypothesis(tmp_path)
    report = engine.audit(hypothesis.hypothesis_id)

    assert isinstance(report, HypothesisIntegrityReport)
    assert report.overall_status == "PASS"
    assert report.violation_count == 0
    assert report.warning_count == 0
    assert {item.code for item in report.findings} == {
        "POST_HOLDOUT_MODIFICATION",
        "FUTURE_DATA_LEAK",
        "DATASET_SILENT_CHANGE",
        "STRATEGY_SEMANTIC_MISMATCH",
        "MISSING_LINEAGE",
        "MISSING_REVISION",
    }
    assert all(item.severity == "PASS" for item in report.findings)
    assert all(item.reason for item in report.findings)
    assert "do not modify records" in report.disclosure

    overview = engine.overview()
    assert isinstance(overview, WorkspaceIntegrityReport)
    assert overview.overall_status == "PASS"
    assert overview.total_violations == 0
    assert len(overview.hypotheses) == len(hypotheses.list())


def test_new_revision_after_holdout_reveal_is_sanctioned(tmp_path: Path) -> None:
    engine, discovery, _, hypothesis = _complete_hypothesis(tmp_path)
    revision = discovery.create_revision(
        hypothesis,
        CreateHypothesisRevision(
            holding_horizon="20 trading days",
            revision_reason="Test a different horizon as a separate experiment.",
        ),
    )

    revision_report = engine.audit(revision.hypothesis_id)
    assert _finding(revision_report, "POST_HOLDOUT_MODIFICATION").severity == "PASS"
    original_report = engine.audit(hypothesis.hypothesis_id)
    assert _finding(original_report, "POST_HOLDOUT_MODIFICATION").severity == "PASS"


def test_guardrails_flag_modification_of_a_revealed_hypothesis(tmp_path: Path) -> None:
    engine, _, hypotheses, hypothesis = _complete_hypothesis(tmp_path)
    hypotheses.save(
        hypothesis.model_copy(
            update={
                "factor_research_ids": hypothesis.factor_research_ids[:1],
                "lineage": hypothesis.lineage.model_copy(
                    update={"factor_ids": hypothesis.lineage.factor_ids[:1]}
                ),
            }
        )
    )

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "POST_HOLDOUT_MODIFICATION")
    assert finding.severity == "VIOLATION"
    assert "without a matching research ledger event" in finding.reason
    assert any(item.startswith("factor ids recorded") for item in finding.evidence)


def test_guardrails_flag_non_sanctioned_event_after_holdout_reveal(tmp_path: Path) -> None:
    engine, _, _, hypothesis = _complete_hypothesis(tmp_path)
    engine.ledger.save(
        ResearchLedgerEntry.new(
            entry_id="ledger-post-reveal-event",
            kind="HYPOTHESIS",
            artifact_id=hypothesis.hypothesis_id,
            revision=hypothesis.revision,
            metadata={"event": "VALIDATE", "family_id": hypothesis.family_id},
        )
    )

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "POST_HOLDOUT_MODIFICATION")
    assert finding.severity == "VIOLATION"
    assert "after its Holdout had been revealed" in finding.reason
    assert any(
        item.startswith("VALIDATE event at")
        and "modified this hypothesis after Holdout reveal" in item
        for item in finding.evidence
    )


def test_guardrails_flag_mutation_outside_the_ledger(tmp_path: Path) -> None:
    engine, _, hypotheses, hypothesis = _complete_hypothesis(tmp_path)
    hypotheses.save(hypothesis.model_copy(update={"revision": 9}))

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "POST_HOLDOUT_MODIFICATION")
    assert finding.severity == "VIOLATION"
    assert any("revision" in evidence for evidence in finding.evidence)


def test_guardrails_flag_dataset_silent_change(tmp_path: Path) -> None:
    engine, _, hypotheses, hypothesis = _complete_hypothesis(tmp_path)
    hypotheses.save(
        hypothesis.model_copy(update={"dataset_fingerprint": "sha256:not-the-recorded-revision"})
    )

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "DATASET_SILENT_CHANGE")
    assert finding.severity == "VIOLATION"
    assert any("dataset fingerprint drifted" in item for item in finding.evidence)


def test_guardrails_flag_evaluation_timeline_outside_its_stage_window(tmp_path: Path) -> None:
    engine, _, _, hypothesis = _complete_hypothesis(tmp_path)
    research_id = hypothesis.factor_research_ids[0]
    factor = engine.factors.get(research_id)
    assert factor is not None
    evaluation = factor.evaluations[0]
    assert evaluation.horizons
    horizon = evaluation.horizons[0]
    assert horizon.timeline
    tampered_horizon = horizon.model_copy(
        update={
            "timeline": (
                horizon.timeline[0].model_copy(
                    update={"timestamp": factor.periods.validation.start + timedelta(days=1)}
                ),
            )
        }
    )
    tampered = evaluation.model_copy(
        update={"horizons": (tampered_horizon, *evaluation.horizons[1:])}
    )
    engine.factors.save(factor.model_copy(update={"evaluations": (tampered,)}))

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "FUTURE_DATA_LEAK")
    assert finding.severity == "VIOLATION"
    assert any(
        "RESEARCH evaluation timeline reaches outside its RESEARCH window" in item
        for item in finding.evidence
    )


def test_guardrails_flag_point_in_time_availability_violation(tmp_path: Path) -> None:
    engine, _, _, hypothesis = _complete_hypothesis(tmp_path)
    research_id = hypothesis.factor_research_ids[0]
    factor = engine.factors.get(research_id)
    assert factor is not None
    assert factor.sample_observations
    observation = factor.sample_observations[0]
    engine.factors.save(
        factor.model_copy(
            update={
                "sample_observations": (
                    observation.model_copy(
                        update={"available_at": observation.window_end - timedelta(days=1)}
                    ),
                )
            }
        )
    )

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "FUTURE_DATA_LEAK")
    assert finding.severity == "VIOLATION"
    assert any("before its input window closed" in item for item in finding.evidence)


def test_guardrails_flag_run_covering_holdout_before_reveal(tmp_path: Path) -> None:
    engine, _, hypotheses, hypothesis = _complete_hypothesis(tmp_path)
    hypotheses.save(hypothesis.model_copy(update={"status": "VALIDATED"}))

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "FUTURE_DATA_LEAK")
    assert finding.severity == "VIOLATION"
    assert any(
        "covers the Holdout window before Holdout was revealed" in item for item in finding.evidence
    )


def test_guardrails_flag_restatement_unsafe_factor_as_warning(tmp_path: Path) -> None:
    engine, _, _, hypothesis = _complete_hypothesis(tmp_path)
    research_id = hypothesis.factor_research_ids[0]
    factor = engine.factors.get(research_id)
    assert factor is not None
    engine.factors.save(
        factor.model_copy(
            update={
                "restatement_safe": False,
                "restatement_warning": "Quarterly filings may be restated.",
            }
        )
    )

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "FUTURE_DATA_LEAK")
    assert finding.severity == "WARNING"
    assert any("not restatement safe" in item for item in finding.evidence)
    assert report.overall_status == "WARNING"


def test_guardrails_flag_hypothesis_created_with_holdout_already_revealed(
    tmp_path: Path,
) -> None:
    assets = _assets(tmp_path)
    discovery, factor_engine, factors = assets[0], assets[1], assets[2]
    for stage in ("VALIDATION", "HOLDOUT"):
        for research_id in assets[7]:
            record = factors.get(research_id)
            assert record is not None
            factors.save(factor_engine.reveal(record, stage))
    hypothesis = discovery.create(_request(assets[7]))
    engine = ResearchIntegrityEngine(
        discovery.datasets,
        factors,
        discovery.relationships,
        discovery.walk_forward,
        discovery.hypotheses,
        discovery.portfolios,
        StrategyRegistry(tmp_path),
        run_store.repository,
        discovery.ledger,
    )

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "FUTURE_DATA_LEAK")
    assert finding.severity == "WARNING"
    assert any("Holdout was already revealed" in item for item in finding.evidence)
    assert report.overall_status == "WARNING"


def test_guardrails_flag_strategy_semantics_mismatch(tmp_path: Path) -> None:
    engine, _, _, hypothesis = _complete_hypothesis(tmp_path)
    portfolio = engine.portfolios.get(hypothesis.lineage.portfolio_research_id or "")
    assert portfolio is not None
    engine.portfolios.save(portfolio.model_copy(update={"rebalance": "WEEKLY", "strategy": None}))

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "STRATEGY_SEMANTIC_MISMATCH")
    assert finding.severity == "VIOLATION"
    assert any("rebalance" in evidence for evidence in finding.evidence)
    assert any("no Native Strategy" in evidence for evidence in finding.evidence)


def test_guardrails_flag_portfolio_config_changed_after_strategy_generation(
    tmp_path: Path,
) -> None:
    engine, _, _, hypothesis = _complete_hypothesis(tmp_path)
    portfolio = engine.portfolios.get(hypothesis.lineage.portfolio_research_id or "")
    assert portfolio is not None
    engine.portfolios.save(
        portfolio.model_copy(
            update={"construction": portfolio.construction.model_copy(update={"top_percent": 40.0})}
        )
    )

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "STRATEGY_SEMANTIC_MISMATCH")
    assert finding.severity == "VIOLATION"
    assert any(
        "portfolio top percent is 40.0 while the hypothesis candidate defines 20.0" in item
        for item in finding.evidence
    )
    assert any(
        "no longer matches the current Portfolio semantics" in item for item in finding.evidence
    )


def test_guardrails_flag_run_cost_parameters_differing_from_research(
    tmp_path: Path,
) -> None:
    engine, discovery, _, hypothesis = _complete_hypothesis(tmp_path)
    assert hypothesis.lineage.strategy_id is not None
    expensive = RunLedger().create(
        strategy_id=hypothesis.lineage.strategy_id,
        dataset_id=hypothesis.dataset_id,
        parameters={"fee_bps": 25.0},
        research_cutoff=None,
        strategy_registry_override=engine.strategies,
        dataset_registry_override=engine.datasets,
    )
    assert expensive.manifest.trace_id is not None
    hypothesis = discovery.attach_run(
        hypothesis, expensive.manifest.run_id, expensive.manifest.trace_id
    )

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "STRATEGY_SEMANTIC_MISMATCH")
    assert finding.severity == "VIOLATION"
    assert any(
        "executed with fee_bps 25.0 while the research portfolio defines 5.0" in item
        for item in finding.evidence
    )


def test_guardrails_flag_run_trace_pairing_mismatch(tmp_path: Path) -> None:
    engine, _, hypotheses, hypothesis = _complete_hypothesis(tmp_path)
    hypotheses.save(
        hypothesis.model_copy(
            update={
                "lineage": hypothesis.lineage.model_copy(update={"trace_ids": ("trace-not-owned",)})
            }
        )
    )

    report = engine.audit(hypothesis.hypothesis_id)
    finding = _finding(report, "MISSING_LINEAGE")
    assert finding.severity == "VIOLATION"
    assert any("does not own Trace 'trace-not-owned'" in item for item in finding.evidence)


def test_guardrails_flag_missing_lineage_for_lifecycle_status(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    discovery = assets[0]
    hypothesis = discovery.build_candidate(discovery.create(_request(assets[7])))
    broken = hypothesis.model_copy(
        update={
            "lineage": hypothesis.lineage.model_copy(
                update={"portfolio_research_id": "portfolio-does-not-exist"}
            )
        }
    )
    discovery.hypotheses.save(broken)
    engine = ResearchIntegrityEngine(
        discovery.datasets,
        discovery.factors,
        discovery.relationships,
        discovery.walk_forward,
        discovery.hypotheses,
        discovery.portfolios,
        StrategyRegistry(tmp_path),
        run_store.repository,
        discovery.ledger,
    )

    report = engine.audit(broken.hypothesis_id)
    finding = _finding(report, "MISSING_LINEAGE")
    assert finding.severity == "VIOLATION"
    assert any("portfolio research" in evidence for evidence in finding.evidence)


def test_guardrails_flag_missing_ledger_entries(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    discovery = assets[0]
    hypothesis = discovery.create(_request(assets[7]))
    rmtree(discovery.ledger.root)
    engine = ResearchIntegrityEngine(
        discovery.datasets,
        discovery.factors,
        discovery.relationships,
        discovery.walk_forward,
        discovery.hypotheses,
        discovery.portfolios,
        StrategyRegistry(tmp_path),
        run_store.repository,
        discovery.ledger,
    )

    report = engine.audit(hypothesis.hypothesis_id)
    post_holdout = _finding(report, "POST_HOLDOUT_MODIFICATION")
    assert post_holdout.severity == "VIOLATION"
    assert "no research ledger entries" in post_holdout.reason
    revision = _finding(report, "MISSING_REVISION")
    assert revision.severity == "WARNING"
    assert any("no research ledger revision entries" in item for item in revision.evidence)


def test_frozen_snapshot_still_audits_clean_after_source_mutation(tmp_path: Path) -> None:
    engine, _, hypotheses, hypothesis = _complete_hypothesis(tmp_path)
    snapshots = ResearchSnapshotRepository(tmp_path)
    snapshot_engine = ResearchSnapshotEngine(
        engine.datasets,
        engine.factors,
        engine.relationships,
        engine.walk_forward,
        engine.hypotheses,
        engine.portfolios,
        engine.strategies,
        engine.runs,
        snapshots,
        engine.ledger,
    )
    frozen = snapshot_engine.create(
        CreateResearchSnapshot(name="Frozen integrity", hypothesis_id=hypothesis.hypothesis_id)
    )
    hypotheses.save(hypothesis.model_copy(update={"title": "Changed after freezing"}))

    report = engine.audit(hypothesis.hypothesis_id)
    stored = snapshots.get(frozen.snapshot_id)
    assert stored is not None
    assert "Changed after freezing" not in stored.hypothesis.payload_json
    # The title-only mutation is not ledger-visible, so guardrails stay green while
    # the frozen snapshot still proves the original content.
    assert report.overall_status == "PASS"
