from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

from test_phase23_discovery import _assets, _request

from app.discovery import ResearchHypothesis
from app.factors.registry import FactorRegistry
from app.global_search import (
    GlobalSearchService,
    SearchDocument,
    normalize_search_text,
    rank_search_documents,
)
from app.main import app
from app.research_snapshots import ResearchSnapshotRepository
from app.runs import AnnotationUpdate, RunLedger, run_store


def _document(
    entity_type: str,
    entity_id: str,
    title: str,
    *,
    aliases: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    created_at: datetime | None = None,
    metadata: dict[str, str | int | float | bool | None] | None = None,
) -> SearchDocument:
    return SearchDocument.model_validate(
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "title": title,
            "aliases": aliases,
            "tags": tags,
            "created_at": created_at,
            "route": f"/{entity_type.lower()}/{entity_id}",
            "metadata": metadata or {},
        }
    )


def _service(tmp_path: Path, hypothesis: ResearchHypothesis | None = None) -> GlobalSearchService:
    assets = _assets(tmp_path)
    discovery, _, factors, hypotheses, _, datasets, strategies, research_ids = assets
    if hypothesis is None:
        hypotheses.save(
            discovery.create(_request(research_ids).model_copy(update={"title": "苹果动量研究"}))
        )
    else:
        hypotheses.save(hypothesis)
    return GlobalSearchService(
        datasets,
        FactorRegistry(tmp_path),
        factors,
        discovery.relationships,
        discovery.walk_forward,
        discovery.portfolios,
        hypotheses,
        strategies,
        run_store.repository,
        ResearchSnapshotRepository(tmp_path),
    )


def test_api_is_registered_and_empty_query_does_not_build_documents(
    tmp_path: Path, monkeypatch
) -> None:
    assert "/api/search" in app.openapi()["paths"]
    service = _service(tmp_path)

    def fail_document_build() -> tuple[SearchDocument, ...]:
        raise AssertionError("empty search must not build documents")

    monkeypatch.setattr(service, "documents", fail_document_build)
    response = service.search("   ")
    assert response.results == ()
    assert response.normalized_query == ""


def test_exact_prefix_tag_alias_and_unicode_normalization_are_deterministic() -> None:
    documents = (
        _document("RUN", "run-alpha_01", "Momentum Baseline", tags=("cost-test",)),
        _document("FACTOR", "momentum", "Momentum", aliases=("动量因子", "v1")),
        _document("HYPOTHESIS", "hypothesis-z", "苹果动量研究"),
    )

    assert rank_search_documents(documents, "RUN-ALPHA 01")[0].score == 1000
    assert rank_search_documents(documents, "momentum")[0].score == 1000
    assert rank_search_documents(documents, "Momentum Baseline")[0].score == 900
    assert rank_search_documents(documents, "run alpha")[0].score == 850
    assert rank_search_documents(documents, "momentum base")[0].score == 800
    assert rank_search_documents(documents, "cost-test")[0].score == 600
    assert rank_search_documents(documents, "动量因子")[0].score == 950
    assert rank_search_documents(documents, "苹果动量")[0].entity_id == "hypothesis-z"
    assert normalize_search_text("  RUN_alpha-01  ") == "run alpha 01"


def test_type_filter_tie_break_and_metrics_never_affect_ranking() -> None:
    created = datetime(2026, 8, 27, tzinfo=UTC)
    documents = (
        _document(
            "RUN",
            "run-b",
            "Same experiment",
            created_at=created,
            metadata={"sharpe": 99.0, "return": 42.0, "outcome": "SUPPORTED"},
        ),
        _document(
            "RUN",
            "run-a",
            "Same experiment",
            created_at=created,
            metadata={"sharpe": -99.0, "return": -42.0, "outcome": "NOT_SUPPORTED"},
        ),
        _document(
            "HYPOTHESIS",
            "hypothesis-a",
            "Same experiment",
            created_at=created + timedelta(seconds=1),
        ),
    )

    ranked = rank_search_documents(documents, "same experiment")
    assert [item.entity_id for item in ranked] == ["hypothesis-a", "run-a", "run-b"]
    run_only = rank_search_documents(documents, "same", entity_types=("RUN",))
    assert [item.entity_id for item in run_only] == ["run-a", "run-b"]


def test_query_time_documents_cover_research_and_never_read_trace_or_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    created = RunLedger().create(
        strategy_id="pairs-trading",
        dataset_id="pairs-sample-v1",
        parameters={"lookback": 20, "entry_z": 1.5, "exit_z": 0.4},
        research_cutoff=None,
        strategy_registry_override=service.strategies,
        dataset_registry_override=service.datasets,
    )
    run_store.repository.update_annotations(
        created.manifest.run_id,
        AnnotationUpdate(
            display_name="AAPL momentum baseline",
            note="DO-NOT-INDEX-secret-credential",
            tags=("custom-run-tag",),
        ),
    )

    def fail_trace_read(*_args, **_kwargs):
        raise AssertionError("Global Search must not load Trace event contents")

    monkeypatch.setattr(run_store.repository, "load_trace", fail_trace_read)
    monkeypatch.setattr(
        service.factors,
        "list_definitions",
        lambda: (_ for _ in ()).throw(AssertionError("must not load Factor source files")),
    )
    documents = service.documents()
    types = {item.entity_type for item in documents}
    assert {
        "DATASET",
        "FACTOR",
        "FACTOR_RESEARCH",
        "FACTOR_RELATIONSHIP",
        "WALK_FORWARD",
        "HYPOTHESIS",
        "STRATEGY",
        "RUN",
        "TRACE",
    } <= types

    assert service.search("AAPL").results
    assert service.search("momentum").results
    assert service.search("苹果动量").results[0].entity_type == "HYPOTHESIS"
    assert service.search(created.manifest.run_id[8:18]).results[0].entity_type == "RUN"
    assert service.search("custom-run-tag").results[0].entity_id == created.manifest.run_id
    assert created.manifest.trace_id is not None
    assert service.search(created.manifest.trace_id[10:20]).results[0].entity_type == "TRACE"
    assert service.search("DO-NOT-INDEX-secret-credential").results == ()


def test_ranker_handles_five_thousand_documents_with_stable_limit() -> None:
    documents = tuple(
        _document("FACTOR", f"factor-{index:04d}", f"Synthetic momentum {index:04d}")
        for index in range(5_000)
    )
    started = perf_counter()
    first = rank_search_documents(documents, "synthetic momentum", limit=50)
    elapsed = perf_counter() - started
    second = rank_search_documents(documents, "synthetic momentum", limit=50)
    assert len(first) == 50
    assert first == second
    assert elapsed < 0.25


def test_document_cache_reuses_results_and_invalidates_from_source_signature(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    first = service.documents()
    assert service.documents() is first
    hypothesis = service.hypotheses.list()[0]
    service.hypotheses.save(hypothesis.model_copy(update={"title": "缓存失效后的研究标题"}))

    refreshed = service.documents()
    assert refreshed is not first
    assert any(item.title == "缓存失效后的研究标题" for item in refreshed)
    service.invalidate()
    assert service.documents() is not refreshed
