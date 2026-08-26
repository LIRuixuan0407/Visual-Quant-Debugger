from datetime import UTC, datetime, timedelta

import pytest

from app.backtest import BacktestParameters, run_backtest
from app.models import MarketBar
from app.strategies import PairsTradingParameters


def make_bars() -> tuple[MarketBar, ...]:
    prices_a = [100, 102, 104, 112, 108, 110, 112, 106, 116, 118]
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(MarketBar(start + timedelta(days=i), a, 50 + i) for i, a in enumerate(prices_a))


def test_backtest_is_deterministic_and_executes_one_bar_later() -> None:
    params = BacktestParameters(
        strategy=PairsTradingParameters(lookback=2, entry_z=0.5, exit_z=0.2),
        gross_target=10_000,
    )
    first = run_backtest(make_bars(), params)
    second = run_backtest(make_bars(), params)
    assert first == second

    transition_index = next(
        index for index, row in enumerate(first.timeline) if row.decision.signal_id is not None
    )
    assert first.timeline[transition_index].executions == ()
    assert first.timeline[transition_index + 1].executions
    assert all(
        execution.executed_at == first.timeline[transition_index + 1].timestamp
        for execution in first.timeline[transition_index + 1].executions
    )


def test_costs_reduce_net_pnl_by_exact_amount() -> None:
    strategy = PairsTradingParameters(lookback=2, entry_z=0.5, exit_z=0.2)
    no_cost = run_backtest(
        make_bars(),
        BacktestParameters(strategy=strategy, fee_bps=0, slippage_bps=0),
    )
    with_cost = run_backtest(
        make_bars(),
        BacktestParameters(strategy=strategy, fee_bps=5, slippage_bps=5),
    )
    cost = with_cost.metrics.total_fees + with_cost.metrics.total_slippage
    assert with_cost.metrics.net_pnl == pytest.approx(no_cost.metrics.net_pnl - cost)
    assert with_cost.metrics.gross_pnl == pytest.approx(no_cost.metrics.net_pnl)


def test_order_and_execution_lineage_is_complete() -> None:
    result = run_backtest(
        make_bars(),
        BacktestParameters(strategy=PairsTradingParameters(lookback=2, entry_z=0.5, exit_z=0.2)),
    )
    for row in result.timeline:
        order_ids = {order.order_id for order in row.orders}
        assert all(order.source_signal_id.startswith("signal-") for order in row.orders)
        assert all(execution.source_order_id in order_ids for execution in row.executions)


@pytest.mark.parametrize("additional_delay", [0, 1, 2])
def test_additional_execution_delay_reruns_orders_and_leaves_end_signals_unfilled(
    additional_delay: int,
) -> None:
    result = run_backtest(
        make_bars(),
        BacktestParameters(
            strategy=PairsTradingParameters(lookback=2, entry_z=0.5, exit_z=0.2),
            additional_execution_delay_bars=additional_delay,
        ),
    )
    signal_index = {
        row.decision.signal_id: index
        for index, row in enumerate(result.timeline)
        if row.decision.signal_id is not None
    }
    for execution_index, row in enumerate(result.timeline):
        for order in row.orders:
            assert execution_index - signal_index[order.source_signal_id] == 1 + additional_delay

    expected_unfilled = sum(
        index + 1 + additional_delay >= len(result.timeline)
        for index, row in enumerate(result.timeline)
        if row.decision.signal_id is not None
    )
    assert result.unfilled_signal_count == expected_unfilled
    assert sum(len(row.orders) > 0 for row in result.timeline) + expected_unfilled == len(
        signal_index
    )
