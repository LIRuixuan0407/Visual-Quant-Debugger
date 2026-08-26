from datetime import UTC, datetime

import pytest

from app.execution import ExecutionEngine
from app.portfolio import Portfolio


def test_portfolio_cash_holdings_and_equity_reconcile() -> None:
    now = datetime(2024, 1, 2, tzinfo=UTC)
    engine = ExecutionEngine(fee_bps=5, slippage_bps=5)
    orders = engine.create_orders(
        current_a=0,
        current_b=0,
        desired_a=10,
        desired_b=-20,
        submitted_at=now,
        target_position=1,
        source_signal_id="signal-0001",
    )
    fills = engine.execute(orders, price_a=100, price_b=50, executed_at=now)
    portfolio = Portfolio(100_000)
    portfolio.apply(fills)
    snapshot = portfolio.mark(100, 50)
    assert snapshot.quantity_a == pytest.approx(10)
    assert snapshot.quantity_b == pytest.approx(-20)
    assert snapshot.equity == pytest.approx(99_998)
    assert snapshot.cumulative_fees == pytest.approx(1)
    assert snapshot.cumulative_slippage == pytest.approx(1)
