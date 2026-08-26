from pathlib import Path

from app.backtest import BacktestParameters, run_backtest
from app.data import load_pair_csv
from app.strategies import PairsTradingParameters


def main() -> None:
    project_root = Path(__file__).parents[2]
    bars = load_pair_csv(project_root / "sample_data" / "pairs_daily.csv")
    result = run_backtest(
        bars,
        BacktestParameters(strategy=PairsTradingParameters(lookback=5, entry_z=1.0, exit_z=0.8)),
    )
    metrics = result.metrics
    print("Visual Quant Debugger — deterministic sample")
    print(f"Bars:           {len(result.timeline)}")
    print(f"Orders:         {metrics.number_of_orders}")
    print(f"Gross P&L:      ${metrics.gross_pnl:,.2f}")
    print(f"Fees:           ${metrics.total_fees:,.2f}")
    print(f"Slippage:       ${metrics.total_slippage:,.2f}")
    print(f"Net P&L:        ${metrics.net_pnl:,.2f}")
    print(f"Max drawdown:   {metrics.max_drawdown:.2%}")


if __name__ == "__main__":
    main()
