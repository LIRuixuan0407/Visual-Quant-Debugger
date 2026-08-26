from __future__ import annotations

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


def test_guardrails_flag_post_holdout_family_modification(tmp_path: Path) -> None:
    engine, discovery, _, hypothesis = _complete_hypothesis(tmp_path)
    revision = discovery.create_revision(
        hypothesis,
        CreateHypothesisRevision(
            holding_horizon="20 trading days",
            revision_reason="Test a different horizon after Holdout was revealed.",
        ),
    )

    report = engine.audit(revision.hypothesis_id)
    finding = next(item for item in report.findings if item.code == "POST_HOLDOUT_MODIFICATION")
    assert finding.severity == "VIOLATION"
    assert "Holdout" in finding.reason
    assert any(item.startswith("CREATE_REVISION") for item in finding.evidence)
    assert report.overall_status == "VIOLATION"


def test_guardrails_flag_mutation_outside_the_ledger(tmp_path: Path) -> None:
    engine, _, hypotheses, hypothesis = _complete_hypothesis(tmp_path)
    hypotheses.save(hypothesis.model_copy(update={"revision": 9}))

    report = engine.audit(hypothesis.hypothesis_id)
    finding = next(item for item in report.findings if item.code == "POST_HOLDOUT_MODIFICATION")
    assert finding.severity == "VIOLATION"
    assert any("revision" in evidence for evidence in finding.evidence)


def test_guardrails_flag_dataset_silent_change(tmp_path: Path) -> None:
    engine, _, hypotheses, hypothesis = _complete_hypothesis(tmp_path)
    hypotheses.save(
        hypothesis.model_copy(update={"dataset_fingerprint": "sha256:not-the-recorded-revision"})
    )

    report = engine.audit(hypothesis.hypothesis_id)
    finding = next(item for item in report.findings if item.code == "DATASET_SILENT_CHANGE")
    assert finding.severity == "VIOLATION"
    assert any("dataset fingerprint drifted" in item for item in finding.evidence)


def test_guardrails_flag_strategy_semantic_mismatch(tmp_path: Path) -> None:
    engine, _, _, hypothesis = _complete_hypothesis(tmp_path)
    portfolio = engine.portfolios.get(hypothesis.lineage.portfolio_research_id or "")
    assert portfolio is not None
    engine.portfolios.save(portfolio.model_copy(update={"rebalance": "WEEKLY", "strategy": None}))

    report = engine.audit(hypothesis.hypothesis_id)
    finding = next(item for item in report.findings if item.code == "STRATEGY_SEMANTIC_MISMATCH")
    assert finding.severity == "VIOLATION"
    assert any("rebalance" in evidence for evidence in finding.evidence)
    assert any("no Native Strategy" in evidence for evidence in finding.evidence)


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
    finding = next(item for item in report.findings if item.code == "MISSING_LINEAGE")
    assert finding.severity == "VIOLATION"
    assert any("portfolio research" in evidence for evidence in finding.evidence)


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
    finding = next(item for item in report.findings if item.code == "FUTURE_DATA_LEAK")
    assert finding.severity == "WARNING"
    assert any("Holdout was already revealed" in evidence for evidence in finding.evidence)
    assert report.overall_status == "WARNING"


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
    post_holdout = next(
        item for item in report.findings if item.code == "POST_HOLDOUT_MODIFICATION"
    )
    assert post_holdout.severity == "VIOLATION"
    assert "no research ledger entries" in post_holdout.reason
    revision = next(item for item in report.findings if item.code == "MISSING_REVISION")
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
