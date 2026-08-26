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
