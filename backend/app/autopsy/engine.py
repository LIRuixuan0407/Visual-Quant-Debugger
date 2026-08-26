from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC

from app.autopsy.models import (
    AutopsySourceRun,
    DrawdownEpisode,
    EquityPoint,
    PeriodAttribution,
    PeriodBreakdown,
    PnLAutopsyReport,
    PnLReconciliation,
    PnLSummary,
    TradeAttribution,
    TradeAttributionReport,
)
from app.trace import BacktestTrace
from app.trace.models import TimelineEvent

RECONCILIATION_TOLERANCE = 1e-8


def _periods(
    events: tuple[TimelineEvent, ...],
    key: Callable[[TimelineEvent], tuple[int, ...]],
    label: Callable[[tuple[int, ...]], str],
) -> tuple[PeriodAttribution, ...]:
    groups: dict[tuple[int, ...], list[TimelineEvent]] = defaultdict(list)
    for event in events:
        groups[key(event)].append(event)

    output: list[PeriodAttribution] = []
    event_index = {event.event_id: index for index, event in enumerate(events)}
    for period_key in sorted(groups):
        group = groups[period_key]
        first = group[0]
        last = group[-1]
        first_index = event_index[first.event_id]
        start_equity = (
            first.pnl_snapshot.equity - first.pnl_snapshot.period_net_pnl
            if first_index == 0
            else events[first_index - 1].pnl_snapshot.equity
        )
        gross = sum(event.pnl_snapshot.period_gross_pnl for event in group)
        fees = sum(event.cost_snapshot.fees for event in group)
        slippage = sum(event.cost_snapshot.slippage for event in group)
        net = sum(event.pnl_snapshot.period_net_pnl for event in group)
        end_equity = last.pnl_snapshot.equity
        output.append(
            PeriodAttribution(
                label=label(period_key),
                start=first.timestamp,
                end=last.timestamp,
                gross_pnl=gross,
                fees=fees,
                slippage=slippage,
                net_pnl=net,
                start_equity=start_equity,
                end_equity=end_equity,
                period_return=end_equity / start_equity - 1.0,
                event_count=len(group),
            )
        )
    return tuple(output)


def _utc_parts(event: TimelineEvent) -> tuple[int, int, int]:
    timestamp = event.timestamp.astimezone(UTC)
    return timestamp.year, timestamp.month, timestamp.day


def _period_breakdown(events: tuple[TimelineEvent, ...]) -> PeriodBreakdown:
    return PeriodBreakdown(
        monthly=_periods(
            events,
            lambda event: _utc_parts(event)[:2],
            lambda value: f"{value[0]:04d}-{value[1]:02d}",
        ),
        quarterly=_periods(
            events,
            lambda event: (_utc_parts(event)[0], (_utc_parts(event)[1] - 1) // 3 + 1),
            lambda value: f"{value[0]:04d} Q{value[1]}",
        ),
        yearly=_periods(
            events,
            lambda event: (_utc_parts(event)[0],),
            lambda value: f"{value[0]:04d}",
        ),
    )


def _sum_events(events: Iterable[TimelineEvent]) -> tuple[float, float, float, float, int]:
    group = tuple(events)
    return (
        sum(event.pnl_snapshot.period_gross_pnl for event in group),
        sum(event.cost_snapshot.fees for event in group),
        sum(event.cost_snapshot.slippage for event in group),
        sum(event.pnl_snapshot.period_net_pnl for event in group),
        len(group),
    )


def _trade_attribution(
    trace: BacktestTrace, initial_equity: float, total_net_pnl: float
) -> TradeAttributionReport:
    indexes = {event.event_id: index for index, event in enumerate(trace.timeline)}
    trades: list[TradeAttribution] = []
    for trade in trace.trades:
        start_index = indexes[trade.entry_event_id]
        end_index = (
            indexes[trade.exit_event_id]
            if trade.exit_event_id is not None
            else len(trace.timeline) - 1
        )
        gross, fees, slippage, net, count = _sum_events(trace.timeline[start_index : end_index + 1])
        start_equity = (
            initial_equity
            if start_index == 0
            else trace.timeline[start_index - 1].pnl_snapshot.equity
        )
        trades.append(
            TradeAttribution(
                trade_id=trade.trade_id,
                direction=trade.direction,
                status=trade.status,
                opened_at=trade.opened_at,
                closed_at=trade.closed_at,
                entry_event_id=trade.entry_event_id,
                exit_event_id=trade.exit_event_id,
                gross_pnl=gross,
                fees=fees,
                slippage=slippage,
                net_pnl=net,
                trade_return=net / start_equity,
                event_count=count,
            )
        )

    closed = tuple(item for item in trades if item.status == "CLOSED")
    opened = tuple(item for item in trades if item.status == "OPEN")
    best = tuple(sorted(closed, key=lambda item: (-item.net_pnl, item.trade_id))[:5])
    worst = tuple(sorted(closed, key=lambda item: (item.net_pnl, item.trade_id))[:5])
    attributed = sum(item.net_pnl for item in trades)
    unattributed = total_net_pnl - attributed
    return TradeAttributionReport(
        method=(
            "Each trade receives Trace timeline P&L and costs from its entry execution event "
            "through its exit execution event, inclusive; open trades continue through the "
            "final event."
        ),
        closed_trades=closed,
        open_trades=opened,
        best_closed=best,
        worst_closed=worst,
        attributed_net_pnl=attributed,
        unattributed_net_pnl=unattributed,
        reconciliation_status=(
            "RECONCILED"
            if abs(unattributed) <= RECONCILIATION_TOLERANCE
            else "UNATTRIBUTED_REMAINS"
        ),
    )


def detect_drawdown_episodes(points: tuple[EquityPoint, ...]) -> tuple[DrawdownEpisode, ...]:
    if not points:
        return ()
    peak_index = 0
    start_index: int | None = None
    trough_index: int | None = None
    raw: list[tuple[int, int, int, int | None]] = []

    for index in range(1, len(points)):
        equity = points[index].equity
        peak_equity = points[peak_index].equity
        if start_index is None:
            if equity >= peak_equity:
                peak_index = index
            else:
                start_index = index
                trough_index = index
        else:
            if trough_index is None:
                raise RuntimeError("An active drawdown must have a trough")
            if equity < points[trough_index].equity:
                trough_index = index
            if equity >= peak_equity:
                raw.append((peak_index, start_index, trough_index, index))
                peak_index = index
                start_index = None
                trough_index = None

    if start_index is not None and trough_index is not None:
        raw.append((peak_index, start_index, trough_index, None))

    depths = [points[trough].equity / points[peak].equity - 1.0 for peak, _, trough, _ in raw]
    ranked = {
        raw_index: rank
        for rank, raw_index in enumerate(
            sorted(range(len(raw)), key=lambda index: (depths[index], index)), start=1
        )
    }
    episodes: list[DrawdownEpisode] = []
    for index, (peak, start, trough, recovery) in enumerate(raw):
        episodes.append(
            DrawdownEpisode(
                episode_id=f"drawdown-{index + 1:04d}",
                rank_by_depth=ranked[index],
                peak_event_id=points[peak].event_id,
                drawdown_start_event_id=points[start].event_id,
                trough_event_id=points[trough].event_id,
                recovery_event_id=None if recovery is None else points[recovery].event_id,
                peak_time=points[peak].timestamp,
                drawdown_start_time=points[start].timestamp,
                trough_time=points[trough].timestamp,
                recovery_time=None if recovery is None else points[recovery].timestamp,
                peak_equity=points[peak].equity,
                trough_equity=points[trough].equity,
                max_drawdown=depths[index],
                duration_bars=(len(points) - 1 if recovery is None else recovery) - peak,
                recovery_bars=None if recovery is None else recovery - trough,
                recovered=recovery is not None,
            )
        )
    return tuple(episodes)


def build_pnl_autopsy(trace_id: str, trace: BacktestTrace) -> PnLAutopsyReport:
    if not trace.timeline:
        raise ValueError("P&L Autopsy requires at least one Trace timeline event")
    first = trace.timeline[0]
    last = trace.timeline[-1]
    initial_equity = first.pnl_snapshot.equity - first.pnl_snapshot.period_net_pnl
    gross, fees, slippage, net, _ = _sum_events(trace.timeline)
    final_equity = last.pnl_snapshot.equity
    summary = PnLSummary(
        initial_equity=initial_equity,
        gross_pnl=gross,
        fees=fees,
        slippage=slippage,
        total_cost=fees + slippage,
        net_pnl=net,
        final_equity=final_equity,
    )
    gross_less_costs = gross - fees - slippage
    initial_plus_net = initial_equity + net
    pnl_difference = gross_less_costs - net
    equity_difference = initial_plus_net - final_equity
    reconciliation = PnLReconciliation(
        gross_less_costs=gross_less_costs,
        reported_net_pnl=net,
        pnl_difference=pnl_difference,
        initial_plus_net=initial_plus_net,
        reported_final_equity=final_equity,
        equity_difference=equity_difference,
        reconciled=(
            abs(pnl_difference) <= RECONCILIATION_TOLERANCE
            and abs(equity_difference) <= RECONCILIATION_TOLERANCE
        ),
    )
    equity_points = tuple(
        EquityPoint(
            event_id=event.event_id,
            timestamp=event.timestamp,
            equity=event.pnl_snapshot.equity,
        )
        for event in trace.timeline
    )
    return PnLAutopsyReport(
        source_run=AutopsySourceRun(
            trace_id=trace_id,
            trace_version=trace.trace_version,
            strategy_id=trace.strategy.strategy_id,
            dataset_id=trace.metadata.dataset_id,
            dataset_name=trace.metadata.dataset_name,
            bar_count=trace.metadata.bar_count,
        ),
        summary=summary,
        reconciliation=reconciliation,
        periods=_period_breakdown(trace.timeline),
        trades=_trade_attribution(trace, initial_equity, net),
        drawdowns=detect_drawdown_episodes(equity_points),
    )
