from __future__ import annotations

from pathlib import Path

from test_phase23_discovery import _assets, _request

from app.main import app
from app.research_integrity import ResearchIntegrityEngine
from app.research_snapshots import ResearchSnapshotRepository
from app.research_workspace import ResearchWorkspaceEngine
from app.runs import RunLedger, run_store


def _engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    assets = _assets(tmp_path)
    discovery, _, factors, hypotheses, ledger, datasets, strategies, _ = assets
    integrity = ResearchIntegrityEngine(
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
    workspace = ResearchWorkspaceEngine(
        datasets,
        factors,
        hypotheses,
        discovery.portfolios,
        strategies,
        run_store.repository,
        ResearchSnapshotRepository(tmp_path),
        integrity,
        ledger,
    )
    return workspace, assets


def test_research_workspace_api_is_registered() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/research-workspaces" in paths
    assert "/api/research-workspaces/{hypothesis_id}" in paths


def test_workspace_projects_existing_research_without_new_persistence(tmp_path: Path) -> None:
    workspace, assets = _engine(tmp_path)
    discovery, _, _, _, _, _, _, research_ids = assets
    hypothesis = discovery.create(_request(research_ids))

    detail = workspace.get(hypothesis.hypothesis_id)

    assert detail.idea_id == hypothesis.hypothesis_id
    assert detail.dataset_id == hypothesis.dataset_id
    assert len(detail.factors) == len(research_ids)
    assert detail.portfolio is None
    assert detail.next_action.action == "BUILD_CANDIDATE"
    assert [item.key for item in detail.stages] == [
        "DATA",
        "FACTOR",
        "PORTFOLIO",
        "VALIDATION",
        "HYPOTHESIS",
        "STRATEGY",
        "RUN",
    ]
    assert [item.status for item in detail.stages[:3]] == [
        "COMPLETE",
        "COMPLETE",
        "CURRENT",
    ]
    assert "does not duplicate quantitative engines" in detail.disclosure

    summaries = workspace.list()
    assert summaries[0].idea_id == hypothesis.hypothesis_id
    assert summaries[0].completed_stage_count == 2


def test_workspace_continues_one_idea_through_existing_execution_chain(tmp_path: Path) -> None:
    workspace, assets = _engine(tmp_path)
    (
        discovery,
        factor_engine,
        factors,
        _,
        _,
        datasets,
        strategies,
        research_ids,
    ) = assets
    hypothesis = discovery.build_candidate(discovery.create(_request(research_ids)))
    assert workspace.get(hypothesis.hypothesis_id).next_action.action == "RUN_VALIDATION"

    for research_id in research_ids:
        factor = factors.get(research_id)
        assert factor is not None
        factors.save(factor_engine.reveal(factor, "VALIDATION"))
    hypothesis = discovery.validate(hypothesis)
    validation = workspace.get(hypothesis.hypothesis_id)
    assert validation.next_action.action == "REVEAL_HOLDOUT"
    assert validation.next_action.requires_explicit_confirmation

    for research_id in research_ids:
        factor = factors.get(research_id)
        assert factor is not None
        factors.save(factor_engine.reveal(factor, "HOLDOUT"))
    hypothesis = discovery.reveal_holdout(hypothesis)
    assert workspace.get(hypothesis.hypothesis_id).next_action.action == "CREATE_STRATEGY"

    hypothesis = discovery.create_strategy(hypothesis)
    assert hypothesis.lineage.strategy_id is not None
    before_run = workspace.get(hypothesis.hypothesis_id)
    assert before_run.next_action.action == "RUN_BACKTEST"

    result = RunLedger().create(
        strategy_id=hypothesis.lineage.strategy_id,
        dataset_id=hypothesis.dataset_id,
        parameters={},
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    assert result.manifest.trace_id is not None
    hypothesis = discovery.attach_run(
        hypothesis,
        result.manifest.run_id,
        result.manifest.trace_id,
    )

    completed = workspace.get(hypothesis.hypothesis_id)
    assert completed.next_action.action == "OPEN_RUN"
    assert all(stage.status == "COMPLETE" for stage in completed.stages)
    assert completed.runs[0].run_id == result.manifest.run_id
    assert completed.runs[0].trace_id == result.manifest.trace_id
    assert completed.runs[0].total_return == result.manifest.metrics.total_return  # type: ignore[union-attr]
    assert completed.integrity_status == "PASS"
