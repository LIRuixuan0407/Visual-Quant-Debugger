from datetime import UTC, datetime

import pytest

from app.execution import ExecutionEngine


def test_fees_slippage_and_directional_fill() -> None:
    now = datetime(2024, 1, 2, tzinfo=UTC)
    engine = ExecutionEngine(fee_bps=5, slippage_bps=10)
    orders = engine.create_orders(
        current_a=0,
        current_b=0,
        desired_a=10,
        desired_b=-5,
        submitted_at=now,
        target_position=1,
        source_signal_id="signal-0001",
    )
    executions = engine.execute(orders, price_a=100, price_b=50, executed_at=now)
    buy, sell = executions
    assert buy.fill_price == pytest.approx(100.1)
    assert sell.fill_price == pytest.approx(49.95)
    assert buy.fee == pytest.approx(0.5)
    assert buy.slippage == pytest.approx(1.0)
    assert sell.source_order_id == orders[1].order_id


def test_spread_and_volume_impact_are_directional_and_explicit() -> None:
    now = datetime(2024, 1, 2, tzinfo=UTC)
    engine = ExecutionEngine(
        fee_bps=0,
        slippage_bps=0,
        spread_bps=20,
        market_impact_bps=100,
    )
    orders = engine.create_target_orders(
        current_positions={"AAPL": 0.0},
        target_positions={"AAPL": 100.0},
        submitted_at=now,
        source_signal_id="impact-1",
        target_state=1,
    )
    fills = engine.execute_at_prices(
        orders,
        prices={"AAPL": 100.0},
        volumes={"AAPL": 10_000.0},
        executed_at=now,
    )
    fill = fills[0]
    # 20 bps quoted spread -> 10 bps one-way; 1% participation -> 10 bps sqrt impact.
    assert fill.spread_cost == pytest.approx(10.0)
    assert fill.market_impact == pytest.approx(10.0)
    assert fill.slippage == pytest.approx(20.0)
    assert fill.fill_price == pytest.approx(100.2)
