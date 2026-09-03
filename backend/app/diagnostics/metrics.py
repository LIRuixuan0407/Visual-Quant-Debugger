import numpy as np

from app.diagnostics.annualization import sharpe_ratio
from app.models import BacktestMetrics, TimelineRow


def daily_returns(equity: tuple[float, ...], initial_cash: float) -> np.ndarray:
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if not equity:
        return np.asarray([], dtype=np.float64)
    values = np.asarray((initial_cash, *equity), dtype=np.float64)
    return values[1:] / values[:-1] - 1.0


def sharpe(returns: np.ndarray, *, dataset_frequency: str | None = "1D") -> float:
    return sharpe_ratio(returns, dataset_frequency=dataset_frequency)


def max_drawdown(equity: tuple[float, ...], initial_cash: float) -> float:
    values = np.asarray((initial_cash, *equity), dtype=np.float64)
    running_max = np.maximum.accumulate(values)
    drawdowns = values / running_max - 1.0
    return float(np.min(drawdowns))


def calculate_metrics(
    timeline: tuple[TimelineRow, ...],
    initial_cash: float,
    *,
    dataset_frequency: str | None = "1D",
) -> BacktestMetrics:
    equity = tuple(row.portfolio.equity for row in timeline)
    final = timeline[-1].portfolio
    net_pnl = final.equity - initial_cash
    gross_pnl = net_pnl + final.cumulative_fees + final.cumulative_slippage
    traded_notional = sum(
        execution.traded_notional for row in timeline for execution in row.executions
    )
    average_equity = float(np.mean(np.asarray(equity, dtype=np.float64)))
    turnover = traded_notional / average_equity if average_equity > 0 else 0.0
    return BacktestMetrics(
        total_return=net_pnl / initial_cash,
        sharpe=sharpe(daily_returns(equity, initial_cash), dataset_frequency=dataset_frequency),
        max_drawdown=max_drawdown(equity, initial_cash),
        turnover=turnover,
        net_pnl=net_pnl,
        gross_pnl=gross_pnl,
        total_fees=final.cumulative_fees,
        total_slippage=final.cumulative_slippage,
        number_of_orders=sum(len(row.orders) for row in timeline),
    )
