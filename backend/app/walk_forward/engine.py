from __future__ import annotations

import calendar
import secrets
import statistics
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from app.datasets import DatasetRegistry
from app.diagnostics.metrics import daily_returns, max_drawdown, sharpe
from app.factors import FactorResearchEngine
from app.factors.models import HorizonEvaluation, ResearchPeriod, ResearchStage
from app.factors.repository import FactorResearchRepository
from app.research_ledger import ResearchLedgerEntry, ResearchLedgerRepository, research_ledger
from app.runs import RunLedger, run_ledger
from app.sdk.registry import StrategyRegistry
from app.trace.models import BacktestTrace

from .models import (
    CreateWalkForwardResearch,
    FactorWindowMetrics,
    FirstDegradation,
    MetricDistribution,
    StrategyWindowMetrics,
    WalkForwardResearchRecord,
    WalkForwardStability,
    WalkForwardWindowDefinition,
    WalkForwardWindowResult,
)


def _add_months(value: datetime, months: int) -> datetime:
    absolute = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(absolute, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _distribution(values: list[float]) -> MetricDistribution:
    if not values:
        return MetricDistribution(count=0, mean=None, std=None, minimum=None, maximum=None)
    return MetricDistribution(
        count=len(values),
        mean=statistics.fmean(values),
        std=statistics.pstdev(values) if len(values) > 1 else 0.0,
        minimum=min(values),
        maximum=max(values),
    )


class WalkForwardEngine:
    def __init__(
        self,
        datasets: DatasetRegistry,
        factor_repository: FactorResearchRepository,
        factor_engine: FactorResearchEngine,
        strategies: StrategyRegistry,
        runs: RunLedger | None = None,
        ledger: ResearchLedgerRepository | None = None,
    ) -> None:
        self.datasets = datasets
        self.factor_repository = factor_repository
        self.factor_engine = factor_engine
        self.strategies = strategies
        self.runs = runs or run_ledger
        self.ledger = ledger or research_ledger

    @staticmethod
    def _windows(
        start: datetime,
        end: datetime,
        request: CreateWalkForwardResearch,
    ) -> tuple[WalkForwardWindowDefinition, ...]:
        result: list[WalkForwardWindowDefinition] = []
        cursor = start
        while True:
            research_end = _add_months(cursor, request.config.research_months) - timedelta(
                microseconds=1
            )
            validation_start = research_end + timedelta(microseconds=1)
            validation_end = _add_months(
                validation_start, request.config.validation_months
            ) - timedelta(microseconds=1)
            forward_start = validation_end + timedelta(microseconds=1)
            forward_end = _add_months(forward_start, request.config.forward_months) - timedelta(
                microseconds=1
            )
            if forward_end > end:
                break
            result.append(
                WalkForwardWindowDefinition(
                    index=len(result) + 1,
                    research=ResearchPeriod(start=cursor, end=research_end),
                    validation=ResearchPeriod(start=validation_start, end=validation_end),
                    forward=ResearchPeriod(start=forward_start, end=forward_end),
                )
            )
            cursor = _add_months(cursor, request.config.step_months)
        if not result:
            raise ValueError("The selected data range is too short for one Walk-Forward window")
        return tuple(result)

    @staticmethod
    def _factor_metrics(evaluation: HorizonEvaluation) -> FactorWindowMetrics:
        return FactorWindowMetrics(
            observation_count=evaluation.observation_count,
            cross_section_count=evaluation.cross_section_count,
            ic=evaluation.ic,
            rank_ic=evaluation.rank_ic,
            quantile_returns=evaluation.quantile_returns,
            spread=evaluation.long_short_spread,
            coverage=evaluation.coverage,
            turnover=evaluation.turnover,
            monotonic=evaluation.monotonic,
        )

    @staticmethod
    def _ledger_factor_result(
        index: int, metrics: FactorWindowMetrics
    ) -> dict[str, str | int | float | bool | None]:
        return {
            "window_index": index,
            "observation_count": metrics.observation_count,
            "cross_section_count": metrics.cross_section_count,
            "ic": metrics.ic,
            "rank_ic": metrics.rank_ic,
            **{
                f"q{quantile + 1}": value for quantile, value in enumerate(metrics.quantile_returns)
            },
            "spread": metrics.spread,
            "coverage": metrics.coverage,
            "turnover": metrics.turnover,
            "monotonic": metrics.monotonic,
        }

    @staticmethod
    def _ledger_strategy_result(
        index: int, metrics: StrategyWindowMetrics
    ) -> dict[str, str | int | float | bool | None]:
        return {"window_index": index, **metrics.model_dump()}

    @staticmethod
    def _strategy_metrics(
        trace: BacktestTrace, period: ResearchPeriod, initial_cash: float
    ) -> StrategyWindowMetrics:
        before = [item for item in trace.timeline if item.timestamp < period.start]
        events = [item for item in trace.timeline if period.start <= item.timestamp <= period.end]
        baseline = before[-1].pnl_snapshot.equity if before else initial_cash
        if not events:
            return StrategyWindowMetrics(
                total_return=0.0,
                sharpe=0.0,
                max_drawdown=0.0,
                trades=0,
                fees=0.0,
                slippage=0.0,
                net_costs=0.0,
            )
        equity = tuple(item.pnl_snapshot.equity for item in events)
        fees = sum(item.cost_snapshot.fees for item in events)
        slippage = sum(item.cost_snapshot.slippage for item in events)
        return StrategyWindowMetrics(
            total_return=equity[-1] / baseline - 1 if baseline else 0.0,
            sharpe=sharpe(daily_returns(equity, baseline)),
            max_drawdown=max_drawdown(equity, baseline),
            trades=sum(len(item.execution_events) for item in events),
            fees=fees,
            slippage=slippage,
            net_costs=fees + slippage,
        )

    @staticmethod
    def _stability(windows: tuple[WalkForwardWindowResult, ...]) -> WalkForwardStability:
        rank_ics = [item.forward.rank_ic for item in windows if item.forward.rank_ic is not None]
        ics = [item.forward.ic for item in windows if item.forward.ic is not None]
        signs = [1 if value > 0 else -1 if value < 0 else 0 for value in ics]
        dominant = max((-1, 0, 1), key=lambda sign: signs.count(sign)) if signs else 0
        turnovers = [item.forward.turnover for item in windows if item.forward.turnover is not None]
        turnover_std = statistics.pstdev(turnovers) if len(turnovers) > 1 else 0.0
        strategy_returns = [
            item.forward_strategy.total_return
            for item in windows
            if item.forward_strategy is not None
        ]
        return WalkForwardStability(
            positive_ic_window_ratio=(sum(value > 0 for value in ics) / len(ics) if ics else 0.0),
            rank_ic_distribution=_distribution(rank_ics),
            factor_sign_consistency=(
                sum(sign == dominant for sign in signs) / len(signs) if signs else 0.0
            ),
            quantile_monotonicity_stability=(
                sum(item.forward.monotonic for item in windows) / len(windows)
            ),
            turnover_stability=1.0 / (1.0 + turnover_std),
            strategy_return_distribution=(
                _distribution(strategy_returns) if strategy_returns else None
            ),
        )

    @staticmethod
    def _first_degradation(
        windows: tuple[WalkForwardWindowResult, ...],
        *,
        factor_research_id: str,
        dataset_id: str,
        strategy_id: str | None,
        run_id: str | None,
        trace_id: str | None,
    ) -> FirstDegradation | None:
        previous: WalkForwardWindowResult | None = None
        for window in windows:
            reasons: list[str] = []
            if window.forward.rank_ic is not None and window.forward.rank_ic < 0:
                reasons.append("FORWARD_RANK_IC_NEGATIVE")
            if previous is not None and previous.forward.monotonic and not window.forward.monotonic:
                reasons.append("QUANTILE_MONOTONICITY_DISAPPEARED")
            if (
                previous is not None
                and previous.forward_strategy is not None
                and window.forward_strategy is not None
                and window.forward_strategy.max_drawdown
                < previous.forward_strategy.max_drawdown - 0.05
                and abs(window.forward_strategy.max_drawdown)
                >= max(abs(previous.forward_strategy.max_drawdown) * 1.5, 0.05)
            ):
                reasons.append("MAX_DRAWDOWN_EXPANDED")
            if (
                previous is not None
                and previous.forward.turnover is not None
                and window.forward.turnover is not None
                and window.forward.turnover - previous.forward.turnover >= 0.10
                and window.forward.turnover > previous.forward.turnover * 1.5
            ):
                reasons.append("FACTOR_TURNOVER_WORSENED")
            if reasons:
                timestamp = window.definition.forward.start
                shared = {
                    "window": window.definition.index,
                    "as_of": timestamp.isoformat(),
                    "factor_research_id": factor_research_id,
                    "strategy_id": strategy_id or "",
                    "run_id": run_id or "",
                }
                return FirstDegradation(
                    window_index=window.definition.index,
                    timestamp=timestamp,
                    reasons=tuple(reasons),
                    factor_research_id=factor_research_id,
                    strategy_id=strategy_id,
                    run_id=run_id,
                    historical_market_path=(
                        "/historical-market?" + urlencode({"dataset_id": dataset_id, **shared})
                    ),
                    factor_lab_path=f"/factor-lab?{urlencode(shared)}",
                    replay_path=(
                        None
                        if run_id is None or trace_id is None
                        else f"/runs/{run_id}/replay?{urlencode(shared)}"
                    ),
                )
            previous = window
        return None

    def create(self, request: CreateWalkForwardResearch) -> WalkForwardResearchRecord:
        factor = self.factor_repository.get(request.factor_research_id)
        if factor is None:
            raise KeyError(f"Factor research '{request.factor_research_id}' was not found")
        dataset = self.datasets.get(factor.dataset_id)
        if dataset is None:
            raise KeyError(f"Dataset '{factor.dataset_id}' was not found")
        if dataset.content_fingerprint != factor.dataset_revision:
            raise ValueError("Factor research dataset fingerprint no longer matches")
        start = request.config.start or dataset.start_time
        end = request.config.end or dataset.end_time
        if start < dataset.start_time or end > dataset.end_time:
            raise ValueError("Walk-Forward range must stay inside the saved dataset revision")
        definitions = self._windows(start, end, request)
        stages: tuple[ResearchStage, ...] = ("RESEARCH", "VALIDATION", "HOLDOUT")
        evaluation_requests = tuple(
            (stage, period)
            for definition in definitions
            for stage, period in zip(
                stages,
                (definition.research, definition.validation, definition.forward),
                strict=True,
            )
        )
        evaluations = self.factor_engine.evaluate_periods(factor, evaluation_requests)

        run_id: str | None = None
        trace_id: str | None = None
        strategy_revision: str | None = None
        trace: BacktestTrace | None = None
        if request.strategy_id is not None:
            parameters = {
                **request.strategy_parameters,
                "initial_cash": request.initial_cash,
                "fee_bps": request.fee_bps,
                "slippage_bps": request.slippage_bps,
            }
            run = self.runs.create(
                strategy_id=request.strategy_id,
                dataset_id=factor.dataset_id,
                parameters=parameters,
                research_cutoff=None,
                strategy_registry_override=self.strategies,
                dataset_registry_override=self.datasets,
            )
            if run.trace is None or run.manifest.trace_id is None:
                raise ValueError("The native strategy run did not produce a replayable trace")
            run_id = run.manifest.run_id
            trace_id = run.manifest.trace_id
            strategy_revision = run.manifest.strategy.source_fingerprint
            trace = run.trace

        windows: list[WalkForwardWindowResult] = []
        for index, definition in enumerate(definitions):
            offset = index * 3
            selected = [
                next(item for item in evaluation.horizons if item.horizon == request.horizon)
                for evaluation in evaluations[offset : offset + 3]
            ]
            windows.append(
                WalkForwardWindowResult(
                    definition=definition,
                    research=self._factor_metrics(selected[0]),
                    validation=self._factor_metrics(selected[1]),
                    forward=self._factor_metrics(selected[2]),
                    forward_strategy=(
                        None
                        if trace is None
                        else self._strategy_metrics(trace, definition.forward, request.initial_cash)
                    ),
                )
            )
        window_results = tuple(windows)
        walk_forward_id = f"walk-forward-{secrets.token_hex(10)}"
        record = WalkForwardResearchRecord(
            walk_forward_id=walk_forward_id,
            name=request.name,
            created_at=datetime.now(UTC),
            factor_research_id=factor.research_id,
            factor_id=factor.factor.factor_id,
            factor_revision=factor.factor.source_fingerprint or factor.factor.version,
            strategy_id=request.strategy_id,
            strategy_revision=strategy_revision,
            dataset_id=dataset.dataset_id,
            dataset_fingerprint=dataset.content_fingerprint,
            config=request.config,
            horizon=request.horizon,
            initial_cash=request.initial_cash,
            fee_bps=request.fee_bps,
            slippage_bps=request.slippage_bps,
            windows=window_results,
            stability=self._stability(window_results),
            first_degradation=self._first_degradation(
                window_results,
                factor_research_id=factor.research_id,
                dataset_id=dataset.dataset_id,
                strategy_id=request.strategy_id,
                run_id=run_id,
                trace_id=trace_id,
            ),
            run_id=run_id,
            trace_id=trace_id,
        )
        self.ledger.save(
            ResearchLedgerEntry.new(
                entry_id=f"ledger-{secrets.token_hex(10)}",
                kind="WALK_FORWARD",
                artifact_id=walk_forward_id,
                revision=1,
                dataset_ids=(dataset.dataset_id,),
                dataset_fingerprints=(dataset.content_fingerprint,),
                factor_ids=(factor.factor.factor_id,),
                factor_revisions=(record.factor_revision,),
                strategy_id=request.strategy_id,
                strategy_revision=strategy_revision,
                known_evidence=("RESEARCH", "VALIDATION", "FORWARD"),
                result_refs=tuple(
                    [f"walk-forward:{walk_forward_id}"]
                    + ([] if run_id is None else [f"run:{run_id}"])
                ),
                metadata={
                    "horizon": request.horizon,
                    "window_count": len(window_results),
                    "research_months": request.config.research_months,
                    "validation_months": request.config.validation_months,
                    "forward_months": request.config.forward_months,
                    "step_months": request.config.step_months,
                },
                walk_forward_id=walk_forward_id,
                window_definitions=tuple(
                    {
                        "index": item.index,
                        "research_start": item.research.start.isoformat(),
                        "research_end": item.research.end.isoformat(),
                        "validation_start": item.validation.start.isoformat(),
                        "validation_end": item.validation.end.isoformat(),
                        "forward_start": item.forward.start.isoformat(),
                        "forward_end": item.forward.end.isoformat(),
                    }
                    for item in definitions
                ),
                research_results=tuple(
                    self._ledger_factor_result(item.definition.index, item.research)
                    for item in window_results
                ),
                validation_results=tuple(
                    self._ledger_factor_result(item.definition.index, item.validation)
                    for item in window_results
                ),
                forward_results=tuple(
                    self._ledger_factor_result(item.definition.index, item.forward)
                    for item in window_results
                ),
                strategy_results=tuple(
                    self._ledger_strategy_result(
                        item.definition.index,
                        item.forward_strategy,
                    )
                    for item in window_results
                    if item.forward_strategy is not None
                ),
            )
        )
        return record
