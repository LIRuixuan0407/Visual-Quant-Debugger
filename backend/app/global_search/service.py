from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from threading import Lock
from urllib.parse import quote

from app.corporate_actions import CorporateActionRepository
from app.datasets import DatasetRegistry
from app.discovery import HypothesisRepository
from app.factor_relationships import FactorRelationshipRepository
from app.factors import FactorResearchRepository
from app.factors.catalog import FACTOR_CATALOG
from app.factors.registry import FactorRegistry
from app.portfolio_lab import PortfolioResearchRepository
from app.research_snapshots import ResearchSnapshotRepository
from app.runs import RunRepository
from app.runs.models import RunListItem
from app.sdk.registry import StrategyRegistry
from app.strategies.definition import PAIRS_TRADING_DEFINITION
from app.universes import UniverseRepository
from app.walk_forward import WalkForwardRepository

from .models import (
    SEARCH_ENTITY_TYPES,
    GlobalSearchResponse,
    SearchDocument,
    SearchEntityType,
    SearchResult,
)

_BOUNDARIES = re.compile(r"[-_\s]+", re.UNICODE)
_TYPE_ORDER = {entity_type: index for index, entity_type in enumerate(SEARCH_ENTITY_TYPES)}


def normalize_search_text(value: str) -> str:
    """Normalize only for matching; original values remain untouched for display."""

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return _BOUNDARIES.sub(" ", normalized).strip()


def _match(document: SearchDocument, query: str) -> tuple[int, tuple[str, ...]] | None:
    entity_id = normalize_search_text(document.entity_id)
    title = normalize_search_text(document.title)
    subtitle = normalize_search_text(document.subtitle)
    aliases = tuple(normalize_search_text(value) for value in document.aliases if value.strip())
    tags = tuple(normalize_search_text(value) for value in document.tags if value.strip())
    tokens = tuple(part for part in query.split(" ") if part)

    if query == entity_id:
        return 1000, ("entity_id",)
    if query in aliases:
        return 950, ("alias",)
    if query == title:
        return 900, ("title",)
    if entity_id.startswith(query):
        return 850, ("entity_id",)
    if title.startswith(query):
        return 800, ("title",)
    if tokens and all(token in f"{title} {entity_id}" for token in tokens):
        return 700, ("title", "entity_id")
    if query in tags:
        return 600, ("tag",)
    if any(query in alias for alias in aliases):
        return 500, ("alias",)

    searchable = (
        ("entity_id", entity_id),
        ("title", title),
        ("subtitle", subtitle),
        *(("alias", value) for value in aliases),
        *(("tag", value) for value in tags),
    )
    fields = tuple(dict.fromkeys(field for field, value in searchable if query in value))
    if fields:
        return 400, fields
    return None


def rank_search_documents(
    documents: Iterable[SearchDocument],
    query: str,
    *,
    entity_types: tuple[SearchEntityType, ...] = (),
    limit: int = 20,
) -> tuple[SearchResult, ...]:
    normalized_query = normalize_search_text(query)
    if not normalized_query:
        return ()
    allowed = set(entity_types)
    matches: list[tuple[SearchDocument, int, tuple[str, ...]]] = []
    for document in documents:
        if allowed and document.entity_type not in allowed:
            continue
        matched = _match(document, normalized_query)
        if matched is not None:
            matches.append((document, matched[0], matched[1]))

    def sort_key(
        item: tuple[SearchDocument, int, tuple[str, ...]],
    ) -> tuple[int, float, int, str]:
        document, score, _ = item
        created = document.created_at.timestamp() if document.created_at is not None else 0.0
        return (-score, -created, _TYPE_ORDER[document.entity_type], document.entity_id)

    matches.sort(key=sort_key)
    return tuple(
        SearchResult(
            entity_type=document.entity_type,
            entity_id=document.entity_id,
            title=document.title,
            subtitle=document.subtitle,
            score=score,
            route=document.route,
            highlights=highlights,
            metadata=document.metadata,
        )
        for document, score, highlights in matches[:limit]
    )


def _route(path: str, parameter: str, value: str) -> str:
    return f"{path}?{parameter}={quote(value, safe='')}"


class GlobalSearchService:
    def __init__(
        self,
        datasets: DatasetRegistry,
        factors: FactorRegistry,
        factor_research: FactorResearchRepository,
        relationships: FactorRelationshipRepository,
        walk_forward: WalkForwardRepository,
        portfolios: PortfolioResearchRepository,
        hypotheses: HypothesisRepository,
        strategies: StrategyRegistry,
        runs: RunRepository,
        snapshots: ResearchSnapshotRepository,
        universes: UniverseRepository | None = None,
        corporate_actions: CorporateActionRepository | None = None,
    ) -> None:
        self.datasets = datasets
        self.factors = factors
        self.factor_research = factor_research
        self.relationships = relationships
        self.walk_forward = walk_forward
        self.portfolios = portfolios
        self.hypotheses = hypotheses
        self.strategies = strategies
        self.runs = runs
        self.snapshots = snapshots
        self.universes = universes or UniverseRepository(datasets.workspace_root)
        self.corporate_actions = corporate_actions or CorporateActionRepository(
            datasets.workspace_root
        )
        self._cache_lock = Lock()
        self._cached_signature: tuple[tuple[str, int, int], ...] | None = None
        self._cached_documents: tuple[SearchDocument, ...] | None = None

    def invalidate(self) -> None:
        """Explicitly discard the derived read-model cache without changing source records."""

        with self._cache_lock:
            self._cached_signature = None
            self._cached_documents = None

    @staticmethod
    def _file_signature(path: Path) -> tuple[str, int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return str(path), stat.st_mtime_ns, stat.st_size

    def _source_signature(self) -> tuple[tuple[str, int, int], ...]:
        roots = (
            self.datasets.datasets_root,
            self.factor_research.root,
            self.relationships.root,
            self.walk_forward.root,
            self.portfolios.root,
            self.hypotheses.root,
            self.snapshots.root,
            self.universes.root,
            self.corporate_actions.root,
        )
        files = [
            self.factors.registry_path,
            self.strategies.registry_path,
            self.runs.database_path,
            self.runs.database_path.with_name(f"{self.runs.database_path.name}-wal"),
        ]
        for root in roots:
            if root.exists():
                files.extend(root.rglob("*.json"))
        signatures = (self._file_signature(path) for path in files)
        return tuple(sorted(item for item in signatures if item is not None))

    def _run_items(self) -> tuple[RunListItem, ...]:
        items: list[RunListItem] = []
        offset = 0
        while True:
            page = self.runs.list_runs(limit=500, offset=offset)
            items.extend(page.items)
            offset += len(page.items)
            if offset >= page.total or not page.items:
                return tuple(items)

    def documents(self) -> tuple[SearchDocument, ...]:
        signature = self._source_signature()
        if signature == self._cached_signature and self._cached_documents is not None:
            return self._cached_documents
        with self._cache_lock:
            signature = self._source_signature()
            if signature == self._cached_signature and self._cached_documents is not None:
                return self._cached_documents
            documents = self._build_documents()
            self._cached_signature = signature
            self._cached_documents = documents
            return documents

    def _build_documents(self) -> tuple[SearchDocument, ...]:
        documents: list[SearchDocument] = []
        datasets = {dataset.dataset_id: dataset for dataset in self.datasets.list()}
        for dataset in datasets.values():
            provider = dataset.provenance.provider if dataset.provenance is not None else None
            documents.append(
                SearchDocument(
                    entity_type="DATASET",
                    entity_id=dataset.dataset_id,
                    title=dataset.name,
                    subtitle=f"{dataset.source_type} · {', '.join(dataset.symbols)}",
                    aliases=(
                        *dataset.symbols,
                        dataset.source_type,
                        provider or "",
                        dataset.content_fingerprint,
                    ),
                    created_at=dataset.created_at,
                    route=_route("/data", "dataset_id", dataset.dataset_id),
                    metadata={
                        "source_type": dataset.source_type,
                        "provider": provider,
                        "fingerprint": dataset.content_fingerprint,
                    },
                )
            )

        for universe in self.universes.list():
            documents.append(
                SearchDocument(
                    entity_type="UNIVERSE",
                    entity_id=universe.universe_id,
                    title=universe.name,
                    subtitle=(
                        f"{universe.mode} · {len(universe.snapshots)} snapshots · {universe.source}"
                    ),
                    aliases=(
                        universe.source,
                        universe.mode,
                        universe.dataset_id or "",
                        *(symbol for item in universe.snapshots for symbol in item.symbols),
                    ),
                    created_at=universe.created_at,
                    route=_route("/data", "universe_id", universe.universe_id),
                    metadata={
                        "mode": universe.mode,
                        "snapshot_count": len(universe.snapshots),
                        "survivorship_bias_free": universe.survivorship_bias_free,
                    },
                )
            )

        for actions in self.corporate_actions.list():
            documents.append(
                SearchDocument(
                    entity_type="CORPORATE_ACTION_DATASET",
                    entity_id=actions.corporate_action_dataset_id,
                    title=actions.name,
                    subtitle=(
                        f"{actions.provider} · {len(actions.actions)} actions · "
                        f"{', '.join(actions.symbols)}"
                    ),
                    aliases=(
                        actions.provider,
                        actions.content_fingerprint,
                        *actions.symbols,
                        *(item.action_id for item in actions.actions),
                    ),
                    created_at=actions.retrieved_at,
                    route=_route(
                        "/data",
                        "corporate_action_dataset_id",
                        actions.corporate_action_dataset_id,
                    ),
                    metadata={
                        "provider": actions.provider,
                        "action_count": len(actions.actions),
                        "point_in_time_safe": actions.point_in_time_safe,
                    },
                )
            )

        research_records = []
        for summary in self.factor_research.list():
            record = self.factor_research.get(summary.research_id)
            if record is None:
                continue
            research_records.append(record)
            documents.append(
                SearchDocument(
                    entity_type="FACTOR_RESEARCH",
                    entity_id=record.research_id,
                    title=record.name,
                    subtitle=(
                        f"{record.factor.name} · {datasets[record.dataset_id].name}"
                        if record.dataset_id in datasets
                        else record.dataset_id
                    ),
                    aliases=(
                        record.factor.factor_id,
                        record.dataset_id,
                        record.factor.name,
                        record.revealed_stage,
                    ),
                    created_at=record.created_at,
                    route=_route("/factor-lab", "research_id", record.research_id),
                    metadata={
                        "factor_id": record.factor.factor_id,
                        "dataset_id": record.dataset_id,
                        "revealed_stage": record.revealed_stage,
                    },
                )
            )

        factor_documents: dict[tuple[str, str], SearchDocument] = {}
        for definition in FACTOR_CATALOG:
            revision = definition.source_fingerprint or definition.version
            factor_documents[(definition.factor_id, revision)] = SearchDocument(
                entity_type="FACTOR",
                entity_id=definition.factor_id,
                title=definition.name,
                subtitle=f"{definition.category} · {definition.data_source} · {definition.origin}",
                aliases=(
                    definition.version,
                    revision,
                    definition.category,
                    definition.data_source,
                    definition.origin,
                ),
                created_at=None,
                route=_route("/factor-lab", "factor_id", definition.factor_id),
                metadata={"revision": revision, "origin": definition.origin},
            )
        for factor_registration in self.factors.list_registrations():
            factor_documents[
                (factor_registration.factor_id, factor_registration.source_fingerprint)
            ] = SearchDocument(
                entity_type="FACTOR",
                entity_id=factor_registration.factor_id,
                title=factor_registration.class_name,
                subtitle="CUSTOM · registered Factor",
                aliases=(
                    factor_registration.class_name,
                    factor_registration.source_fingerprint,
                    "CUSTOM",
                ),
                created_at=factor_registration.registered_at,
                route=_route("/factor-lab", "factor_id", factor_registration.factor_id),
                metadata={
                    "revision": factor_registration.source_fingerprint,
                    "origin": "CUSTOM",
                },
            )
        for record in research_records:
            definition = record.factor
            revision = definition.source_fingerprint or definition.version
            factor_documents[(definition.factor_id, revision)] = SearchDocument(
                entity_type="FACTOR",
                entity_id=definition.factor_id,
                title=definition.name,
                subtitle=f"{definition.category} · {definition.data_source} · {definition.origin}",
                aliases=(
                    definition.version,
                    revision,
                    definition.category,
                    definition.data_source,
                    definition.origin,
                    record.research_id,
                ),
                created_at=record.created_at,
                route=_route("/factor-lab", "research_id", record.research_id),
                metadata={"revision": revision, "origin": definition.origin},
            )
        documents.extend(factor_documents.values())

        for relationship in self.relationships.list():
            documents.append(
                SearchDocument(
                    entity_type="FACTOR_RELATIONSHIP",
                    entity_id=relationship.relationship_id,
                    title=relationship.name,
                    subtitle=f"{', '.join(relationship.factor_names)} · {relationship.stage}",
                    aliases=(
                        relationship.dataset_id,
                        relationship.stage,
                        *relationship.factor_research_ids,
                        *relationship.factor_ids,
                        *relationship.factor_names,
                        *relationship.factor_revisions,
                    ),
                    created_at=relationship.created_at,
                    route=_route(
                        "/factor-relationships",
                        "relationship_id",
                        relationship.relationship_id,
                    ),
                    metadata={
                        "dataset_id": relationship.dataset_id,
                        "stage": relationship.stage,
                    },
                )
            )

        for walk in self.walk_forward.list():
            documents.append(
                SearchDocument(
                    entity_type="WALK_FORWARD",
                    entity_id=walk.walk_forward_id,
                    title=walk.name,
                    subtitle=f"{walk.factor_id} · {walk.dataset_id}",
                    aliases=(
                        walk.factor_research_id,
                        walk.factor_id,
                        walk.factor_revision,
                        walk.strategy_id or "",
                        walk.strategy_revision or "",
                        walk.dataset_id,
                    ),
                    created_at=walk.created_at,
                    route=_route("/walk-forward", "walk_forward_id", walk.walk_forward_id),
                    metadata={
                        "factor_id": walk.factor_id,
                        "dataset_id": walk.dataset_id,
                        "strategy_id": walk.strategy_id,
                    },
                )
            )

        for portfolio_summary in self.portfolios.list():
            portfolio = self.portfolios.get(portfolio_summary.portfolio_research_id)
            if portfolio is None:
                continue
            documents.append(
                SearchDocument(
                    entity_type="PORTFOLIO_RESEARCH",
                    entity_id=portfolio.portfolio_research_id,
                    title=portfolio.name,
                    subtitle=(f"{', '.join(portfolio.factor_names)} · {portfolio.revealed_stage}"),
                    aliases=(
                        portfolio.dataset_id,
                        portfolio.revealed_stage,
                        portfolio.combination,
                        *portfolio.factor_ids,
                        *portfolio.factor_names,
                        *(ref.research_id for ref in portfolio.factor_refs),
                    ),
                    created_at=portfolio.created_at,
                    route=_route(
                        "/portfolio-lab",
                        "portfolio_research_id",
                        portfolio.portfolio_research_id,
                    ),
                    metadata={
                        "dataset_id": portfolio.dataset_id,
                        "revealed_stage": portfolio.revealed_stage,
                        "factor_count": len(portfolio.factor_ids),
                    },
                )
            )

        for hypothesis in self.hypotheses.list():
            documents.append(
                SearchDocument(
                    entity_type="HYPOTHESIS",
                    entity_id=hypothesis.hypothesis_id,
                    title=hypothesis.title,
                    subtitle=(
                        f"{hypothesis.status} · revision {hypothesis.revision} · "
                        f"{hypothesis.outcome}"
                    ),
                    aliases=(
                        hypothesis.family_id,
                        hypothesis.description,
                        hypothesis.dataset_id,
                        hypothesis.status,
                        hypothesis.outcome,
                        *hypothesis.factor_research_ids,
                        *hypothesis.lineage.factor_ids,
                    ),
                    created_at=hypothesis.created_at,
                    route=(f"/research-workspace/{quote(hypothesis.hypothesis_id, safe='')}"),
                    metadata={
                        "family_id": hypothesis.family_id,
                        "revision": hypothesis.revision,
                        "status": hypothesis.status,
                        "outcome": hypothesis.outcome,
                        "dataset_id": hypothesis.dataset_id,
                    },
                )
            )

        run_items = self._run_items()
        run_strategy_names = {item.strategy_id: item.strategy_name for item in run_items}
        pair = PAIRS_TRADING_DEFINITION
        documents.append(
            SearchDocument(
                entity_type="STRATEGY",
                entity_id=pair.strategy_id,
                title=pair.name,
                subtitle=f"{pair.version} · {pair.runtime.kind} · {pair.source_type}",
                aliases=(pair.version, pair.source_fingerprint or "", pair.runtime.kind),
                route=_route("/strategy", "strategy_id", pair.strategy_id),
                metadata={
                    "version": pair.version,
                    "fingerprint": pair.source_fingerprint,
                    "runtime": pair.runtime.kind,
                    "framework": pair.runtime.framework_name,
                },
            )
        )
        for registration in self.strategies.list():
            manifest = registration.adapter_manifest
            name = run_strategy_names.get(registration.strategy_id, registration.class_name)
            version = manifest.version if manifest is not None else None
            framework = f" · {registration.framework_name}" if registration.framework_name else ""
            documents.append(
                SearchDocument(
                    entity_type="STRATEGY",
                    entity_id=registration.strategy_id,
                    title=name,
                    subtitle=(
                        f"{version or 'registered'} · {registration.runtime_kind}{framework}"
                    ),
                    aliases=(
                        registration.class_name,
                        version or "",
                        registration.source_fingerprint,
                        registration.runtime_kind,
                        registration.framework_name or "",
                        registration.adapter_id or "",
                    ),
                    created_at=registration.registered_at,
                    route=_route("/strategy", "strategy_id", registration.strategy_id),
                    metadata={
                        "version": version,
                        "fingerprint": registration.source_fingerprint,
                        "runtime": registration.runtime_kind,
                        "framework": registration.framework_name,
                    },
                )
            )

        for run in run_items:
            display_name = run.annotations.display_name.strip()
            documents.append(
                SearchDocument(
                    entity_type="RUN",
                    entity_id=run.run_id,
                    title=display_name or run.run_id,
                    subtitle=f"{run.strategy_name} · {run.dataset_name} · {run.status}",
                    aliases=(
                        run.strategy_id,
                        run.strategy_name,
                        run.dataset_id,
                        run.dataset_name,
                        run.status,
                        run.trace_id or "",
                    ),
                    tags=run.annotations.tags,
                    created_at=run.created_at,
                    route=f"/runs/{quote(run.run_id, safe='')}",
                    metadata={
                        "strategy_id": run.strategy_id,
                        "dataset_id": run.dataset_id,
                        "status": run.status,
                        "trace_id": run.trace_id,
                    },
                )
            )
            if run.trace_id is not None:
                documents.append(
                    SearchDocument(
                        entity_type="TRACE",
                        entity_id=run.trace_id,
                        title=run.trace_id,
                        subtitle=f"{run.strategy_name} · {run.dataset_name}",
                        aliases=(
                            run.run_id,
                            run.strategy_id,
                            run.strategy_name,
                            run.dataset_id,
                            run.dataset_name,
                        ),
                        created_at=run.created_at,
                        route=_route("/replay", "trace_id", run.trace_id),
                        metadata={
                            "run_id": run.run_id,
                            "strategy_id": run.strategy_id,
                            "dataset_id": run.dataset_id,
                        },
                    )
                )

        for snapshot in self.snapshots.list():
            documents.append(
                SearchDocument(
                    entity_type="SNAPSHOT",
                    entity_id=snapshot.snapshot_id,
                    title=snapshot.name,
                    subtitle=(
                        f"{snapshot.hypothesis_id} · revision {snapshot.hypothesis_revision}"
                    ),
                    aliases=(
                        snapshot.hypothesis_id,
                        str(snapshot.hypothesis_revision),
                        snapshot.dataset_id,
                        snapshot.strategy_id,
                        snapshot.content_fingerprint,
                    ),
                    created_at=snapshot.created_at,
                    route=_route("/research-snapshots", "snapshot_id", snapshot.snapshot_id),
                    metadata={
                        "hypothesis_id": snapshot.hypothesis_id,
                        "hypothesis_revision": snapshot.hypothesis_revision,
                        "dataset_id": snapshot.dataset_id,
                        "strategy_id": snapshot.strategy_id,
                        "fingerprint": snapshot.content_fingerprint,
                    },
                )
            )
        return tuple(documents)

    def search(
        self,
        query: str,
        *,
        entity_types: tuple[SearchEntityType, ...] = (),
        limit: int = 20,
    ) -> GlobalSearchResponse:
        normalized = normalize_search_text(query)
        results = (
            ()
            if not normalized
            else rank_search_documents(
                self.documents(), query, entity_types=entity_types, limit=limit
            )
        )
        return GlobalSearchResponse(query=query, normalized_query=normalized, results=results)
