from __future__ import annotations

import math
import secrets
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations

import numpy as np

from app.datasets import DatasetRegistry
from app.factors import FactorResearchEngine
from app.factors.models import (
    FactorObservation,
    FactorResearchRecord,
    ResearchPeriod,
    ResearchStage,
)
from app.factors.repository import FactorResearchRepository
from app.research_ledger import ResearchLedgerEntry, ResearchLedgerRepository, research_ledger

from .models import (
    CorrelationCell,
    CorrelationSemantic,
    CreateFactorRelationship,
    ExposureOverlap,
    ExposureOverlapPoint,
    FactorCluster,
    FactorRelationshipRecord,
    IncrementalInformation,
    RedundancyAssessment,
    RedundancyStatus,
    RollingCorrelationPoint,
    RollingCorrelationSeries,
)

_STAGE_ORDER: dict[ResearchStage, int] = {"RESEARCH": 0, "VALIDATION": 1, "HOLDOUT": 2}
type ValueGrid = dict[datetime, dict[str, float]]
type ObservationGrid = dict[datetime, dict[str, FactorObservation]]
type AlignedPairs = dict[datetime, tuple[tuple[float, ...], tuple[float, ...]]]


@dataclass(frozen=True, slots=True)
class _AssociationMetrics:
    rank_ic: float | None
    spread: float | None
    coverage: float
    turnover: float | None
    top_return: float | None


def _pearson(
    left: list[float] | tuple[float, ...],
    right: list[float] | tuple[float, ...],
) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    if len(set(left)) < 2 or len(set(right)) < 2:
        return None
    value = float(np.corrcoef(np.asarray(left), np.asarray(right))[0, 1])
    return value if math.isfinite(value) else None


def _ranks(values: list[float] | tuple[float, ...]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[cursor]]:
            end += 1
        rank = (cursor + end - 1) / 2 + 1
        for index in ordered[cursor:end]:
            result[index] = rank
        cursor = end
    return result


def _spearman(left: tuple[float, ...], right: tuple[float, ...]) -> float | None:
    return _pearson(_ranks(left), _ranks(right))


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _delta(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else right - left


def _percentiles(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    symbols = sorted(values)
    ranks = _ranks([values[symbol] for symbol in symbols])
    if len(symbols) == 1:
        return {symbols[0]: 0.5}
    return {
        symbol: (rank - 1) / (len(symbols) - 1) for symbol, rank in zip(symbols, ranks, strict=True)
    }


def _flatten(
    pairs: AlignedPairs,
    timestamps: list[datetime] | None = None,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    selected = sorted(pairs) if timestamps is None else timestamps
    return (
        tuple(value for timestamp in selected for value in pairs[timestamp][0]),
        tuple(value for timestamp in selected for value in pairs[timestamp][1]),
    )


class FactorRelationshipEngine:
    def __init__(
        self,
        datasets: DatasetRegistry,
        factor_repository: FactorResearchRepository,
        factor_engine: FactorResearchEngine,
        ledger: ResearchLedgerRepository | None = None,
    ) -> None:
        self.datasets = datasets
        self.factor_repository = factor_repository
        self.factor_engine = factor_engine
        self.ledger = ledger or research_ledger

    @staticmethod
    def _period(record: FactorResearchRecord, stage: ResearchStage) -> ResearchPeriod:
        if stage == "RESEARCH":
            return record.periods.research
        if stage == "VALIDATION":
            return record.periods.validation
        return record.periods.holdout

    def _records(self, request: CreateFactorRelationship) -> tuple[FactorResearchRecord, ...]:
        records: list[FactorResearchRecord] = []
        for research_id in request.factor_research_ids:
            record = self.factor_repository.get(research_id)
            if record is None:
                raise KeyError(f"Factor research '{research_id}' was not found")
            if _STAGE_ORDER[record.revealed_stage] < _STAGE_ORDER[request.stage]:
                raise ValueError(
                    f"{request.stage} is still sealed for factor research '{research_id}'"
                )
            records.append(record)
        first = records[0]
        for record in records[1:]:
            if record.dataset_id != first.dataset_id:
                raise ValueError("Factor relationship studies require one market dataset")
            if record.dataset_revision != first.dataset_revision:
                raise ValueError("Factor relationship studies require one dataset revision")
            if record.universe != first.universe:
                raise ValueError("Factor relationship studies require one research universe")
            if record.periods != first.periods:
                raise ValueError(
                    "Factor relationship studies require matching "
                    "Research/Validation/Holdout periods"
                )
        dataset = self.datasets.get(first.dataset_id)
        if dataset is None:
            raise KeyError(f"Dataset '{first.dataset_id}' was not found")
        if dataset.content_fingerprint != first.dataset_revision:
            raise ValueError("Factor relationship dataset fingerprint no longer matches")
        return tuple(records)

    def _observation_grids(
        self,
        records: tuple[FactorResearchRecord, ...],
        period: ResearchPeriod,
    ) -> tuple[ObservationGrid, ...]:
        result: list[ObservationGrid] = []
        for record in records:
            grid: ObservationGrid = {}
            for item in self.factor_engine.observations(record):
                if (
                    period.start <= item.timestamp <= period.end
                    and item.available_at <= item.timestamp
                ):
                    grid.setdefault(item.timestamp, {})[item.symbol] = item
            result.append(grid)
        return tuple(result)

    @staticmethod
    def _value_grid(observations: ObservationGrid) -> ValueGrid:
        return {
            timestamp: {symbol: item.value for symbol, item in values.items()}
            for timestamp, values in observations.items()
        }

    @staticmethod
    def _rank_grid(values: ValueGrid, direction: str | None = None) -> ValueGrid:
        result: ValueGrid = {}
        for timestamp, rows in values.items():
            adjusted = (
                rows
                if direction is None or direction == "HIGH"
                else {symbol: -value for symbol, value in rows.items()}
            )
            result[timestamp] = _percentiles(adjusted)
        return result

    @staticmethod
    def _factor_returns(
        observations: ObservationGrid,
        period: ResearchPeriod,
        horizon: int,
        direction: str,
    ) -> ValueGrid:
        result: ValueGrid = {}
        for timestamp, rows in observations.items():
            eligible = [
                item
                for item in rows.values()
                if item.future_returns.get(horizon) is not None
                and (endpoint := item.future_return_timestamps.get(horizon)) is not None
                and period.start <= endpoint <= period.end
            ]
            if len(eligible) < 2:
                continue
            ordered = sorted(
                eligible,
                key=lambda item: (
                    item.value if direction == "HIGH" else -item.value,
                    item.symbol,
                ),
            )
            quintile_count = max(1, math.ceil(len(ordered) / 5))
            bottom = _mean(
                [
                    value
                    for item in ordered[:quintile_count]
                    if (value := item.future_returns[horizon]) is not None
                ]
            )
            top = _mean(
                [
                    value
                    for item in ordered[-quintile_count:]
                    if (value := item.future_returns[horizon]) is not None
                ]
            )
            if bottom is not None and top is not None:
                result[timestamp] = {"factor_return": top - bottom}
        return result

    @staticmethod
    def _aligned(left: ValueGrid, right: ValueGrid) -> AlignedPairs:
        result: AlignedPairs = {}
        for timestamp in sorted(set(left) & set(right)):
            keys = sorted(set(left[timestamp]) & set(right[timestamp]))
            if not keys:
                continue
            result[timestamp] = (
                tuple(left[timestamp][key] for key in keys),
                tuple(right[timestamp][key] for key in keys),
            )
        return result

    @staticmethod
    def _cell(
        left_id: str,
        right_id: str,
        semantic: CorrelationSemantic,
        pairs: AlignedPairs,
    ) -> CorrelationCell:
        left, right = _flatten(pairs)
        return CorrelationCell(
            left_research_id=left_id,
            right_research_id=right_id,
            semantic=semantic,
            pearson=_pearson(left, right),
            spearman=_spearman(left, right),
            observations=len(left),
        )

    @staticmethod
    def _rolling(
        left_id: str,
        right_id: str,
        semantic: CorrelationSemantic,
        pairs: AlignedPairs,
        window: int,
    ) -> RollingCorrelationSeries:
        timestamps = sorted(pairs)
        points: list[RollingCorrelationPoint] = []
        for end in range(window - 1, len(timestamps)):
            selected = timestamps[end - window + 1 : end + 1]
            left, right = _flatten(pairs, selected)
            points.append(
                RollingCorrelationPoint(
                    timestamp=timestamps[end],
                    pearson=_pearson(left, right),
                    spearman=_spearman(left, right),
                    observations=len(left),
                )
            )
        return RollingCorrelationSeries(
            left_research_id=left_id,
            right_research_id=right_id,
            semantic=semantic,
            window=window,
            points=tuple(points),
        )

    @staticmethod
    def _overlap(
        left_id: str,
        right_id: str,
        left: ValueGrid,
        right: ValueGrid,
        top_percent: float,
    ) -> ExposureOverlap:
        points: list[ExposureOverlapPoint] = []
        for timestamp in sorted(set(left) & set(right)):
            left_count = max(1, math.ceil(len(left[timestamp]) * top_percent / 100))
            right_count = max(1, math.ceil(len(right[timestamp]) * top_percent / 100))
            left_top = set(
                sorted(left[timestamp], key=lambda symbol: (left[timestamp][symbol], symbol))[
                    -left_count:
                ]
            )
            right_top = set(
                sorted(right[timestamp], key=lambda symbol: (right[timestamp][symbol], symbol))[
                    -right_count:
                ]
            )
            intersection = len(left_top & right_top)
            union = len(left_top | right_top)
            points.append(
                ExposureOverlapPoint(
                    timestamp=timestamp,
                    intersection_count=intersection,
                    union_count=union,
                    overlap_percent=intersection / max(min(len(left_top), len(right_top)), 1),
                    jaccard=intersection / union if union else 0.0,
                )
            )
        return ExposureOverlap(
            left_research_id=left_id,
            right_research_id=right_id,
            top_percent=top_percent,
            mean_intersection_count=(
                statistics.fmean(item.intersection_count for item in points) if points else 0.0
            ),
            mean_union_count=(
                statistics.fmean(item.union_count for item in points) if points else 0.0
            ),
            mean_overlap=(
                statistics.fmean(item.overlap_percent for item in points) if points else 0.0
            ),
            mean_jaccard=(statistics.fmean(item.jaccard for item in points) if points else 0.0),
            timestamps=len(points),
            points=tuple(points),
        )

    def _eligible_timestamps(
        self,
        dataset_id: str,
        universe: tuple[str, ...],
        period: ResearchPeriod,
        horizon: int,
    ) -> set[datetime]:
        frames = self.datasets.load_frames(dataset_id, universe)
        return {
            frame.timestamp
            for index, frame in enumerate(frames)
            if period.start <= frame.timestamp <= period.end
            and index + horizon < len(frames)
            and frames[index + horizon].timestamp <= period.end
        }

    @staticmethod
    def _association_metrics(
        scores: ValueGrid,
        observations: ObservationGrid,
        eligible_timestamps: set[datetime],
        horizon: int,
        universe_size: int,
        top_percent: float,
    ) -> _AssociationMetrics:
        daily_rank_ic: list[float] = []
        daily_spreads: list[float] = []
        daily_top_returns: list[float] = []
        top_sets: list[set[str]] = []
        observation_count = 0
        for timestamp in sorted(eligible_timestamps & set(scores) & set(observations)):
            available = [
                (symbol, score, observations[timestamp][symbol].future_returns.get(horizon))
                for symbol, score in scores[timestamp].items()
                if symbol in observations[timestamp]
                and observations[timestamp][symbol].future_returns.get(horizon) is not None
            ]
            if len(available) < 2:
                continue
            ordered = sorted(available, key=lambda item: (item[1], item[0]))
            score_values = [item[1] for item in ordered]
            forward_returns = [float(item[2]) for item in ordered if item[2] is not None]
            rank_ic = _pearson(_ranks(score_values), _ranks(forward_returns))
            if rank_ic is not None:
                daily_rank_ic.append(rank_ic)
            quintile_count = max(1, math.ceil(len(ordered) / 5))
            bottom = _mean(
                [float(value) for _, _, value in ordered[:quintile_count] if value is not None]
            )
            top = _mean(
                [float(value) for _, _, value in ordered[-quintile_count:] if value is not None]
            )
            if bottom is not None and top is not None:
                daily_spreads.append(top - bottom)
            top_count = max(1, math.ceil(len(ordered) * top_percent / 100))
            top_sets.append({item[0] for item in ordered[-top_count:]})
            portfolio_return = _mean(
                [float(value) for _, _, value in ordered[-top_count:] if value is not None]
            )
            if portfolio_return is not None:
                daily_top_returns.append(portfolio_return)
            observation_count += len(ordered)
        turnovers = [
            1 - len(previous & current) / max(len(previous), 1)
            for previous, current in zip(top_sets, top_sets[1:], strict=False)
        ]
        potential = len(eligible_timestamps) * universe_size
        return _AssociationMetrics(
            rank_ic=_mean(daily_rank_ic),
            spread=_mean(daily_spreads),
            coverage=observation_count / potential if potential else 0.0,
            turnover=_mean(turnovers),
            top_return=_mean(daily_top_returns),
        )

    @staticmethod
    def _composite(left: ValueGrid, right: ValueGrid) -> ValueGrid:
        result: ValueGrid = {}
        for timestamp in sorted(set(left) & set(right)):
            symbols = set(left[timestamp]) & set(right[timestamp])
            result[timestamp] = {
                symbol: 0.5 * left[timestamp][symbol] + 0.5 * right[timestamp][symbol]
                for symbol in symbols
            }
        return result

    @staticmethod
    def _redundancy(
        pairs: tuple[tuple[str, str], ...],
        rank_cells: dict[tuple[str, str], CorrelationCell],
        overlaps: dict[tuple[str, str], ExposureOverlap],
        correlation_threshold: float,
        overlap_threshold: float,
    ) -> tuple[RedundancyAssessment, ...]:
        result: list[RedundancyAssessment] = []
        for left_id, right_id in pairs:
            correlation = rank_cells[(left_id, right_id)].pearson
            overlap = overlaps[(left_id, right_id)].mean_overlap
            high_correlation = correlation is not None and abs(correlation) >= correlation_threshold
            high_overlap = overlap >= overlap_threshold
            status: RedundancyStatus
            if high_correlation and high_overlap:
                status = "HIGH_REDUNDANCY"
                reason = (
                    "High absolute cross-sectional rank correlation and high internal "
                    "top-quantile portfolio overlap. Review the pair; nothing is removed "
                    "or reweighted."
                )
            elif (
                correlation is not None and abs(correlation) >= correlation_threshold * 0.75
            ) or overlap >= overlap_threshold * 0.75:
                status = "RELATED"
                reason = (
                    "The pair shares either meaningful rank association or internal portfolio "
                    "overlap, but does not satisfy both high-redundancy thresholds."
                )
            else:
                status = "LOW_REDUNDANCY"
                reason = (
                    "The pair stays below the configured rank-correlation and internal-overlap "
                    "redundancy rules."
                )
            result.append(
                RedundancyAssessment(
                    left_research_id=left_id,
                    right_research_id=right_id,
                    status=status,
                    rank_correlation=correlation,
                    top_quantile_overlap=overlap,
                    reason=reason,
                )
            )
        return tuple(result)

    @staticmethod
    def _clusters(
        research_ids: tuple[str, ...],
        rank_cells: dict[tuple[str, str], CorrelationCell],
        threshold: float,
    ) -> tuple[FactorCluster, ...]:
        neighbors = {research_id: set[str]() for research_id in research_ids}
        for left_id, right_id in combinations(research_ids, 2):
            correlation = rank_cells[(left_id, right_id)].pearson
            if correlation is not None and abs(correlation) >= threshold:
                neighbors[left_id].add(right_id)
                neighbors[right_id].add(left_id)
        remaining = set(research_ids)
        clusters: list[FactorCluster] = []
        while remaining:
            seed = min(remaining)
            stack = [seed]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(neighbors[current] - component)
            remaining -= component
            clusters.append(
                FactorCluster(
                    cluster_id=f"cluster-{len(clusters) + 1}",
                    factor_research_ids=tuple(item for item in research_ids if item in component),
                    rule=(
                        "Connected component where abs(cross-sectional rank Pearson) "
                        f">= {threshold:.2f}"
                    ),
                )
            )
        return tuple(clusters)

    def create(self, request: CreateFactorRelationship) -> FactorRelationshipRecord:
        records = self._records(request)
        first = records[0]
        period = self._period(first, request.stage)
        observation_grids = self._observation_grids(records, period)
        value_grids = tuple(self._value_grid(item) for item in observation_grids)
        raw_rank_grids = tuple(self._rank_grid(item) for item in value_grids)
        direction_rank_grids = tuple(
            self._rank_grid(values, record.factor.direction)
            for values, record in zip(value_grids, records, strict=True)
        )
        return_grids = tuple(
            self._factor_returns(
                observations,
                period,
                request.horizon,
                record.factor.direction,
            )
            for observations, record in zip(observation_grids, records, strict=True)
        )
        ids = request.factor_research_ids
        grids_by_semantic: dict[CorrelationSemantic, tuple[ValueGrid, ...]] = {
            "FACTOR_VALUES": value_grids,
            "FACTOR_RANKS": raw_rank_grids,
            "FACTOR_RETURNS": return_grids,
        }
        cells: dict[CorrelationSemantic, list[CorrelationCell]] = {
            "FACTOR_VALUES": [],
            "FACTOR_RANKS": [],
            "FACTOR_RETURNS": [],
        }
        pair_data: dict[tuple[CorrelationSemantic, int, int], AlignedPairs] = {}
        for semantic, grids in grids_by_semantic.items():
            for left_index, left_id in enumerate(ids):
                for right_index, right_id in enumerate(ids):
                    aligned = self._aligned(grids[left_index], grids[right_index])
                    pair_data[(semantic, left_index, right_index)] = aligned
                    cells[semantic].append(self._cell(left_id, right_id, semantic, aligned))

        distinct_pairs = tuple(combinations(ids, 2))
        index_by_id = {research_id: index for index, research_id in enumerate(ids)}
        semantics: tuple[CorrelationSemantic, ...] = (
            "FACTOR_VALUES",
            "FACTOR_RANKS",
            "FACTOR_RETURNS",
        )
        rolling = tuple(
            self._rolling(
                left_id,
                right_id,
                semantic,
                pair_data[(semantic, index_by_id[left_id], index_by_id[right_id])],
                request.rolling_window,
            )
            for left_id, right_id in distinct_pairs
            for semantic in semantics
        )
        overlaps = {
            (left_id, right_id): self._overlap(
                left_id,
                right_id,
                direction_rank_grids[index_by_id[left_id]],
                direction_rank_grids[index_by_id[right_id]],
                request.top_percent,
            )
            for left_id, right_id in distinct_pairs
        }
        rank_cells = {
            (item.left_research_id, item.right_research_id): item for item in cells["FACTOR_RANKS"]
        }
        redundancy = self._redundancy(
            distinct_pairs,
            rank_cells,
            overlaps,
            request.redundancy_threshold,
            request.overlap_threshold,
        )

        eligible_timestamps = self._eligible_timestamps(
            first.dataset_id,
            first.universe,
            period,
            request.horizon,
        )
        base_metrics = tuple(
            self._association_metrics(
                scores,
                observations,
                eligible_timestamps,
                request.horizon,
                len(first.universe),
                request.top_percent,
            )
            for scores, observations in zip(direction_rank_grids, observation_grids, strict=True)
        )
        incremental: list[IncrementalInformation] = []
        for base_index, base_id in enumerate(ids):
            for added_index, added_id in enumerate(ids):
                if base_index == added_index:
                    continue
                composite = self._composite(
                    direction_rank_grids[base_index], direction_rank_grids[added_index]
                )
                composite_metrics = self._association_metrics(
                    composite,
                    observation_grids[base_index],
                    eligible_timestamps,
                    request.horizon,
                    len(first.universe),
                    request.top_percent,
                )
                base = base_metrics[base_index]
                incremental.append(
                    IncrementalInformation(
                        base_research_id=base_id,
                        added_research_id=added_id,
                        base_rank_ic=base.rank_ic,
                        composite_rank_ic=composite_metrics.rank_ic,
                        rank_ic_delta=_delta(base.rank_ic, composite_metrics.rank_ic),
                        base_spread=base.spread,
                        composite_spread=composite_metrics.spread,
                        spread_delta=_delta(base.spread, composite_metrics.spread),
                        base_coverage=base.coverage,
                        composite_coverage=composite_metrics.coverage,
                        coverage_delta=composite_metrics.coverage - base.coverage,
                        base_turnover=base.turnover,
                        composite_turnover=composite_metrics.turnover,
                        turnover_delta=_delta(base.turnover, composite_metrics.turnover),
                        base_portfolio_return=base.top_return,
                        composite_portfolio_return=composite_metrics.top_return,
                        portfolio_effect=_delta(base.top_return, composite_metrics.top_return),
                    )
                )

        dataset = self.datasets.get(first.dataset_id)
        if dataset is None:
            raise KeyError(first.dataset_id)
        relationship_id = f"factor-relationship-{secrets.token_hex(10)}"
        record = FactorRelationshipRecord(
            relationship_id=relationship_id,
            name=request.name,
            created_at=datetime.now(UTC),
            stage=request.stage,
            period=period,
            horizon=request.horizon,
            rolling_window=request.rolling_window,
            top_percent=request.top_percent,
            redundancy_threshold=request.redundancy_threshold,
            overlap_threshold=request.overlap_threshold,
            dataset_id=first.dataset_id,
            dataset_fingerprint=dataset.content_fingerprint,
            universe=first.universe,
            factor_research_ids=ids,
            factor_ids=tuple(item.factor.factor_id for item in records),
            factor_names=tuple(item.factor.name for item in records),
            factor_revisions=tuple(
                item.factor.source_fingerprint or item.factor.version for item in records
            ),
            value_correlations=tuple(cells["FACTOR_VALUES"]),
            rank_correlations=tuple(cells["FACTOR_RANKS"]),
            return_correlations=tuple(cells["FACTOR_RETURNS"]),
            rolling_correlations=rolling,
            redundancy=redundancy,
            exposure_overlap=tuple(overlaps[pair] for pair in distinct_pairs),
            incremental_information=tuple(incremental),
            clusters=self._clusters(ids, rank_cells, request.redundancy_threshold),
            correlation_methodology=(
                "FACTOR_VALUES correlates aligned raw observations; FACTOR_RANKS correlates "
                "aligned within-date cross-sectional percentile ranks; FACTOR_RETURNS correlates "
                "within-date direction-adjusted Q5-minus-Q1 forward-return series. Pearson and "
                "Spearman "
                "are reported separately. Forward-return endpoints must remain inside the stage."
            ),
            incremental_disclosure=(
                "Incremental information uses an equal-weight Rank Average rule: 0.5 × base "
                "direction-adjusted percentile rank + 0.5 × added direction-adjusted percentile "
                "rank. Deltas are associations, not causal improvement claims or optimization."
            ),
            crowding_disclosure=(
                "Overlap measures concentration inside the selected VQD research portfolios only. "
                "It is not evidence that a factor is crowded in the market because VQD does not "
                "have all-market fund holdings."
            ),
        )
        self.ledger.save(
            ResearchLedgerEntry.new(
                entry_id=f"ledger-{secrets.token_hex(10)}",
                kind="FACTOR_RELATIONSHIP",
                artifact_id=relationship_id,
                revision=1,
                dataset_ids=(record.dataset_id,),
                dataset_fingerprints=(record.dataset_fingerprint,),
                factor_ids=record.factor_ids,
                factor_revisions=record.factor_revisions,
                known_evidence=(request.stage,),
                result_refs=(f"factor-relationship:{relationship_id}",),
                metadata={
                    "stage": request.stage,
                    "horizon": request.horizon,
                    "factor_count": len(records),
                    "rolling_window": request.rolling_window,
                    "top_percent": request.top_percent,
                    "redundancy_threshold": request.redundancy_threshold,
                    "overlap_threshold": request.overlap_threshold,
                },
                factor_relationship_id=relationship_id,
            )
        )
        return record
