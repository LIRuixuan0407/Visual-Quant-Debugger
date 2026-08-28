from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from typing import Literal, cast

from app.discovery.models import ResearchHypothesis
from app.portfolio_lab.models import PortfolioResearchRecord
from app.runs.comparison import compare_run_records
from app.runs.models import RunManifest
from app.trace.models import BacktestTrace

from .models import (
    ArtifactKind,
    ContextSignificance,
    ExperimentArtifactComparison,
    ExperimentComparability,
    ExperimentComparisonReport,
    ExperimentComparisonRequest,
    ExperimentContextComparison,
    ExperimentHypothesisState,
    ExperimentMetricComparison,
    ExperimentParameterComparison,
    ExperimentSnapshotIdentity,
    FrozenArtifact,
    ParameterOwner,
    ResearchSnapshot,
    SnapshotScalar,
)
from .repository import ResearchSnapshotRepository


def _render(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _all_same(values: tuple[object, ...]) -> bool:
    return all(value == values[0] for value in values[1:])


def _run_manifest(snapshot: ResearchSnapshot) -> RunManifest:
    manifest = RunManifest.model_validate_json(snapshot.runs[0].payload_json)
    if manifest.run_id != snapshot.lineage.run_ids[0]:
        raise ValueError(
            f"Snapshot '{snapshot.snapshot_id}' primary Run payload identity does not match"
        )
    return manifest


def _trace(snapshot: ResearchSnapshot) -> BacktestTrace:
    payload = json.loads(snapshot.traces[0].payload_json)
    if not isinstance(payload, dict) or "trace" not in payload:
        raise ValueError(f"Snapshot '{snapshot.snapshot_id}' frozen Trace payload is invalid")
    return BacktestTrace.model_validate(payload["trace"])


def _stage_periods(snapshot: ResearchSnapshot) -> str:
    boundaries = snapshot.time_boundaries
    return _render(
        {
            item.label: {"start": item.start, "end": item.end, "cutoff": item.cutoff}
            for item in (boundaries.research, boundaries.validation, boundaries.holdout)
        }
    )


def _run_period(manifest: RunManifest) -> str:
    return _render(
        {
            "start": manifest.period.start,
            "end": manifest.period.end,
            "cutoff": manifest.period.cutoff,
        }
    )


def _execution_model(manifest: RunManifest) -> str:
    return _render(manifest.execution_model.model_dump(mode="json"))


def _runtime(manifest: RunManifest) -> str:
    return _render(manifest.runtime.model_dump(mode="json"))


def _environment(snapshot: ResearchSnapshot) -> str:
    return _render(snapshot.environment.model_dump(mode="json"))


def _context(
    snapshots: tuple[ResearchSnapshot, ...],
    manifests: tuple[RunManifest, ...],
) -> tuple[ExperimentContextComparison, ...]:
    context_specs: tuple[
        tuple[
            str,
            ContextSignificance,
            Callable[[ResearchSnapshot, RunManifest], str],
        ],
        ...,
    ] = (
        (
            "dataset_revision",
            "STRICT_CONTROL",
            lambda snapshot, _: snapshot.dataset.source_revision,
        ),
        (
            "universe_revisions",
            "STRICT_CONTROL",
            lambda snapshot, _: _render(tuple(item.source_revision for item in snapshot.universes)),
        ),
        (
            "corporate_action_revisions",
            "STRICT_CONTROL",
            lambda snapshot, _: _render(
                tuple(item.source_revision for item in snapshot.corporate_actions)
            ),
        ),
        (
            "research_periods",
            "STRICT_CONTROL",
            lambda snapshot, _: _stage_periods(snapshot),
        ),
        (
            "run_period",
            "STRICT_CONTROL",
            lambda _, manifest: _run_period(manifest),
        ),
        (
            "execution_model",
            "STRICT_CONTROL",
            lambda _, manifest: _execution_model(manifest),
        ),
        ("runtime", "CONTEXT", lambda _, manifest: _runtime(manifest)),
        (
            "creation_environment",
            "INFORMATIONAL",
            lambda snapshot, _: _environment(snapshot),
        ),
    )
    rows: list[ExperimentContextComparison] = []
    for field, significance, projection in context_specs:
        values = tuple(
            projection(snapshot, manifest)
            for snapshot, manifest in zip(snapshots, manifests, strict=True)
        )
        rows.append(
            ExperimentContextComparison(
                field=cast(
                    Literal[
                        "dataset_revision",
                        "universe_revisions",
                        "corporate_action_revisions",
                        "research_periods",
                        "run_period",
                        "execution_model",
                        "runtime",
                        "creation_environment",
                    ],
                    field,
                ),
                same=_all_same(values),
                significance=significance,
                values=values,
            )
        )
    return tuple(rows)


def _comparability(
    context: tuple[ExperimentContextComparison, ...],
) -> ExperimentComparability:
    by_field = {item.field: item for item in context}
    if not by_field["dataset_revision"].same:
        return "DESCRIPTIVE_ONLY"
    return (
        "STRICTLY_COMPARABLE"
        if all(item.same for item in context if item.field != "creation_environment")
        else "CONTEXTUALLY_COMPARABLE"
    )


def _unique_key(base: str, counts: Counter[str]) -> str:
    counts[base] += 1
    return base if counts[base] == 1 else f"{base}:{counts[base]}"


def _artifacts(snapshot: ResearchSnapshot) -> dict[tuple[ArtifactKind, str], FrozenArtifact]:
    result: dict[tuple[ArtifactKind, str], FrozenArtifact] = {}
    result[("DATASET", "DATASET")] = snapshot.dataset
    for artifact in snapshot.universes:
        result[("UNIVERSE", f"UNIVERSE:{artifact.artifact_id}")] = artifact
    for artifact in snapshot.corporate_actions:
        result[
            (
                "CORPORATE_ACTION_DATASET",
                f"CORPORATE_ACTION_DATASET:{artifact.artifact_id}",
            )
        ] = artifact
    for factor_id, artifact in zip(snapshot.lineage.factor_ids, snapshot.factors, strict=True):
        result[("FACTOR_RESEARCH", f"FACTOR:{factor_id}")] = artifact

    relationship_counts: Counter[str] = Counter()
    for artifact in snapshot.relationships:
        payload = json.loads(artifact.payload_json)
        factor_ids = ",".join(sorted(str(item) for item in payload.get("factor_ids", [])))
        base = f"RELATIONSHIP:{payload.get('stage')}:{payload.get('horizon')}:{factor_ids}"
        result[(artifact.kind, _unique_key(base, relationship_counts))] = artifact

    walk_forward_counts: Counter[str] = Counter()
    for artifact in snapshot.walk_forward:
        payload = json.loads(artifact.payload_json)
        base = f"WALK_FORWARD:{payload.get('factor_id')}:{payload.get('horizon')}"
        result[(artifact.kind, _unique_key(base, walk_forward_counts))] = artifact

    result[("HYPOTHESIS", "HYPOTHESIS")] = snapshot.hypothesis
    result[("PORTFOLIO_RESEARCH", "PORTFOLIO")] = snapshot.portfolio
    result[("STRATEGY_SOURCE", "STRATEGY")] = snapshot.strategy
    for index, artifact in enumerate(snapshot.runs, start=1):
        result[("RUN_MANIFEST", f"RUN:{index}")] = artifact
    for index, artifact in enumerate(snapshot.traces, start=1):
        result[("TRACE", f"TRACE:{index}")] = artifact
    return result


def _artifact_diff(
    snapshots: tuple[ResearchSnapshot, ...],
) -> tuple[ExperimentArtifactComparison, ...]:
    maps = tuple(_artifacts(snapshot) for snapshot in snapshots)
    keys = sorted(set().union(*(set(items) for items in maps)))
    return tuple(
        ExperimentArtifactComparison(
            kind=kind,
            semantic_key=semantic_key,
            artifact_ids=tuple(
                None
                if (artifact := items.get((kind, semantic_key))) is None
                else artifact.artifact_id
                for items in maps
            ),
            source_revisions=tuple(
                None
                if (artifact := items.get((kind, semantic_key))) is None
                else artifact.source_revision
                for items in maps
            ),
            payload_fingerprints=tuple(
                None
                if (artifact := items.get((kind, semantic_key))) is None
                else artifact.payload_sha256
                for items in maps
            ),
            same_revision=_all_same(
                tuple(
                    None
                    if (artifact := items.get((kind, semantic_key))) is None
                    else artifact.source_revision
                    for items in maps
                )
            ),
        )
        for kind, semantic_key in keys
    )


def _parameter_map(
    snapshot: ResearchSnapshot,
) -> dict[tuple[ParameterOwner, str, str], SnapshotScalar]:
    factor_keys = dict(
        zip(snapshot.lineage.factor_research_ids, snapshot.lineage.factor_ids, strict=True)
    )
    run_keys = {
        run_id: f"RUN:{index}" for index, run_id in enumerate(snapshot.lineage.run_ids, start=1)
    }
    result: dict[tuple[ParameterOwner, str, str], SnapshotScalar] = {}
    for parameter_set in snapshot.parameters:
        owner_key = (
            factor_keys.get(parameter_set.owner_id, parameter_set.owner_id)
            if parameter_set.owner_type == "FACTOR"
            else run_keys.get(parameter_set.owner_id, parameter_set.owner_id)
            if parameter_set.owner_type == "RUN"
            else parameter_set.owner_type
        )
        for item in parameter_set.values:
            result[(parameter_set.owner_type, owner_key, item.key)] = item.value
    return result


def _parameter_diff(
    snapshots: tuple[ResearchSnapshot, ...],
) -> tuple[ExperimentParameterComparison, ...]:
    maps = tuple(_parameter_map(snapshot) for snapshot in snapshots)
    keys = sorted(set().union(*(set(items) for items in maps)))
    rows: list[ExperimentParameterComparison] = []
    for owner_type, owner_key, parameter in keys:
        values = tuple(items.get((owner_type, owner_key, parameter)) for items in maps)
        if _all_same(values):
            continue
        rows.append(
            ExperimentParameterComparison(
                owner_type=owner_type,
                owner_key=owner_key,
                parameter=parameter,
                values=values,
            )
        )
    return tuple(rows)


def _metric_map(
    snapshot: ResearchSnapshot,
    manifest: RunManifest,
) -> dict[tuple[str, str], float | int | None]:
    portfolio = PortfolioResearchRecord.model_validate_json(snapshot.portfolio.payload_json)
    result: dict[tuple[str, str], float | int | None] = {}
    for stage in portfolio.stages:
        preview = stage.cost_preview
        scope = f"PORTFOLIO:{stage.stage}"
        result.update(
            {
                (scope, "gross_return"): preview.gross_return,
                (scope, "net_return"): preview.net_return,
                (scope, "turnover"): preview.turnover,
                (scope, "max_drawdown"): preview.max_drawdown,
                (scope, "fees"): preview.fees,
                (scope, "slippage"): preview.slippage,
                (scope, "positions"): preview.positions,
                (scope, "rebalance_count"): preview.rebalance_count,
            }
        )
    metrics = manifest.metrics
    for metric in (
        "total_return",
        "sharpe",
        "max_drawdown",
        "turnover",
        "trades",
        "final_equity",
        "fees",
        "slippage",
        "net_pnl",
    ):
        result[("PRIMARY_RUN", metric)] = (
            None if metrics is None else cast(float | int, getattr(metrics, metric))
        )
    return result


def _metric_diff(
    snapshots: tuple[ResearchSnapshot, ...],
    manifests: tuple[RunManifest, ...],
) -> tuple[ExperimentMetricComparison, ...]:
    maps = tuple(
        _metric_map(snapshot, manifest)
        for snapshot, manifest in zip(snapshots, manifests, strict=True)
    )
    keys = sorted(set().union(*(set(items) for items in maps)))
    rows: list[ExperimentMetricComparison] = []
    for scope, metric in keys:
        values = tuple(items.get((scope, metric)) for items in maps)
        baseline = values[0]
        differences = tuple(
            None
            if index == 0 or baseline is None or value is None
            else float(value) - float(baseline)
            for index, value in enumerate(values)
        )
        rows.append(
            ExperimentMetricComparison(
                scope=scope,
                metric=metric,
                values=values,
                differences_from_first=differences,
            )
        )
    return tuple(rows)


def _hypothesis_state(snapshot: ResearchSnapshot) -> ExperimentHypothesisState:
    hypothesis = ResearchHypothesis.model_validate_json(snapshot.hypothesis.payload_json)
    stances = Counter(item.stance for item in hypothesis.evidence)
    return ExperimentHypothesisState(
        snapshot_id=snapshot.snapshot_id,
        status=hypothesis.status,
        outcome=hypothesis.outcome,
        supporting_evidence=stances["SUPPORTING"],
        contradicting_evidence=stances["CONTRADICTING"],
        neutral_evidence=stances["NEUTRAL"],
    )


def compare_experiments(
    repository: ResearchSnapshotRepository,
    request: ExperimentComparisonRequest,
) -> ExperimentComparisonReport:
    snapshots: list[ResearchSnapshot] = []
    for snapshot_id in request.snapshot_ids:
        snapshot = repository.get(snapshot_id)
        if snapshot is None:
            raise KeyError(f"Research Snapshot '{snapshot_id}' was not found")
        snapshots.append(snapshot)
    frozen = tuple(snapshots)
    manifests = tuple(_run_manifest(snapshot) for snapshot in frozen)
    traces = tuple(_trace(snapshot) for snapshot in frozen)
    context = _context(frozen, manifests)
    return ExperimentComparisonReport(
        snapshot_ids=request.snapshot_ids,
        snapshots=tuple(
            ExperimentSnapshotIdentity(
                snapshot_id=snapshot.snapshot_id,
                name=snapshot.name,
                content_fingerprint=snapshot.content_fingerprint,
                hypothesis_id=snapshot.lineage.hypothesis_id,
                hypothesis_revision=snapshot.lineage.hypothesis_revision,
                run_id=snapshot.lineage.run_ids[0],
                trace_id=snapshot.lineage.trace_ids[0],
            )
            for snapshot in frozen
        ),
        comparability=_comparability(context),
        context_diff=context,
        artifact_diff=_artifact_diff(frozen),
        parameter_diff=_parameter_diff(frozen),
        metric_diff=_metric_diff(frozen, manifests),
        hypothesis_states=tuple(_hypothesis_state(snapshot) for snapshot in frozen),
        primary_run_comparison=compare_run_records(manifests, traces),
    )
