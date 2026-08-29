from __future__ import annotations

import hashlib
import json
import math
import secrets
import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from app.adapters.models import native_runtime
from app.discovery.repository import HypothesisRepository
from app.factors.engine import FactorResearchEngine
from app.factors.models import FactorObservation, FactorResearchRecord
from app.factors.repository import FactorResearchRepository
from app.forward.models import ForwardSessionSnapshot, ForwardTrace
from app.paper.models import PaperSessionManifest, PaperTrace
from app.research_snapshots.models import ResearchSnapshot
from app.research_snapshots.repository import ResearchSnapshotRepository
from app.runs.models import RunManifest
from app.runs.repository import ArtifactIntegrityError, RunNotFoundError, RunRepository
from app.trace.models import BacktestTrace, TimelineEvent

from .models import (
    CreateStrategyDriftReport,
    DriftBaselineType,
    DriftComparability,
    DriftComparabilityCheck,
    DriftDimension,
    DriftDimensionReport,
    DriftMetric,
    DriftMetricStatus,
    DriftObservedType,
    DriftOverallStatus,
    DriftSource,
    DriftTimelineWindow,
    DriftWindowDimension,
    StrategyDriftReport,
)

_DIMENSIONS: tuple[DriftDimension, ...] = (
    "FACTOR",
    "SIGNAL",
    "TURNOVER",
    "EXPOSURE",
    "PERFORMANCE",
)
_STATUS_RANK: dict[DriftMetricStatus, int] = {
    "INSUFFICIENT_EVIDENCE": -1,
    "STABLE": 0,
    "WATCH": 1,
    "DRIFT": 2,
}


class ForwardSessionSource(Protocol):
    strategy_id: str
    dataset_id: str
    strategy_fingerprint: str
    dataset_revision: str | None

    def trace(self) -> ForwardTrace: ...

    def snapshot(self) -> ForwardSessionSnapshot: ...


@dataclass(frozen=True, slots=True)
class _ResolvedEvidence:
    source: DriftSource
    timeline: tuple[TimelineEvent, ...]
    manifest: RunManifest | None = None
    snapshot: ResearchSnapshot | None = None


@dataclass(frozen=True, slots=True)
class _FactorEvidence:
    records: tuple[FactorResearchRecord, ...]
    baseline: tuple[FactorObservation, ...]
    observed: tuple[FactorObservation, ...]
    evidence: tuple[str, ...]


def _canonical(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _runtime_key(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return _canonical(value)


def _parameter_key(values: dict[str, str | int | float | bool]) -> str:
    normalized: dict[str, str | float | bool] = {}
    for key, value in values.items():
        if isinstance(value, (bool, str)):
            normalized[key] = value
        else:
            normalized[key] = float(value)
    return _canonical(normalized)


def _safe_mean(values: Iterable[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    return statistics.fmean(finite) if finite else None


def _safe_std(values: Iterable[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    return statistics.pstdev(finite) if finite else None


def _source_from_run(
    source_type: DriftBaselineType | DriftObservedType,
    source_id: str,
    manifest: RunManifest,
    trace: BacktestTrace,
) -> DriftSource:
    return DriftSource(
        source_type=source_type,
        source_id=source_id,
        resolved_run_id=manifest.run_id,
        trace_id=manifest.trace_id,
        strategy_id=manifest.strategy.strategy_id,
        strategy_fingerprint=manifest.strategy.source_fingerprint,
        parameters=dict(manifest.parameters),
        execution_model=(
            f"{manifest.execution_model.execution_model_id}@{manifest.execution_model.version}"
        ),
        runtime=_runtime_key(manifest.runtime),
        dataset_id=manifest.dataset.dataset_id,
        dataset_revision=manifest.dataset.content_fingerprint,
        sample_size=len(trace.timeline),
        observed_until=trace.timeline[-1].timestamp if trace.timeline else None,
        status="COMPLETED" if manifest.status == "COMPLETED" else "PARTIAL",
    )


def _position_weights(event: TimelineEvent) -> tuple[float, ...]:
    values = tuple(abs(item.market_value) for item in event.position_snapshot.asset_positions)
    gross = sum(values)
    return () if gross <= 1e-12 else tuple(value / gross for value in values)


def _period_returns(events: tuple[TimelineEvent, ...]) -> list[float]:
    equities = [item.pnl_snapshot.equity for item in events]
    return [
        current / previous - 1
        for previous, current in zip(equities, equities[1:], strict=False)
        if abs(previous) > 1e-12
    ]


def _signal_metrics(events: tuple[TimelineEvent, ...]) -> dict[str, float | None]:
    bars = len(events)
    if not bars:
        return {}
    signals = sum(item.signal_evaluation.signal_id is not None for item in events)
    transitions = sum(
        item.signal_evaluation.previous_state != item.signal_evaluation.next_state
        for item in events
    )
    conditions = [condition for item in events for condition in item.signal_evaluation.conditions]
    active_targets = sum(
        bool(item.signal_evaluation.target_positions) or item.signal_evaluation.target_position != 0
        for item in events
    )
    state_counts: dict[str, int] = {}
    for item in events:
        state = item.signal_evaluation.next_state
        state_counts[state] = state_counts.get(state, 0) + 1
    return {
        "signal_frequency": signals / bars,
        "signals_per_100_bars": signals * 100 / bars,
        "state_transition_rate": transitions / bars,
        "condition_true_ratio": (
            None if not conditions else sum(item.result for item in conditions) / len(conditions)
        ),
        "active_target_ratio": active_targets / bars,
        "dominant_state_share": max(state_counts.values(), default=0) / bars,
    }


def _turnover_metrics(events: tuple[TimelineEvent, ...]) -> dict[str, float | None]:
    bars = len(events)
    if not bars:
        return {}
    executions = [item for event in events for item in event.execution_events]
    average_equity = _safe_mean(abs(item.pnl_snapshot.equity) for item in events) or 0.0
    traded_notional = sum(abs(item.traded_notional) for item in executions)
    position_changes = 0
    previous: dict[str, float] = {}
    for event in events:
        current = {item.symbol: item.quantity for item in event.position_snapshot.asset_positions}
        symbols = set(previous) | set(current)
        if any(
            not math.isclose(previous.get(symbol, 0.0), current.get(symbol, 0.0), abs_tol=1e-12)
            for symbol in symbols
        ):
            position_changes += 1
        previous = current
    normalized = None if average_equity <= 1e-12 else traded_notional / average_equity
    return {
        "trades_per_100_bars": len(executions) * 100 / bars,
        "turnover_per_bar": None if normalized is None else normalized / bars,
        "traded_notional_to_average_equity": normalized,
        "position_change_rate": position_changes / bars,
    }


def _exposure_metrics(events: tuple[TimelineEvent, ...]) -> dict[str, float | None]:
    if not events:
        return {}
    gross_ratios: list[float] = []
    net_ratios: list[float] = []
    position_counts: list[float] = []
    largest: list[float] = []
    concentration: list[float] = []
    balances: list[float] = []
    for event in events:
        equity = abs(event.pnl_snapshot.equity)
        position = event.position_snapshot
        if equity > 1e-12:
            gross_ratios.append(position.gross_exposure / equity)
            net_ratios.append(position.net_exposure / equity)
        active = tuple(item for item in position.asset_positions if abs(item.market_value) > 1e-12)
        position_counts.append(float(len(active)))
        weights = _position_weights(event)
        largest.append(max(weights, default=0.0))
        concentration.append(sum(value * value for value in weights))
        long_value = sum(max(item.market_value, 0.0) for item in active)
        short_value = sum(abs(min(item.market_value, 0.0)) for item in active)
        gross = long_value + short_value
        balances.append(0.0 if gross <= 1e-12 else (long_value - short_value) / gross)
    return {
        "average_gross_exposure": _safe_mean(gross_ratios),
        "average_net_exposure": _safe_mean(net_ratios),
        "average_position_count": _safe_mean(position_counts),
        "largest_position_share": _safe_mean(largest),
        "exposure_concentration": _safe_mean(concentration),
        "long_short_balance": _safe_mean(balances),
    }


def _performance_metrics(
    events: tuple[TimelineEvent, ...], *, minimum_bars: int
) -> dict[str, float | None]:
    if len(events) < minimum_bars:
        return {
            "window_return": None,
            "volatility": None,
            "max_drawdown": None,
            "cost_drag": None,
            "pnl_dispersion": None,
            "sharpe": None,
        }
    equities = [item.pnl_snapshot.equity for item in events]
    returns = _period_returns(events)
    peak = equities[0]
    drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 1e-12:
            drawdown = min(drawdown, equity / peak - 1)
    average_equity = _safe_mean(abs(value) for value in equities) or 0.0
    costs = sum(item.cost_snapshot.total_cost for item in events)
    volatility = _safe_std(returns)
    mean_return = _safe_mean(returns)
    sharpe = (
        None
        if len(returns) < 30 or volatility is None or volatility <= 1e-12 or mean_return is None
        else mean_return / volatility * math.sqrt(252)
    )
    return {
        "window_return": (None if abs(equities[0]) <= 1e-12 else equities[-1] / equities[0] - 1),
        "volatility": volatility,
        "max_drawdown": drawdown,
        "cost_drag": None if average_equity <= 1e-12 else costs / average_equity,
        "pnl_dispersion": (
            None
            if average_equity <= 1e-12
            else (_safe_std(item.pnl_snapshot.period_net_pnl for item in events) or 0.0)
            / average_equity
        ),
        "sharpe": sharpe,
    }


def _status_for_distance(distance: float | None) -> DriftMetricStatus:
    if distance is None or not math.isfinite(distance):
        return "INSUFFICIENT_EVIDENCE"
    if distance >= 2.0:
        return "DRIFT"
    if distance >= 1.0:
        return "WATCH"
    return "STABLE"


def _dimension_status(metrics: tuple[DriftMetric, ...]) -> DriftMetricStatus:
    known = [item.status for item in metrics if item.status != "INSUFFICIENT_EVIDENCE"]
    return max(known, key=_STATUS_RANK.__getitem__) if known else "INSUFFICIENT_EVIDENCE"


class StrategyDriftEngine:
    """Build immutable, deterministic drift evidence without rerunning a strategy."""

    def __init__(
        self,
        runs: RunRepository,
        snapshots: ResearchSnapshotRepository,
        forward_lookup: Callable[[str], ForwardSessionSource | None],
        paper_lookup: Callable[[str], tuple[PaperSessionManifest, PaperTrace] | None] | None = None,
        factor_research: FactorResearchRepository | None = None,
        factor_engine: FactorResearchEngine | None = None,
        hypotheses: HypothesisRepository | None = None,
    ) -> None:
        self.runs = runs
        self.snapshots = snapshots
        self.forward_lookup = forward_lookup
        self.paper_lookup = paper_lookup
        self.factor_research = factor_research
        self.factor_engine = factor_engine
        self.hypotheses = hypotheses

    def _run(self, run_id: str, expected_type: str) -> tuple[RunManifest, BacktestTrace]:
        try:
            manifest = self.runs.get_manifest(run_id)
            trace = self.runs.load_trace_for_run(run_id)
        except (RunNotFoundError, ArtifactIntegrityError) as exc:
            raise KeyError(run_id) from exc
        if manifest.run_type != expected_type:
            raise ValueError(
                f"Run '{run_id}' is {manifest.run_type}; expected {expected_type} evidence"
            )
        return manifest, trace

    @staticmethod
    def _snapshot_run(snapshot: ResearchSnapshot) -> tuple[RunManifest, BacktestTrace]:
        if len(snapshot.runs) != 1:
            raise ValueError(
                "A Snapshot baseline must freeze exactly one Run so no baseline is guessed"
            )
        manifest = RunManifest.model_validate_json(snapshot.runs[0].payload_json)
        if manifest.run_type != "BACKTEST":
            raise ValueError("A Snapshot baseline must resolve to a historical BACKTEST Run")
        if manifest.trace_id is None:
            raise ValueError("The Snapshot baseline Run has no Trace")
        trace_artifact = next(
            (item for item in snapshot.traces if item.artifact_id == manifest.trace_id), None
        )
        if trace_artifact is None:
            raise ValueError("The Snapshot baseline does not contain its Run's exact Trace")
        return manifest, BacktestTrace.model_validate_json(trace_artifact.payload_json)

    def _baseline(self, request: CreateStrategyDriftReport) -> _ResolvedEvidence:
        if request.baseline_type == "RUN":
            manifest, trace = self._run(request.baseline_id, "BACKTEST")
            return _ResolvedEvidence(
                source=_source_from_run("RUN", request.baseline_id, manifest, trace),
                timeline=trace.timeline,
                manifest=manifest,
            )
        snapshot = self.snapshots.get(request.baseline_id)
        if snapshot is None:
            raise KeyError(request.baseline_id)
        manifest, trace = self._snapshot_run(snapshot)
        return _ResolvedEvidence(
            source=_source_from_run("SNAPSHOT", request.baseline_id, manifest, trace),
            timeline=trace.timeline,
            manifest=manifest,
            snapshot=snapshot,
        )

    def _observed(self, request: CreateStrategyDriftReport) -> _ResolvedEvidence:
        if request.observed_type == "PAPER_RUN":
            manifest, trace = self._run(request.observed_id, "PAPER")
            return _ResolvedEvidence(
                source=_source_from_run("PAPER_RUN", request.observed_id, manifest, trace),
                timeline=trace.timeline,
                manifest=manifest,
            )
        if request.observed_type == "PAPER_SESSION":
            if self.paper_lookup is None:
                raise KeyError(request.observed_id)
            paper = self.paper_lookup(request.observed_id)
            if paper is None:
                raise KeyError(request.observed_id)
            paper_manifest, paper_trace = paper
            timeline = paper_trace.timeline
            trace_fingerprint = hashlib.sha256(paper_trace.model_dump_json().encode()).hexdigest()
            execution_model = (
                "alpaca-paper-market@1.0"
                if paper_manifest.execution_mode == "ALPACA_PAPER"
                else "paper-next-close@1.0"
            )
            return _ResolvedEvidence(
                source=DriftSource(
                    source_type="PAPER_SESSION",
                    source_id=request.observed_id,
                    trace_id=None,
                    strategy_id=paper_manifest.strategy_id,
                    strategy_fingerprint=paper_manifest.strategy_fingerprint,
                    parameters={
                        **paper_manifest.parameters,
                        "initial_cash": paper_manifest.initial_cash,
                        "fee_bps": paper_manifest.fee_bps,
                        "slippage_bps": paper_manifest.slippage_bps,
                    },
                    execution_model=execution_model,
                    runtime=_runtime_key(native_runtime()),
                    dataset_id=f"recorded-feed:{paper_manifest.session_id}",
                    dataset_revision=f"sha256:{trace_fingerprint}",
                    sample_size=len(timeline),
                    observed_until=(
                        timeline[-1].timestamp if timeline else paper_manifest.last_market_event
                    ),
                    status="COMPLETED" if paper_manifest.status == "STOPPED" else "PARTIAL",
                ),
                timeline=timeline,
            )
        session = self.forward_lookup(request.observed_id)
        if session is None:
            raise KeyError(request.observed_id)
        forward_trace = session.trace()
        forward_snapshot = session.snapshot()
        timeline = forward_trace.timeline
        parameters = forward_trace.parameters
        session_status = str(forward_snapshot.status)
        return _ResolvedEvidence(
            source=DriftSource(
                source_type="FORWARD_SESSION",
                source_id=request.observed_id,
                trace_id=None,
                strategy_id=forward_trace.strategy_id,
                strategy_fingerprint=getattr(session, "strategy_fingerprint", None) or None,
                parameters=dict(parameters),
                execution_model="next-close@1.0",
                runtime=_runtime_key(native_runtime()),
                dataset_id=str(session.dataset_id),
                dataset_revision=getattr(session, "dataset_revision", None),
                sample_size=len(timeline),
                observed_until=timeline[-1].timestamp if timeline else None,
                status="COMPLETED" if session_status == "COMPLETED" else "PARTIAL",
            ),
            timeline=timeline,
        )

    @staticmethod
    def _comparability(
        baseline: DriftSource, observed: DriftSource
    ) -> tuple[DriftComparability, tuple[DriftComparabilityCheck, ...]]:
        baseline_parameters = _parameter_key(baseline.parameters)
        observed_parameters = _parameter_key(observed.parameters)
        CheckField = Literal[
            "strategy_id",
            "strategy_fingerprint",
            "parameters",
            "execution_model",
            "runtime",
        ]
        rows: tuple[tuple[CheckField, str, str, bool], ...] = (
            ("strategy_id", baseline.strategy_id, observed.strategy_id, True),
            (
                "strategy_fingerprint",
                baseline.strategy_fingerprint or "UNAVAILABLE",
                observed.strategy_fingerprint or "UNAVAILABLE",
                True,
            ),
            ("parameters", baseline_parameters, observed_parameters, True),
            ("execution_model", baseline.execution_model, observed.execution_model, False),
            ("runtime", baseline.runtime, observed.runtime, False),
        )
        checks = tuple(
            DriftComparabilityCheck(
                field=field,
                baseline_value=left,
                observed_value=right,
                same=left == right,
                blocking=blocking,
            )
            for field, left, right, blocking in rows
        )
        missing_revision = (
            baseline.strategy_fingerprint is None or observed.strategy_fingerprint is None
        )
        changed_configuration = any(
            not item.same and item.blocking and item.field != "strategy_fingerprint"
            for item in checks
        ) or (
            not missing_revision and baseline.strategy_fingerprint != observed.strategy_fingerprint
        )
        if changed_configuration:
            return "CONFIGURATION_CHANGED", checks
        if missing_revision:
            return "DESCRIPTIVE_ONLY", checks
        if all(item.same for item in checks):
            return "STRICTLY_COMPARABLE", checks
        return "CONTEXTUALLY_COMPARABLE", checks

    @staticmethod
    def _chunks(
        events: tuple[TimelineEvent, ...], window_bars: int
    ) -> tuple[tuple[TimelineEvent, ...], ...]:
        return tuple(
            events[index : index + window_bars] for index in range(0, len(events), window_bars)
        )

    @staticmethod
    def _raw_dimension(
        dimension: DriftDimension,
        events: tuple[TimelineEvent, ...],
        *,
        window_bars: int,
    ) -> dict[str, float | None]:
        if dimension == "SIGNAL":
            return _signal_metrics(events)
        if dimension == "TURNOVER":
            return _turnover_metrics(events)
        if dimension == "EXPOSURE":
            return _exposure_metrics(events)
        if dimension == "PERFORMANCE":
            return _performance_metrics(events, minimum_bars=max(20, window_bars))
        return {}

    @staticmethod
    def _metric(
        name: str,
        baseline_value: float | None,
        observed_value: float | None,
        baseline_distribution: tuple[float, ...],
    ) -> DriftMetric:
        if baseline_value is None or observed_value is None:
            return DriftMetric(
                metric=name,
                baseline_value=baseline_value,
                observed_value=observed_value,
                relative_change=None,
                normalized_distance=None,
                status="INSUFFICIENT_EVIDENCE",
            )
        relative = (
            None
            if abs(baseline_value) <= 1e-12
            else (observed_value - baseline_value) / abs(baseline_value)
        )
        dispersion = (
            statistics.pstdev(baseline_distribution) if len(baseline_distribution) >= 2 else 0.0
        )
        scale = max(dispersion, abs(baseline_value) * 0.25, 0.02)
        distance = abs(observed_value - baseline_value) / scale
        return DriftMetric(
            metric=name,
            baseline_value=baseline_value,
            observed_value=observed_value,
            relative_change=relative,
            normalized_distance=distance,
            status=_status_for_distance(distance),
        )

    def _factor_lineage(self, baseline: _ResolvedEvidence) -> tuple[str, ...]:
        if baseline.snapshot is not None:
            return baseline.snapshot.lineage.factor_research_ids
        if self.hypotheses is None or baseline.source.resolved_run_id is None:
            return ()
        candidates = tuple(
            item
            for item in self.hypotheses.list()
            if baseline.source.resolved_run_id in item.lineage.run_ids
        )
        return candidates[0].lineage.factor_research_ids if len(candidates) == 1 else ()

    def _factor_evidence(
        self, baseline: _ResolvedEvidence, observed: _ResolvedEvidence
    ) -> _FactorEvidence | None:
        research_ids = self._factor_lineage(baseline)
        if not research_ids or self.factor_research is None or self.factor_engine is None:
            return None
        if not observed.timeline or observed.source.dataset_revision is None:
            return None
        observed_dataset = self.factor_engine.datasets.get(observed.source.dataset_id)
        if observed_dataset is None:
            return None
        if observed_dataset.content_fingerprint != observed.source.dataset_revision:
            return None

        records: list[FactorResearchRecord] = []
        baseline_observations: list[FactorObservation] = []
        observed_observations: list[FactorObservation] = []
        evidence: list[str] = []
        observed_start = observed.timeline[0].timestamp
        observed_end = observed.timeline[-1].timestamp
        for research_id in research_ids:
            record = self.factor_research.get(research_id)
            if record is None:
                return None
            if baseline.snapshot is not None:
                frozen = next(
                    (item for item in baseline.snapshot.factors if item.artifact_id == research_id),
                    None,
                )
                revision = record.factor.source_fingerprint or record.factor.version
                if frozen is None or frozen.source_revision != revision:
                    return None
            try:
                baseline_values = self.factor_engine.observations(record)
                observed_record = record.model_copy(
                    update={
                        "dataset_id": observed_dataset.dataset_id,
                        "dataset_name": observed_dataset.name,
                        "dataset_revision": observed_dataset.content_fingerprint,
                        "dataset_family_id": observed_dataset.dataset_family_id,
                        "dataset_revision_number": observed_dataset.revision,
                        "universe_id": None,
                    }
                )
                observed_values = self.factor_engine.observations(observed_record)
            except (KeyError, ValueError):
                return None
            research_values = tuple(
                item
                for item in baseline_values
                if record.periods.research.start <= item.timestamp <= record.periods.research.end
            )
            live_values = tuple(
                item
                for item in observed_values
                if observed_start <= item.timestamp <= observed_end
                and item.available_at <= observed_end
            )
            if not research_values or not live_values:
                return None
            records.append(record)
            baseline_observations.extend(research_values)
            observed_observations.extend(live_values)
            revision = record.factor.source_fingerprint or record.factor.version
            evidence.append(
                f"Canonical Factor Engine · {record.research_id} · "
                f"{record.factor.factor_id}@{revision}"
            )
        return _FactorEvidence(
            tuple(records),
            tuple(baseline_observations),
            tuple(observed_observations),
            tuple(evidence),
        )

    @staticmethod
    def _factor_raw_from_observations(
        observations: tuple[FactorObservation, ...], *, expected: int
    ) -> dict[str, float | None]:
        values = [item.value for item in observations if math.isfinite(item.value)]
        if len(values) < 5:
            return {
                "mean": None,
                "median": None,
                "standard_deviation": None,
                "positive_ratio": None,
                "coverage": None,
                "cross_sectional_dispersion": None,
            }
        grouped: dict[datetime, list[float]] = {}
        for item in observations:
            grouped.setdefault(item.timestamp, []).append(item.value)
        cross_sectional = [
            statistics.pstdev(items) for items in grouped.values() if len(items) >= 2
        ]
        return {
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "standard_deviation": statistics.pstdev(values),
            "positive_ratio": sum(value > 0 for value in values) / len(values),
            "coverage": min(1.0, len(values) / max(expected, 1)),
            "cross_sectional_dispersion": _safe_mean(cross_sectional),
        }

    def _dimension_metrics(
        self,
        dimension: DriftDimension,
        baseline: tuple[TimelineEvent, ...],
        observed: tuple[TimelineEvent, ...],
        *,
        window_bars: int,
        factor: _FactorEvidence | None,
    ) -> tuple[DriftMetric, ...]:
        if dimension == "FACTOR":
            if factor is None:
                return (
                    DriftMetric(
                        metric="factor_distribution",
                        baseline_value=None,
                        observed_value=None,
                        relative_change=None,
                        normalized_distance=None,
                        status="INSUFFICIENT_EVIDENCE",
                    ),
                )
            baseline_raw = self._factor_raw_from_observations(
                factor.baseline,
                expected=sum(max(1, item.factor_observation_count) for item in factor.records),
            )
            observed_factor = factor.observed
            if observed:
                observed_start = observed[0].timestamp
                observed_end = observed[-1].timestamp
                observed_factor = tuple(
                    item
                    for item in factor.observed
                    if observed_start <= item.timestamp <= observed_end
                )
            observed_symbols = {item.symbol for item in observed_factor}
            observed_timestamps = {item.timestamp for item in observed_factor}
            observed_raw = self._factor_raw_from_observations(
                observed_factor,
                expected=max(1, len(observed_symbols) * len(observed_timestamps)),
            )
            return tuple(
                self._metric(name, baseline_raw.get(name), observed_raw.get(name), ())
                for name in baseline_raw
            )
        baseline_raw = self._raw_dimension(dimension, baseline, window_bars=window_bars)
        observed_raw = self._raw_dimension(dimension, observed, window_bars=window_bars)
        window_values: dict[str, list[float]] = {name: [] for name in baseline_raw}
        for window in self._chunks(baseline, window_bars):
            if len(window) < window_bars:
                continue
            values = self._raw_dimension(dimension, window, window_bars=window_bars)
            for name, value in values.items():
                if value is not None and math.isfinite(value):
                    window_values.setdefault(name, []).append(value)
        return tuple(
            self._metric(
                name,
                baseline_raw.get(name),
                observed_raw.get(name),
                tuple(window_values.get(name, ())),
            )
            for name in baseline_raw
        )

    @staticmethod
    def _dimension_evidence(
        dimension: DriftDimension,
        factor: _FactorEvidence | None,
        observed_size: int,
    ) -> tuple[str, ...]:
        if dimension == "FACTOR":
            return (
                (
                    (
                        "No unique explicit Factor Research lineage and canonical recorded "
                        "Factor evidence were available."
                    ),
                )
                if factor is None
                else factor.evidence
            )
        sources = {
            "SIGNAL": "Trace.signal_evaluation and recorded condition outcomes",
            "TURNOVER": "Trace.execution_events and normalized position changes",
            "EXPOSURE": "Trace.position_snapshot normalized by recorded equity",
            "PERFORMANCE": "Trace.pnl_snapshot and cost_snapshot window distributions",
        }
        return (sources[dimension], f"Observed bars: {observed_size}")

    def build(self, request: CreateStrategyDriftReport) -> StrategyDriftReport:
        baseline = self._baseline(request)
        observed = self._observed(request)
        comparability, checks = self._comparability(baseline.source, observed.source)
        factor = (
            None
            if comparability == "CONFIGURATION_CHANGED"
            else self._factor_evidence(baseline, observed)
        )

        window_rows: list[
            tuple[tuple[TimelineEvent, ...], dict[DriftDimension, tuple[DriftMetric, ...]]]
        ] = []
        for window in self._chunks(observed.timeline, request.window_bars):
            metrics_by_dimension = {
                dimension: self._dimension_metrics(
                    dimension,
                    baseline.timeline,
                    window,
                    window_bars=request.window_bars,
                    factor=factor,
                )
                for dimension in _DIMENSIONS
            }
            window_rows.append((window, metrics_by_dimension))

        timeline = tuple(
            DriftTimelineWindow(
                window_index=index,
                start_at=window[0].timestamp,
                end_at=window[-1].timestamp,
                end_event_id=window[-1].event_id,
                sample_size=len(window),
                complete=len(window) == request.window_bars,
                dimensions=tuple(
                    DriftWindowDimension(
                        dimension=dimension,
                        status=(
                            "INSUFFICIENT_EVIDENCE"
                            if comparability == "CONFIGURATION_CHANGED"
                            else _dimension_status(metrics_by_dimension[dimension])
                        ),
                        maximum_normalized_distance=max(
                            (
                                item.normalized_distance
                                for item in metrics_by_dimension[dimension]
                                if item.normalized_distance is not None
                            ),
                            default=None,
                        ),
                    )
                    for dimension in _DIMENSIONS
                ),
            )
            for index, (window, metrics_by_dimension) in enumerate(window_rows, start=1)
            if window
        )

        first_by_dimension: dict[DriftDimension, DriftTimelineWindow] = {}
        for timeline_window in timeline:
            if not timeline_window.complete:
                continue
            for item in timeline_window.dimensions:
                if item.status == "DRIFT" and item.dimension not in first_by_dimension:
                    first_by_dimension[item.dimension] = timeline_window

        dimensions: list[DriftDimensionReport] = []
        for dimension in _DIMENSIONS:
            metrics = (
                tuple(
                    DriftMetric(
                        metric=item.metric,
                        baseline_value=item.baseline_value,
                        observed_value=item.observed_value,
                        relative_change=item.relative_change,
                        normalized_distance=None,
                        status="INSUFFICIENT_EVIDENCE",
                    )
                    for item in self._dimension_metrics(
                        dimension,
                        baseline.timeline,
                        observed.timeline,
                        window_bars=request.window_bars,
                        factor=factor,
                    )
                )
                if comparability == "CONFIGURATION_CHANGED"
                else self._dimension_metrics(
                    dimension,
                    baseline.timeline,
                    observed.timeline,
                    window_bars=request.window_bars,
                    factor=factor,
                )
            )
            status = _dimension_status(metrics)
            dimension_first = first_by_dimension.get(dimension)
            if dimension_first is not None:
                status = "DRIFT"
            dimensions.append(
                DriftDimensionReport(
                    dimension=dimension,
                    status=status,
                    metrics=metrics,
                    first_drift_at=(None if dimension_first is None else dimension_first.end_at),
                    first_drift_event_id=(
                        None if dimension_first is None else dimension_first.end_event_id
                    ),
                    evidence=self._dimension_evidence(
                        dimension, factor, observed.source.sample_size
                    ),
                )
            )

        first_result: tuple[datetime, DriftDimension, str | None] | None = None
        for dimension_report in dimensions:
            if dimension_report.first_drift_at is None:
                continue
            candidate = (
                dimension_report.first_drift_at,
                dimension_report.dimension,
                dimension_report.first_drift_event_id,
            )
            if first_result is None or (candidate[0], _DIMENSIONS.index(candidate[1])) < (
                first_result[0],
                _DIMENSIONS.index(first_result[1]),
            ):
                first_result = candidate
        known_statuses = [
            item.status for item in dimensions if item.status != "INSUFFICIENT_EVIDENCE"
        ]
        incomplete = (
            comparability in {"CONFIGURATION_CHANGED", "DESCRIPTIVE_ONLY"}
            or observed.source.status == "PARTIAL"
            or observed.source.sample_size < request.window_bars
            or not known_statuses
        )
        overall: DriftOverallStatus
        if incomplete:
            overall = "INCOMPLETE"
        elif "DRIFT" in known_statuses:
            overall = "DRIFT"
        elif "WATCH" in known_statuses:
            overall = "WATCH"
        else:
            overall = "STABLE"
        return StrategyDriftReport(
            drift_report_id=f"drift-{secrets.token_hex(12)}",
            baseline_type=request.baseline_type,
            baseline_id=request.baseline_id,
            observed_type=request.observed_type,
            observed_id=request.observed_id,
            created_at=datetime.now(UTC),
            window_bars=request.window_bars,
            baseline=baseline.source,
            observed=observed.source,
            comparability=comparability,
            comparability_checks=checks,
            overall_status=overall,
            dimensions=tuple(dimensions),
            timeline=timeline,
            first_drift_at=None if first_result is None else first_result[0],
            first_drift_dimension=None if first_result is None else first_result[1],
            first_drift_event_id=None if first_result is None else first_result[2],
        )
