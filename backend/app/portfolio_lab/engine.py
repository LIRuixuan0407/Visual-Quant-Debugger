from __future__ import annotations

import math
import secrets
import statistics
from datetime import UTC, datetime

import numpy as np

from app.datasets import DatasetRegistry
from app.diagnostics.volatility import annualization_factor_for_frequency
from app.execution import ExecutionEngine
from app.factors import FactorResearchEngine
from app.factors.models import (
    FactorObservation,
    FactorResearchRecord,
    ResearchPeriod,
    ResearchStage,
)
from app.factors.repository import FactorResearchRepository
from app.portfolio import Portfolio
from app.research_ledger import ResearchLedgerEntry, ResearchLedgerRepository, research_ledger

from .models import (
    AssetRiskContribution,
    CreatePortfolioResearch,
    FactorScoreEvidence,
    PortfolioFactorCheck,
    PortfolioPositionLineage,
    PortfolioRebalanceSnapshot,
    PortfolioResearchRecord,
    PortfolioRiskDecomposition,
    PortfolioStageResult,
    RiskMatrix,
    RiskVolatilityBasis,
    TransactionCostPreview,
)

_STAGE_ORDER: dict[ResearchStage, int] = {"RESEARCH": 0, "VALIDATION": 1, "HOLDOUT": 2}


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _pstdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def _rebalance_dates(timestamps: list[datetime], rule: str) -> set[datetime]:
    result: set[datetime] = set()
    previous: tuple[int, ...] | None = None
    for timestamp in sorted(set(timestamps)):
        key: tuple[int, ...]
        if rule == "DAILY":
            key = (timestamp.year, timestamp.month, timestamp.day)
        elif rule == "WEEKLY":
            year, week, _ = timestamp.isocalendar()
            key = (year, week)
        else:
            key = (timestamp.year, timestamp.month)
        if key != previous:
            result.add(timestamp)
            previous = key
    return result


def _cap_weights(raw: dict[str, float], cap: float) -> dict[str, float]:
    if not raw:
        return {}
    total = sum(max(value, 0.0) for value in raw.values())
    weights = (
        {symbol: max(value, 0.0) / total for symbol, value in raw.items()}
        if total
        else {symbol: 1 / len(raw) for symbol in raw}
    )
    active = set(weights)
    fixed: dict[str, float] = {}
    remaining = 1.0
    while active:
        active_total = sum(weights[symbol] for symbol in active)
        if active_total <= 0:
            break
        changed = False
        for symbol in tuple(active):
            proposed = remaining * weights[symbol] / active_total
            if proposed > cap + 1e-12:
                fixed[symbol] = cap
                remaining -= cap
                active.remove(symbol)
                changed = True
        if not changed:
            for symbol in active:
                fixed[symbol] = remaining * weights[symbol] / active_total
            break
    return fixed


class PortfolioResearchEngine:
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

    def _records(self, request: CreatePortfolioResearch) -> tuple[FactorResearchRecord, ...]:
        records: list[FactorResearchRecord] = []
        for reference in request.factors:
            record = self.factor_repository.get(reference.research_id)
            if record is None:
                raise KeyError(f"Factor research '{reference.research_id}' was not found")
            records.append(record)
        first = records[0]
        for record in records[1:]:
            if record.dataset_id != first.dataset_id:
                raise ValueError("Portfolio factors must use the same market dataset")
            if record.universe != first.universe:
                raise ValueError("Portfolio factors must use the same research universe")
            if record.periods != first.periods:
                raise ValueError("Portfolio factors must share Research/Validation/Holdout periods")
        fundamental_ids = {
            record.fundamental_dataset_id
            for record in records
            if record.factor.data_source in {"FUNDAMENTAL", "MIXED"}
            and record.fundamental_dataset_id is not None
        }
        if len(fundamental_ids) > 1:
            raise ValueError("Fundamental factor inputs must use the same point-in-time dataset")
        universe = set(first.universe)
        requested_symbols = set(request.filters.include_symbols) | set(
            request.filters.exclude_symbols
        )
        unknown = sorted(requested_symbols - universe)
        if unknown:
            raise ValueError(
                "Portfolio symbol filters reference symbols outside the factor universe: "
                + ", ".join(unknown)
            )
        return tuple(records)

    @staticmethod
    def _period(record: FactorResearchRecord, stage: ResearchStage) -> ResearchPeriod:
        if stage == "RESEARCH":
            return record.periods.research
        if stage == "VALIDATION":
            return record.periods.validation
        return record.periods.holdout

    @staticmethod
    def _ensure_revealed(records: tuple[FactorResearchRecord, ...], stage: ResearchStage) -> None:
        if stage == "RESEARCH":
            return
        blocked = [
            record.research_id
            for record in records
            if _STAGE_ORDER[record.revealed_stage] < _STAGE_ORDER[stage]
        ]
        if blocked:
            raise ValueError(f"{stage} is still sealed for factor research: {', '.join(blocked)}")

    def _observations(
        self, records: tuple[FactorResearchRecord, ...]
    ) -> tuple[dict[tuple[datetime, str], FactorObservation], ...]:
        return tuple(
            {
                (item.timestamp, item.symbol): item
                for item in self.factor_engine.observations(record)
            }
            for record in records
        )

    def _market_metrics(
        self, dataset_id: str, universe: tuple[str, ...]
    ) -> dict[tuple[datetime, str], tuple[float | None, float | None]]:
        frames = self.datasets.load_frames(dataset_id, universe)
        result: dict[tuple[datetime, str], tuple[float | None, float | None]] = {}
        for index, frame in enumerate(frames):
            start = max(0, index - 19)
            history = frames[start : index + 1]
            for symbol in universe:
                if symbol not in frame.values:
                    continue
                dollars = [
                    item.value(symbol, "close") * item.value(symbol, "volume")
                    for item in history
                    if "volume" in item.values[symbol]
                ]
                closes = [item.value(symbol, "close") for item in history]
                returns = [
                    current / previous - 1
                    for previous, current in zip(closes, closes[1:], strict=False)
                    if previous > 0
                ]
                result[(frame.timestamp, symbol)] = (
                    _mean(dollars) if dollars else None,
                    _pstdev(returns) if len(returns) >= 2 else None,
                )
        return result

    def _factor_checks(
        self,
        *,
        request: CreatePortfolioResearch,
        records: tuple[FactorResearchRecord, ...],
        observation_maps: tuple[dict[tuple[datetime, str], FactorObservation], ...],
        timestamps: tuple[datetime, ...],
    ) -> tuple[PortfolioFactorCheck, ...]:
        included = set(request.filters.include_symbols)
        excluded = set(request.filters.exclude_symbols)
        universe = tuple(
            symbol
            for symbol in records[0].universe
            if (not included or symbol in included) and symbol not in excluded
        )
        expected = len(timestamps) * len(universe)
        checks: list[PortfolioFactorCheck] = []
        for record, observations, reference in zip(
            records, observation_maps, request.factors, strict=True
        ):
            available = sum(
                1
                for timestamp in timestamps
                for symbol in universe
                if (item := observations.get((timestamp, symbol))) is not None
                and item.available_at <= timestamp
            )
            weight = (
                reference.weight
                if request.combination == "USER_DEFINED_WEIGHT"
                else 1.0 / len(records)
            )
            checks.append(
                PortfolioFactorCheck(
                    research_id=record.research_id,
                    factor_id=record.factor.factor_id,
                    factor_name=record.factor.name,
                    origin=record.factor.origin,
                    category=record.factor.category,
                    data_source=record.factor.data_source,
                    direction=reference.direction_override or record.factor.direction,
                    effective_weight=weight,
                    available_observations=available,
                    expected_observations=expected,
                    missing_observations=max(expected - available, 0),
                    coverage=available / expected if expected else 0.0,
                )
            )
        return tuple(checks)

    def _snapshot(
        self,
        *,
        timestamp: datetime,
        stage: ResearchStage,
        request: CreatePortfolioResearch,
        records: tuple[FactorResearchRecord, ...],
        observation_maps: tuple[dict[tuple[datetime, str], FactorObservation], ...],
        market_metrics: dict[tuple[datetime, str], tuple[float | None, float | None]],
    ) -> PortfolioRebalanceSnapshot:
        universe = records[0].universe
        included = {symbol.upper() for symbol in request.filters.include_symbols}
        excluded = {symbol.upper() for symbol in request.filters.exclude_symbols}
        candidates = [
            symbol
            for symbol in universe
            if (not included or symbol in included) and symbol not in excluded
        ]
        factor_raw: list[dict[str, float]] = []
        for record, observations, reference in zip(
            records, observation_maps, request.factors, strict=True
        ):
            direction = reference.direction_override or record.factor.direction
            values: dict[str, float] = {}
            for symbol in candidates:
                item = observations.get((timestamp, symbol))
                if item is not None and item.available_at <= timestamp:
                    values[symbol] = item.value if direction == "HIGH" else -item.value
            factor_raw.append(values)
        all_available = [
            symbol for symbol in candidates if all(symbol in values for values in factor_raw)
        ]
        if request.filters.require_factor_availability:
            candidates = all_available
        passed: list[str] = []
        statuses: dict[str, list[str]] = {}
        for symbol in candidates:
            liquidity, volatility = market_metrics.get((timestamp, symbol), (None, None))
            checks: list[str] = []
            valid = True
            if request.filters.minimum_liquidity is not None:
                ok = liquidity is not None and liquidity >= request.filters.minimum_liquidity
                checks.append("LIQUIDITY PASS" if ok else "LIQUIDITY FAIL")
                valid = valid and ok
            if request.filters.maximum_volatility is not None:
                ok = volatility is not None and volatility <= request.filters.maximum_volatility
                checks.append("VOLATILITY PASS" if ok else "VOLATILITY FAIL")
                valid = valid and ok
            if request.filters.require_factor_availability:
                checks.append("FACTOR AVAILABILITY PASS")
            elif not all(symbol in values for values in factor_raw):
                checks.append("PARTIAL FACTOR AVAILABILITY")
            if valid:
                passed.append(symbol)
            statuses[symbol] = checks or ["EXPLICIT FILTERS PASS"]
        evidences: dict[str, list[FactorScoreEvidence]] = {symbol: [] for symbol in passed}
        weighted_scores: dict[str, float] = {symbol: 0.0 for symbol in passed}
        available_weights: dict[str, float] = {symbol: 0.0 for symbol in passed}
        for factor_index, (record, values, reference) in enumerate(
            zip(records, factor_raw, request.factors, strict=True)
        ):
            available = {symbol: values[symbol] for symbol in passed if symbol in values}
            ordered = sorted(available, key=lambda symbol: (-available[symbol], symbol))
            ranks = {symbol: rank for rank, symbol in enumerate(ordered, start=1)}
            raw_values = list(available.values())
            minimum = min(raw_values) if raw_values else 0.0
            maximum = max(raw_values) if raw_values else 0.0
            mean = _mean(raw_values)
            deviation = _pstdev(raw_values)
            factor_weight = (
                reference.weight
                if request.combination == "USER_DEFINED_WEIGHT"
                else 1.0 / len(records)
            )
            direction = reference.direction_override or record.factor.direction
            for symbol in passed:
                if symbol not in available:
                    evidences[symbol].append(
                        FactorScoreEvidence(
                            research_id=record.research_id,
                            factor_id=record.factor.factor_id,
                            factor_name=record.factor.name,
                            direction=direction,
                            available=False,
                            raw_value=None,
                            rank=None,
                            universe_count=len(ordered),
                            normalized_score=None,
                            contribution=0.0,
                        )
                    )
                    continue
                rank = ranks[symbol]
                percentile = 1.0 if len(ordered) == 1 else 1 - (rank - 1) / (len(ordered) - 1)
                minmax = (
                    0.5
                    if math.isclose(maximum, minimum)
                    else (available[symbol] - minimum) / (maximum - minimum)
                )
                zscore = 0.0 if deviation < 1e-12 else (available[symbol] - mean) / deviation
                if request.combination == "RANK_AVERAGE":
                    normalized = percentile
                elif request.combination == "Z_SCORE_COMPOSITE":
                    normalized = zscore
                else:
                    normalized = minmax
                contribution = normalized * factor_weight
                weighted_scores[symbol] += contribution
                available_weights[symbol] += factor_weight
                evidences[symbol].append(
                    FactorScoreEvidence(
                        research_id=record.research_id,
                        factor_id=record.factor.factor_id,
                        factor_name=record.factor.name,
                        direction=direction,
                        available=True,
                        raw_value=observation_maps[factor_index][(timestamp, symbol)].value,
                        rank=rank,
                        universe_count=len(ordered),
                        normalized_score=normalized,
                        contribution=contribution,
                    )
                )
        composite = {
            symbol: weighted_scores[symbol] / available_weights[symbol]
            for symbol in passed
            if available_weights[symbol] > 0
        }
        passed = [symbol for symbol in passed if symbol in composite]
        ranked = sorted(passed, key=lambda symbol: (-composite[symbol], symbol))
        if request.construction.selection == "TOP_N":
            count = min(request.construction.top_n, len(ranked))
        else:
            count = (
                max(1, math.ceil(len(ranked) * request.construction.top_percent / 100))
                if ranked
                else 0
            )
        selected = ranked[:count]
        if request.construction.weighting == "SCORE_WEIGHTED":
            if selected:
                floor = min(composite[symbol] for symbol in selected)
                raw_weights = {
                    symbol: max(composite[symbol] - floor, 0.0) + 1e-9 for symbol in selected
                }
            else:
                raw_weights = {}
        else:
            raw_weights = {symbol: 1.0 for symbol in selected}
        weights = _cap_weights(raw_weights, request.construction.max_single_position_weight)
        rank_map = {symbol: rank for rank, symbol in enumerate(ranked, start=1)}
        rows: list[PortfolioPositionLineage] = []
        for symbol in ranked:
            liquidity, volatility = market_metrics.get((timestamp, symbol), (None, None))
            rows.append(
                PortfolioPositionLineage(
                    symbol=symbol,
                    selected=symbol in weights,
                    liquidity=liquidity,
                    volatility=volatility,
                    filter_status=tuple(statuses[symbol]),
                    factors=tuple(evidences[symbol]),
                    composite_score=composite[symbol],
                    portfolio_rank=rank_map[symbol],
                    target_weight=weights.get(symbol, 0.0),
                )
            )
        return PortfolioRebalanceSnapshot(
            timestamp=timestamp,
            stage=stage,
            eligible_count=len(ranked),
            selected_symbols=tuple(selected),
            positions=tuple(rows),
        )

    def _cost_preview(
        self,
        request: CreatePortfolioResearch,
        dataset_id: str,
        universe: tuple[str, ...],
        snapshots: tuple[PortfolioRebalanceSnapshot, ...],
        period: ResearchPeriod,
    ) -> TransactionCostPreview:
        frames = tuple(
            frame
            for frame in self.datasets.load_frames(dataset_id, universe)
            if period.start <= frame.timestamp <= period.end
        )
        if not frames:
            return TransactionCostPreview(
                gross_return=0.0,
                fees=0.0,
                slippage=0.0,
                net_return=0.0,
                turnover=0.0,
                max_drawdown=0.0,
                positions=0,
                rebalance_count=0,
            )
        by_signal = {snapshot.timestamp: snapshot for snapshot in snapshots}
        portfolio = Portfolio(cash=request.initial_cash)
        execution = ExecutionEngine(fee_bps=request.fee_bps, slippage_bps=request.slippage_bps)
        pending: PortfolioRebalanceSnapshot | None = None
        equity_curve: list[float] = []
        traded_notional = 0.0
        for frame in frames:
            prices = {
                symbol: frame.value(symbol, "close")
                for symbol in universe
                if symbol in frame.values
            }
            if pending is not None:
                target_positions = {
                    item.symbol: item.target_weight * request.gross_notional / prices[item.symbol]
                    for item in pending.positions
                    if item.selected and item.symbol in prices and prices[item.symbol] > 0
                }
                target_positions.update(
                    {
                        symbol: 0.0
                        for symbol in portfolio.positions
                        if symbol not in target_positions
                    }
                )
                orders = execution.create_target_orders(
                    current_positions=portfolio.positions,
                    target_positions=target_positions,
                    submitted_at=pending.timestamp,
                    source_signal_id=f"portfolio-preview-{pending.timestamp.isoformat()}",
                    target_state=1 if target_positions else 0,
                )
                fills = execution.execute_at_prices(
                    orders, prices=prices, executed_at=frame.timestamp
                )
                traded_notional += sum(item.traded_notional for item in fills)
                portfolio.apply(fills)
                pending = None
            if frame.timestamp in by_signal:
                pending = by_signal[frame.timestamp]
            equity_curve.append(portfolio.mark_prices(prices).equity)
        final = portfolio.mark_prices(
            {symbol: frames[-1].value(symbol, "close") for symbol in universe}
        )
        net_pnl = final.equity - request.initial_cash
        gross_pnl = net_pnl + final.cumulative_fees + final.cumulative_slippage
        peak = request.initial_cash
        drawdown = 0.0
        for equity in equity_curve:
            peak = max(peak, equity)
            drawdown = min(drawdown, equity / peak - 1 if peak else 0.0)
        average_equity = _mean(equity_curve) or request.initial_cash
        return TransactionCostPreview(
            gross_return=gross_pnl / request.initial_cash,
            fees=final.cumulative_fees,
            slippage=final.cumulative_slippage,
            net_return=net_pnl / request.initial_cash,
            turnover=traded_notional / average_equity if average_equity > 0 else 0.0,
            max_drawdown=drawdown,
            positions=sum(1 for value in portfolio.positions.values() if abs(value) > 1e-12),
            rebalance_count=len(snapshots),
        )

    def _risk_decomposition(
        self,
        *,
        dataset_id: str,
        universe: tuple[str, ...],
        snapshots: tuple[PortfolioRebalanceSnapshot, ...],
        period: ResearchPeriod,
        dataset_frequency: str,
    ) -> PortfolioRiskDecomposition:
        latest = snapshots[-1] if snapshots else None
        positions = (
            tuple(item for item in latest.positions if item.selected and item.target_weight > 0.0)
            if latest is not None
            else ()
        )
        symbols = tuple(item.symbol for item in positions)
        annualization_factor = annualization_factor_for_frequency(dataset_frequency)
        volatility_basis: RiskVolatilityBasis = (
            "ANNUALIZED" if annualization_factor is not None else "PER_OBSERVATION"
        )
        base_details = (
            "The latest revealed-stage target weights are held fixed over the stage's aligned "
            "close-to-close simple-return history; any unallocated weight is treated as zero-risk "
            "cash.",
            "Covariance is the sample covariance matrix (ddof=1) of aligned asset returns. "
            "Correlation divides covariance by the corresponding sample standard deviations.",
            "Portfolio variance is w'Σw. Marginal volatility contribution is (Σw)i / sqrt(w'Σw); "
            "component contribution is wi times marginal contribution, and component shares sum "
            "to one when volatility is non-zero.",
            "Historical 95% VaR is max(0, the linear 95th percentile of one-observation losses). "
            "Expected Shortfall is max(0, the mean loss at or beyond that unrounded percentile).",
        )
        if latest is None or not positions:
            return PortfolioRiskDecomposition(
                status="INSUFFICIENT_DATA",
                verdict="INSUFFICIENT_RISK_HISTORY",
                snapshot_timestamp=latest.timestamp if latest is not None else None,
                dataset_frequency=dataset_frequency,
                observations=0,
                annualization_factor=annualization_factor,
                volatility_basis=volatility_basis,
                portfolio_volatility=None,
                per_observation_volatility=None,
                historical_var_95=None,
                expected_shortfall_95=None,
                covariance=None,
                correlation=None,
                contributions=(),
                calculation_details=base_details,
                boundary_disclosure=(
                    "Risk decomposition is historical diagnostic evidence only. It is not a "
                    "forecast, optimizer, position-sizing instruction, or trading recommendation."
                ),
            )

        frames = tuple(
            frame
            for frame in self.datasets.load_frames(dataset_id, universe)
            if period.start <= frame.timestamp <= period.end
        )
        aligned_returns: list[list[float]] = []
        for previous, current in zip(frames, frames[1:], strict=False):
            if any(
                symbol not in previous.values
                or symbol not in current.values
                or previous.value(symbol, "close") <= 0.0
                for symbol in symbols
            ):
                continue
            aligned_returns.append(
                [
                    current.value(symbol, "close") / previous.value(symbol, "close") - 1.0
                    for symbol in symbols
                ]
            )
        observation_count = len(aligned_returns)
        if observation_count < 20:
            return PortfolioRiskDecomposition(
                status="INSUFFICIENT_DATA",
                verdict="INSUFFICIENT_RISK_HISTORY",
                snapshot_timestamp=latest.timestamp,
                dataset_frequency=dataset_frequency,
                observations=observation_count,
                annualization_factor=annualization_factor,
                volatility_basis=volatility_basis,
                portfolio_volatility=None,
                per_observation_volatility=None,
                historical_var_95=None,
                expected_shortfall_95=None,
                covariance=None,
                correlation=None,
                contributions=(),
                calculation_details=(
                    *base_details,
                    "At least 20 aligned return observations are required for this VQD risk "
                    "decomposition.",
                ),
                boundary_disclosure=(
                    "Risk decomposition is historical diagnostic evidence only. It is not a "
                    "forecast, optimizer, position-sizing instruction, or trading recommendation."
                ),
            )

        returns = np.asarray(aligned_returns, dtype=np.float64)
        weights = np.asarray([item.target_weight for item in positions], dtype=np.float64)
        covariance = np.atleast_2d(np.cov(returns, rowvar=False, ddof=1))
        standard_deviations = np.sqrt(np.maximum(np.diag(covariance), 0.0))
        denominator = np.outer(standard_deviations, standard_deviations)
        correlation = np.divide(
            covariance,
            denominator,
            out=np.zeros_like(covariance),
            where=denominator > 1e-18,
        )
        np.fill_diagonal(correlation, np.where(standard_deviations > 1e-12, 1.0, 0.0))
        period_variance = max(float(weights @ covariance @ weights), 0.0)
        period_volatility = math.sqrt(period_variance)
        scale = math.sqrt(annualization_factor) if annualization_factor is not None else 1.0
        portfolio_volatility = period_volatility * scale
        invested_total = float(np.sum(weights))
        invested_weights = weights / invested_total if invested_total > 0.0 else weights
        median_weight = float(np.median(invested_weights))
        covariance_times_weight = covariance @ weights
        contributions: list[AssetRiskContribution] = []
        for index, symbol in enumerate(symbols):
            marginal = (
                float(covariance_times_weight[index] / period_volatility * scale)
                if period_volatility > 1e-15
                else 0.0
            )
            component = float(weights[index] * marginal)
            risk_share = component / portfolio_volatility if portfolio_volatility > 1e-15 else 0.0
            gap = risk_share - float(invested_weights[index])
            contributions.append(
                AssetRiskContribution(
                    symbol=symbol,
                    portfolio_weight=float(weights[index]),
                    invested_weight=float(invested_weights[index]),
                    marginal_contribution_to_volatility=marginal,
                    component_contribution_to_volatility=component,
                    component_risk_share=risk_share,
                    risk_weight_gap=gap,
                    low_weight_high_risk=(
                        invested_weights[index] <= median_weight + 1e-12 and gap >= 0.05
                    ),
                )
            )
        if period_volatility <= 1e-15:
            verdict = "NO_VARIABLE_RISK_OBSERVED"
        elif any(item.low_weight_high_risk for item in contributions):
            verdict = "LOW_WEIGHT_HIGH_RISK"
        elif any(abs(item.risk_weight_gap) >= 0.05 for item in contributions):
            verdict = "RISK_CONCENTRATED_BEYOND_WEIGHT"
        else:
            verdict = "RISK_BROADLY_ALIGNED_WITH_WEIGHT"

        portfolio_returns = returns @ weights
        losses = -portfolio_returns
        raw_var = float(np.quantile(losses, 0.95, method="linear"))
        tail_losses = losses[losses >= raw_var]
        historical_var = max(0.0, raw_var)
        expected_shortfall = max(
            0.0,
            float(np.mean(tail_losses)) if tail_losses.size else raw_var,
        )
        annualization_detail = (
            f"Dataset frequency '{dataset_frequency}' maps to {annualization_factor} "
            f"observations per year; volatility and absolute contributions use sqrt("
            f"{annualization_factor}) annualization."
            if annualization_factor is not None
            else f"Dataset frequency '{dataset_frequency}' has no reliable VQD annualization "
            "mapping; volatility and absolute contributions remain per observation."
        )
        return PortfolioRiskDecomposition(
            status="AVAILABLE",
            verdict=verdict,
            snapshot_timestamp=latest.timestamp,
            dataset_frequency=dataset_frequency,
            observations=observation_count,
            annualization_factor=annualization_factor,
            volatility_basis=volatility_basis,
            portfolio_volatility=portfolio_volatility,
            per_observation_volatility=period_volatility,
            historical_var_95=historical_var,
            expected_shortfall_95=expected_shortfall,
            covariance=RiskMatrix(
                symbols=symbols,
                values=tuple(tuple(float(value) for value in row) for row in covariance),
            ),
            correlation=RiskMatrix(
                symbols=symbols,
                values=tuple(tuple(float(value) for value in row) for row in correlation),
            ),
            contributions=tuple(contributions),
            calculation_details=(*base_details, annualization_detail),
            boundary_disclosure=(
                "Risk decomposition is historical diagnostic evidence only. It is not a "
                "forecast, optimizer, position-sizing instruction, or trading recommendation."
            ),
        )

    def _stage(
        self,
        request: CreatePortfolioResearch,
        records: tuple[FactorResearchRecord, ...],
        stage: ResearchStage,
    ) -> PortfolioStageResult:
        self._ensure_revealed(records, stage)
        period = self._period(records[0], stage)
        observation_maps = self._observations(records)
        timestamps = sorted(
            {
                timestamp
                for values in observation_maps
                for timestamp, _ in values
                if period.start <= timestamp <= period.end
            }
        )
        selected_dates = _rebalance_dates(timestamps, request.rebalance)
        metrics = self._market_metrics(records[0].dataset_id, records[0].universe)
        ordered_dates = tuple(sorted(selected_dates))
        factor_checks = self._factor_checks(
            request=request,
            records=records,
            observation_maps=observation_maps,
            timestamps=ordered_dates,
        )
        snapshots = tuple(
            self._snapshot(
                timestamp=timestamp,
                stage=stage,
                request=request,
                records=records,
                observation_maps=observation_maps,
                market_metrics=metrics,
            )
            for timestamp in ordered_dates
        )
        preview = self._cost_preview(
            request, records[0].dataset_id, records[0].universe, snapshots, period
        )
        dataset = self.datasets.get(records[0].dataset_id)
        if dataset is None:
            raise KeyError(records[0].dataset_id)
        risk_decomposition = self._risk_decomposition(
            dataset_id=records[0].dataset_id,
            universe=records[0].universe,
            snapshots=snapshots,
            period=period,
            dataset_frequency=dataset.frequency,
        )
        return PortfolioStageResult(
            stage=stage,
            period=period,
            factor_checks=factor_checks,
            snapshots=snapshots,
            cost_preview=preview,
            risk_decomposition=risk_decomposition,
        )

    def create(self, request: CreatePortfolioResearch) -> PortfolioResearchRecord:
        records = self._records(request)
        stage = self._stage(request, records, "RESEARCH")
        dataset = self.datasets.get(records[0].dataset_id)
        if dataset is None:
            raise KeyError(records[0].dataset_id)
        research_id = f"portfolio-research-{secrets.token_hex(10)}"
        record = PortfolioResearchRecord(
            portfolio_research_id=research_id,
            name=request.name,
            created_at=datetime.now(UTC),
            dataset_id=records[0].dataset_id,
            dataset_fingerprint=dataset.content_fingerprint,
            universe=records[0].universe,
            factor_refs=request.factors,
            factor_ids=tuple(item.factor.factor_id for item in records),
            factor_names=tuple(item.factor.name for item in records),
            combination=request.combination,
            filters=request.filters,
            construction=request.construction,
            rebalance=request.rebalance,
            gross_notional=request.gross_notional,
            initial_cash=request.initial_cash,
            fee_bps=request.fee_bps,
            slippage_bps=request.slippage_bps,
            stages=(stage,),
        )
        self.ledger.save(
            ResearchLedgerEntry.new(
                entry_id=f"ledger-{secrets.token_hex(10)}",
                kind="PORTFOLIO",
                artifact_id=research_id,
                revision=1,
                dataset_ids=(record.dataset_id,),
                dataset_fingerprints=(record.dataset_fingerprint,),
                factor_ids=record.factor_ids,
                factor_revisions=tuple(item.factor.version for item in records),
                known_evidence=("RESEARCH",),
                metadata={"combination": record.combination, "rebalance": record.rebalance},
            )
        )
        return record

    def reveal(
        self, record: PortfolioResearchRecord, stage: ResearchStage
    ) -> PortfolioResearchRecord:
        expected = "VALIDATION" if record.revealed_stage == "RESEARCH" else "HOLDOUT"
        if stage != expected:
            if stage == record.revealed_stage:
                return record
            raise ValueError(f"Cannot reveal {stage} after {record.revealed_stage}")
        request = CreatePortfolioResearch(
            name=record.name,
            factors=record.factor_refs,
            combination=record.combination,
            filters=record.filters,
            construction=record.construction,
            rebalance=record.rebalance,
            gross_notional=record.gross_notional,
            initial_cash=record.initial_cash,
            fee_bps=record.fee_bps,
            slippage_bps=record.slippage_bps,
        )
        records = self._records(request)
        result = self._stage(request, records, stage)
        return record.model_copy(
            update={"revealed_stage": stage, "stages": (*record.stages, result)}
        )
