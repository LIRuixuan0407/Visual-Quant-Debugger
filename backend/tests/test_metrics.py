import numpy as np
import pytest

from app.diagnostics import daily_returns, max_drawdown, sharpe


def test_daily_returns_sharpe_and_drawdown() -> None:
    returns = daily_returns((100.0, 110.0, 99.0), initial_cash=100.0)
    assert returns.tolist() == pytest.approx([0.0, 0.1, -0.1])
    assert sharpe(np.asarray([0.01, 0.02, 0.03])) > 0
    assert max_drawdown((100.0, 110.0, 99.0), 100.0) == pytest.approx(-0.1)


def test_zero_volatility_sharpe_is_zero() -> None:
    assert sharpe(np.asarray([0.0, 0.0, 0.0])) == 0.0
